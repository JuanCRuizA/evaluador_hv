"""Traduccion de errores del agente, cierre del cliente, timeouts y esquemas. SIN red ni API."""
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.extensions import db
from app.models import Candidato, Evaluacion
from app.models.schemas import CriterioEvaluado, EvaluacionHV
from app.services import agente_evaluador as ag
from tests.helpers import cabeceras
from tests.test_agente import PERFIL, TEXTO_CV, DobleEjecutar, _error_api, _resultado
from tests.test_evaluacion import crear

# El conftest sustituye ag._ejecutar por un guard en cada test; se guarda el real al importar.
EJECUTAR_REAL = ag._ejecutar

SECRETO = "SECRETO"

MSG_NO_EXISTE = ("El modelo de IA configurado no existe o ya no está disponible "
                 "(revise OPENROUTER_MODEL)")
MSG_400 = "La IA rechazó la petición (400); revise la configuración del modelo"
MSG_TURNOS = "La IA superó el número máximo de turnos (MAX_TURNOS_AGENTE); intente de nuevo"
MSG_INTERPRETAR = "La IA devolvió una respuesta que no se pudo interpretar; intente de nuevo"
MSG_CONEXION = "No se pudo conectar con la IA; revise la conexión"
MSG_5XX = "El servicio de IA no está disponible en este momento; intente más tarde"


@pytest.fixture
def modelo(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", "modelo-falso")


def _req():
    import httpx2 as httpx
    return httpx.Request("POST", "https://api.invalid/v1/chat/completions")


def _status(estado, clase=None):
    from openai import APIStatusError
    return _error_api(clase or APIStatusError, estado, f"cuerpo con {SECRETO}")


def _validation_error():
    try:
        EvaluacionHV.model_validate({"criterios": [{"cumple": "no-bool"}], "resumen": SECRETO})
    except ValidationError as e:
        return e
    raise AssertionError("debia fallar")


def _json_error():
    try:
        json.loads("{" + SECRETO)
    except json.JSONDecodeError as e:
        return e


def _casos_traduccion():
    from agents.exceptions import (AgentsException, MaxTurnsExceeded, ModelBehaviorError,
                                   UserError)
    from openai import APIConnectionError, NotFoundError, BadRequestError
    return [
        ("404", lambda: _status(404, NotFoundError), MSG_NO_EXISTE),
        ("400_no_reintentable", lambda: _status(400, BadRequestError), MSG_400),
        ("max_turns", lambda: MaxTurnsExceeded(f"turnos {SECRETO}"), MSG_TURNOS),
        ("agents_exception", lambda: AgentsException(SECRETO), MSG_INTERPRETAR),
        ("model_behavior", lambda: ModelBehaviorError(SECRETO), MSG_INTERPRETAR),
        ("user_error", lambda: UserError(SECRETO), MSG_INTERPRETAR),
        ("pydantic", _validation_error, MSG_INTERPRETAR),
        ("json", _json_error, MSG_INTERPRETAR),
        ("conexion", lambda: APIConnectionError(message=SECRETO, request=_req()), MSG_CONEXION),
        ("500", lambda: _status(500), MSG_5XX),
        ("503", lambda: _status(503), MSG_5XX),
    ]


@pytest.mark.parametrize("nombre", [c[0] for c in _casos_traduccion()])
def test_traduccion_a_mensaje_propio(modelo, monkeypatch, caplog, nombre):
    _, fabrica, esperado = next(c for c in _casos_traduccion() if c[0] == nombre)
    d = DobleEjecutar(fabrica())
    monkeypatch.setattr(ag, "_ejecutar", d)
    with caplog.at_level(logging.DEBUG), pytest.raises(ag.ErrorAgente) as e:
        ag.evaluar(PERFIL, TEXTO_CV)
    assert e.value.mensaje == esperado
    assert type(e.value) is ag.ErrorAgente
    assert SECRETO not in e.value.mensaje and SECRETO not in str(e.value)
    assert SECRETO not in caplog.text
    assert len(d.llamadas) == 1
    assert e.value.__cause__ is None  # from None: no encadena la causa con el cuerpo


def test_modeltimeout_y_tooltimeout_a_tiempo_agotado(modelo, monkeypatch, caplog):
    from agents.exceptions import ModelTimeoutError, ToolTimeoutError
    for err in (ModelTimeoutError(30.0), ToolTimeoutError(SECRETO, 30.0)):
        monkeypatch.setattr(ag, "_ejecutar", DobleEjecutar(err))
        with caplog.at_level(logging.DEBUG), pytest.raises(ag.ErrorTiempoAgotadoIA):
            ag.evaluar(PERFIL, TEXTO_CV)
    assert SECRETO not in caplog.text


def test_400_reintento_que_vuelve_a_fallar_da_mensaje_400(modelo, monkeypatch):
    from openai import BadRequestError
    d = DobleEjecutar(_status(400, BadRequestError).__class__(
        "bad temperature", response=_status(400).response, body=None),
        _status(400, BadRequestError))
    monkeypatch.setattr(ag, "_ejecutar", d)
    with pytest.raises(ag.ErrorAgente) as e:
        ag.evaluar(PERFIL, TEXTO_CV)
    assert e.value.mensaje == MSG_400 and len(d.llamadas) == 2


@pytest.mark.parametrize("estado,indice", [(401, 0), (403, 1), (429, 2)])
def test_401_403_429_sin_cambios(modelo, monkeypatch, estado, indice):
    from openai import AuthenticationError, PermissionDeniedError, RateLimitError
    clase = (AuthenticationError, PermissionDeniedError, RateLimitError)[indice]
    monkeypatch.setattr(ag, "_ejecutar", DobleEjecutar(_status(estado, clase)))
    with pytest.raises(ag.ErrorAgente) as e:
        ag.evaluar(PERFIL, TEXTO_CV)
    esperado = (ag.ErrorAgente, ag.ErrorAgente, ag.ErrorAgente)[indice]
    assert isinstance(e.value, esperado)
    if estado == 429:
        assert e.value.mensaje == "La IA está limitando las peticiones; intente de nuevo en un minuto"
    else:
        assert e.value.mensaje == "La clave de la IA no es válida o no tiene permiso"


def test_422_se_relanza_tal_cual(modelo, monkeypatch, caplog):
    from openai import APIStatusError, UnprocessableEntityError
    err = _status(422, UnprocessableEntityError)
    monkeypatch.setattr(ag, "_ejecutar", DobleEjecutar(err))
    with caplog.at_level(logging.DEBUG), pytest.raises(APIStatusError) as e:
        ag.evaluar(PERFIL, TEXTO_CV)
    assert e.value is err
    assert "status=422" in caplog.text and SECRETO not in caplog.text


def test_excepcion_cualquiera_se_relanza_tal_cual(modelo, monkeypatch):
    err = KeyError("x")
    monkeypatch.setattr(ag, "_ejecutar", DobleEjecutar(err))
    with pytest.raises(KeyError) as e:
        ag.evaluar(PERFIL, TEXTO_CV)
    assert e.value is err


# ---- log de fallo ----
@pytest.mark.parametrize("estado", [404, 500, 429, 422])
def test_log_de_fallo_con_status_y_sin_cuerpo(modelo, monkeypatch, caplog, estado):
    monkeypatch.setattr(ag, "_ejecutar", DobleEjecutar(_status(estado)))
    with caplog.at_level(logging.DEBUG), pytest.raises(Exception):
        ag.evaluar(PERFIL, TEXTO_CV)
    assert f"status={estado}" in caplog.text
    assert "APIStatusError" in caplog.text or "Error" in caplog.text
    assert SECRETO not in caplog.text


def test_log_de_fallo_sin_status_si_no_es_api(modelo, monkeypatch, caplog):
    monkeypatch.setattr(ag, "_ejecutar", DobleEjecutar(asyncio.TimeoutError()))
    with caplog.at_level(logging.DEBUG), pytest.raises(ag.ErrorTiempoAgotadoIA):
        ag.evaluar(PERFIL, TEXTO_CV)
    assert "TimeoutError" in caplog.text and "status=" not in caplog.text


def test_log_de_evaluar_incluye_turnos(modelo, monkeypatch, caplog):
    r = _resultado()
    r.raw_responses = [object(), object(), object()]
    monkeypatch.setattr(ag, "_ejecutar", DobleEjecutar(r))
    with caplog.at_level(logging.INFO):
        ag.evaluar(PERFIL, TEXTO_CV)
    assert "turnos=3" in caplog.text


def test_log_de_evaluar_turnos_desconocidos(modelo, monkeypatch, caplog):
    monkeypatch.setattr(ag, "_ejecutar", DobleEjecutar(_resultado()))
    with caplog.at_level(logging.INFO):
        ag.evaluar(PERFIL, TEXTO_CV)
    assert "turnos=" in caplog.text


# ---- timeout ----
@pytest.mark.parametrize("valor", ["240", "300", "240.0", "1000"])
def test_timeout_mayor_o_igual_a_240_es_error_configuracion(monkeypatch, valor):
    monkeypatch.setenv("TIMEOUT_AGENTE_S", valor)
    with pytest.raises(ag.ErrorConfiguracionIA) as e:
        ag._timeout()
    assert "TIMEOUT_AGENTE_S" in e.value.mensaje


def test_timeout_239_vale(monkeypatch):
    monkeypatch.setenv("TIMEOUT_AGENTE_S", "239")
    assert ag._timeout() == 239.0


@pytest.mark.parametrize("valor", [None, "", "   "])
def test_timeout_ausente_o_vacio_150(monkeypatch, valor):
    if valor is None:
        monkeypatch.delenv("TIMEOUT_AGENTE_S", raising=False)
    else:
        monkeypatch.setenv("TIMEOUT_AGENTE_S", valor)
    assert ag._timeout() == 150.0
    assert ag.TIMEOUT_DEFECTO_S == 150.0 and ag.TIMEOUT_MAX_S == 240.0


def test_evaluar_con_timeout_300_falla_antes_de_llamar(modelo, monkeypatch):
    d = DobleEjecutar(_resultado())
    monkeypatch.setattr(ag, "_ejecutar", d)
    monkeypatch.setenv("TIMEOUT_AGENTE_S", "300")
    with pytest.raises(ag.ErrorConfiguracionIA):
        ag.evaluar(PERFIL, TEXTO_CV)
    assert d.llamadas == []


def test_env_example_documenta_timeout_150():
    ruta = Path(__file__).resolve().parent.parent / ".env.example"
    assert "TIMEOUT_AGENTE_S=150" in ruta.read_text(encoding="utf-8").splitlines()


# ---- cliente sin reintentos ocultos ----
def test_crear_modelo_max_retries_cero(modelo):
    assert ag._crear_modelo()._client.max_retries == 0


def test_crear_modelo_pasa_max_retries_cero_a_asyncopenai(modelo, monkeypatch):
    capturado = {}
    real = ag.AsyncOpenAI

    def espia(**kw):
        capturado.update(kw)
        return real(**kw)

    monkeypatch.setattr(ag, "AsyncOpenAI", espia)
    ag._crear_modelo()
    assert capturado["max_retries"] == 0


# ---- cierre del cliente en _ejecutar ----
class Cliente:
    def __init__(self, falla=False):
        self.cerrado = 0
        self.falla = falla

    async def close(self):
        self.cerrado += 1
        if self.falla:
            raise RuntimeError(f"cierre roto {SECRETO}")


def _agente(cliente):
    return SimpleNamespace(model=SimpleNamespace(_client=cliente))


def _ejecutar(agente, timeout=5):
    return EJECUTAR_REAL(agente, "msg", ag.Contexto(PERFIL, TEXTO_CV), 3, timeout)


def _runner(monkeypatch, comportamiento):
    async def run(*a, **k):
        return await comportamiento()
    monkeypatch.setattr(ag.Runner, "run", run)


def test_cierre_en_camino_feliz(monkeypatch):
    async def ok():
        return "RES"
    _runner(monkeypatch, ok)
    c = Cliente()
    assert _ejecutar(_agente(c)) == "RES"
    assert c.cerrado == 1


def test_cierre_ante_excepcion(monkeypatch):
    async def mal():
        raise ValueError("original")
    _runner(monkeypatch, mal)
    c = Cliente()
    with pytest.raises(ValueError, match="original"):
        _ejecutar(_agente(c))
    assert c.cerrado == 1


def test_cierre_ante_timeout(monkeypatch):
    async def lento():
        await asyncio.sleep(5)
    _runner(monkeypatch, lento)
    c = Cliente()
    with pytest.raises(asyncio.TimeoutError):
        _ejecutar(_agente(c), timeout=0.05)
    assert c.cerrado == 1


def test_fallo_al_cerrar_no_tapa_el_error_original(monkeypatch, caplog):
    async def mal():
        raise ValueError("original")
    _runner(monkeypatch, mal)
    c = Cliente(falla=True)
    with caplog.at_level(logging.DEBUG), pytest.raises(ValueError, match="original"):
        _ejecutar(_agente(c))
    assert c.cerrado == 1
    assert "RuntimeError" in caplog.text and SECRETO not in caplog.text


def test_fallo_al_cerrar_en_camino_feliz_solo_se_registra(monkeypatch, caplog):
    async def ok():
        return "RES"
    _runner(monkeypatch, ok)
    with caplog.at_level(logging.DEBUG):
        assert _ejecutar(_agente(Cliente(falla=True))) == "RES"
    assert "RuntimeError" in caplog.text and SECRETO not in caplog.text


@pytest.mark.parametrize("agente", [
    None, SimpleNamespace(), SimpleNamespace(model=None),
    SimpleNamespace(model=SimpleNamespace()),
    SimpleNamespace(model=SimpleNamespace(_client=None)),
    SimpleNamespace(model=SimpleNamespace(_client=SimpleNamespace())),
])
def test_cierre_tolerante_si_falta_modelo_o_cliente(monkeypatch, agente):
    async def ok():
        return "RES"
    _runner(monkeypatch, ok)
    assert _ejecutar(agente) == "RES"


# ---- esquemas ----
def test_evaluacion_valores_por_defecto():
    ev = EvaluacionHV(criterios=[{"codigo": "O1", "cumple": True}])
    assert ev.alertas_seguridad == [] and ev.resumen == ""
    assert ev.criterios[0].evidencia == ""


def test_defaults_no_se_comparten_entre_instancias():
    a = EvaluacionHV(criterios=[])
    b = EvaluacionHV(criterios=[])
    a.alertas_seguridad.append("x")
    assert b.alertas_seguridad == []


def test_criterio_evidencia_por_defecto():
    assert CriterioEvaluado(codigo="O1", cumple=False).evidencia == ""


def test_respuesta_json_incompleta_valida():
    ev = EvaluacionHV.model_validate_json('{"criterios": [{"codigo": "O1", "cumple": false}]}')
    assert ev.alertas_seguridad == [] and ev.resumen == "" and ev.criterios[0].evidencia == ""


def test_criterios_sigue_siendo_obligatorio():
    with pytest.raises(ValidationError):
        EvaluacionHV.model_validate_json('{"resumen": "x"}')


# ---- mensajes sin la palabra OpenAI ----
def test_mensajes_de_usuario_sin_openai():
    for m in (MSG_NO_EXISTE, MSG_400, MSG_TURNOS, MSG_INTERPRETAR, MSG_CONEXION, MSG_5XX):
        assert "openai" not in m.lower()
        assert "gemini" not in m.lower()


@pytest.mark.parametrize("nombre", [c[0] for c in _casos_traduccion()])
def test_mensajes_reales_sin_openai(modelo, monkeypatch, nombre):
    _, fabrica, _ = next(c for c in _casos_traduccion() if c[0] == nombre)
    monkeypatch.setattr(ag, "_ejecutar", DobleEjecutar(fabrica()))
    with pytest.raises(ag.ErrorAgente) as e:
        ag.evaluar(PERFIL, TEXTO_CV)
    assert "openai" not in e.value.mensaje.lower()


# ---- end-to-end a nivel de ruta ----
MENSAJES_RUTA = [
    MSG_NO_EXISTE, MSG_400, MSG_TURNOS, MSG_INTERPRETAR, MSG_CONEXION, MSG_5XX,
    "La clave de la IA no es válida o no tiene permiso",
    "La IA está limitando las peticiones; intente de nuevo en un minuto",
]


@pytest.mark.parametrize("clase,mensaje", (
    [(ag.ErrorAgente, m) for m in MENSAJES_RUTA]
    + [(ag.ErrorTiempoAgotadoIA, "La evaluación con IA tardó demasiado y se canceló. Intente de nuevo."),
       (ag.ErrorConfiguracionIA, "Falta configurar el modelo de IA (variable OPENROUTER_MODEL). Avise al administrador.")]
))
def test_ruta_evaluar_502_con_el_mensaje_exacto(app, client_autenticado, monkeypatch, clase, mensaje):
    llamadas = []

    def doble(perfil, texto):
        llamadas.append(1)
        raise clase(mensaje)

    monkeypatch.setattr(ag, "evaluar", doble)
    cid = crear(app)
    r = client_autenticado.post(f"/evaluar/{cid}", headers=cabeceras(client_autenticado))
    assert r.status_code == 502
    j = r.get_json()
    assert j["ok"] is False and j["error"] == mensaje
    assert llamadas == [1]
    with app.app_context():
        assert db.session.get(Candidato, cid).estado == "pendiente"
        assert Evaluacion.query.count() == 0
