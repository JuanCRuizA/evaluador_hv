"""Migracion a OpenRouter: 402, OPENROUTER_MAX_TOKENS, reintentos, cabeceras, base URL y
.env.example. SIN red ni API: _ejecutar siempre es un doble y AsyncOpenAI solo se construye."""
import logging
import re
from pathlib import Path

import pytest

from app.services import agente_evaluador as ag
from tests.test_agente import (PERFIL, TEXTO_CV, DobleEjecutar, _bad_request, _error_api,
                               _resultado)

SENTINELA = "SENTINELA-cuerpo-402-7c1d"
MSG_402 = "No hay crédito suficiente en OpenRouter (402); recargue saldo en openrouter.ai"
RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture
def modelo(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", "modelo-falso")


def _lineas_activas():
    lineas = (RAIZ / ".env.example").read_text(encoding="utf-8").splitlines()
    return [l for l in lineas if l.strip() and not l.lstrip().startswith("#")]


# ---- 402 ----
def _err_402():
    from openai import APIStatusError
    return _error_api(APIStatusError, 402, f"insufficient credits {SENTINELA}")


def test_402_mensaje_exacto_sin_filtrar_cuerpo(modelo, monkeypatch, caplog):
    d = DobleEjecutar(_err_402())
    monkeypatch.setattr(ag, "_ejecutar", d)
    with caplog.at_level(logging.DEBUG), pytest.raises(ag.ErrorAgente) as e:
        ag.evaluar(PERFIL, TEXTO_CV)
    assert e.value.mensaje == MSG_402
    assert type(e.value) is ag.ErrorAgente
    assert SENTINELA not in e.value.mensaje and SENTINELA not in str(e.value)
    assert SENTINELA not in caplog.text
    assert "status=402" in caplog.text
    assert e.value.__cause__ is None
    assert len(d.llamadas) == 1  # sin reintento


def test_402_en_la_ruta_da_502_con_el_mensaje(app, client_autenticado, monkeypatch):
    from app.extensions import db
    from app.models import Candidato
    from tests.helpers import cabeceras
    from tests.test_evaluacion import crear

    monkeypatch.setattr(ag, "_ejecutar", DobleEjecutar(_err_402()))
    cid = crear(app)
    r = client_autenticado.post(f"/evaluar/{cid}", headers=cabeceras(client_autenticado))
    assert r.status_code == 502
    assert r.get_json()["error"] == MSG_402
    assert SENTINELA not in r.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(Candidato, cid).estado == "pendiente"


def test_404_pide_revisar_openrouter_model(modelo, monkeypatch):
    from openai import NotFoundError
    monkeypatch.setattr(ag, "_ejecutar",
                        DobleEjecutar(_error_api(NotFoundError, 404, "no model")))
    with pytest.raises(ag.ErrorAgente) as e:
        ag.evaluar(PERFIL, TEXTO_CV)
    assert "OPENROUTER_MODEL" in e.value.mensaje


# ---- OPENROUTER_MAX_TOKENS ----
def test_max_tokens_defecto_4000(modelo, monkeypatch):
    monkeypatch.delenv("OPENROUTER_MAX_TOKENS", raising=False)
    assert ag.MAX_TOKENS_DEFECTO == 4000
    assert ag._max_tokens() == 4000
    assert ag.construir_agente().model_settings.max_tokens == 4000


@pytest.mark.parametrize("valor", ["", "   "])
def test_max_tokens_vacio_usa_defecto(modelo, monkeypatch, valor):
    monkeypatch.setenv("OPENROUTER_MAX_TOKENS", valor)
    assert ag._max_tokens() == ag.MAX_TOKENS_DEFECTO


def test_max_tokens_valido_llega_a_model_settings(modelo, monkeypatch):
    monkeypatch.setenv("OPENROUTER_MAX_TOKENS", "1234")
    assert ag._max_tokens() == 1234
    a = ag.construir_agente()
    assert a.model_settings.max_tokens == 1234
    assert a.model_settings.temperature == 0


@pytest.mark.parametrize("valor", ["abc", "0", "-5", "1.5"])
def test_max_tokens_invalido(modelo, monkeypatch, caplog, valor):
    monkeypatch.setenv("OPENROUTER_MAX_TOKENS", valor)
    llamadas = DobleEjecutar(_resultado())
    monkeypatch.setattr(ag, "_ejecutar", llamadas)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ag.ErrorConfiguracionIA) as e:
            ag.evaluar(PERFIL, TEXTO_CV)
        with pytest.raises(ag.ErrorConfiguracionIA) as e2:
            ag.construir_agente()
    for err in (e, e2):
        assert "OPENROUTER_MAX_TOKENS" in err.value.mensaje
        # no revela el valor: el mensaje no lleva digitos ni el texto configurado
        assert valor not in err.value.mensaje.replace("OPENROUTER_MAX_TOKENS", "")
        assert not re.search(r"\d", err.value.mensaje)
    assert llamadas.llamadas == []  # falla antes de llamar a la IA


def test_sin_parametros_max_tokens_lo_deja_en_none(modelo):
    a = ag.construir_agente(("max_tokens",))
    assert a.model_settings.max_tokens is None
    assert a.model_settings.temperature == 0


def test_parametros_reintentables():
    assert set(ag.PARAMETROS_REINTENTABLES) == {"temperature", "max_tokens", "reasoning"}


def test_sin_parametros_max_tokens_no_valida_la_variable(modelo, monkeypatch):
    """Si se omite max_tokens, un valor invalido de la variable no se evalua en el agente."""
    monkeypatch.setenv("OPENROUTER_MAX_TOKENS", "abc")
    assert ag.construir_agente(("max_tokens",)).model_settings.max_tokens is None


@pytest.mark.parametrize("texto_error", [
    "Unsupported parameter: 'max_tokens' is not supported with this model",
    "Unsupported parameter: 'max_completion_tokens' is not supported",
    "Use 'max_completion_tokens' instead of 'max_tokens'",
])
def test_reintento_sin_max_tokens(modelo, monkeypatch, texto_error):
    d = DobleEjecutar(_bad_request(texto_error), _resultado())
    monkeypatch.setattr(ag, "_ejecutar", d)
    ag.evaluar(PERFIL, TEXTO_CV)
    assert len(d.llamadas) == 2
    ms0, ms1 = (l.agente.model_settings for l in d.llamadas)
    assert ms0.max_tokens == 4000 and ms0.temperature == 0
    assert ms1.max_tokens is None and ms1.temperature == 0


def test_reintento_max_completion_tokens_solo(modelo, monkeypatch):
    """Un 400 que solo menciona max_completion_tokens cuenta como max_tokens."""
    d = DobleEjecutar(_bad_request("'max_completion_tokens' no soportado"), _resultado())
    monkeypatch.setattr(ag, "_ejecutar", d)
    ag.evaluar(PERFIL, TEXTO_CV)
    assert len(d.llamadas) == 2
    assert d.llamadas[1].agente.model_settings.max_tokens is None


@pytest.mark.parametrize("texto_error", [
    "bad 'max_tokens' " + SENTINELA, "bad 'max_completion_tokens' " + SENTINELA])
def test_reintento_max_tokens_una_sola_vez(modelo, monkeypatch, texto_error):
    d = DobleEjecutar(_bad_request(texto_error), _bad_request(texto_error), _resultado())
    monkeypatch.setattr(ag, "_ejecutar", d)
    with pytest.raises(ag.ErrorAgente) as e:
        ag.evaluar(PERFIL, TEXTO_CV)
    assert "rechazó la petición (400)" in e.value.mensaje
    assert SENTINELA not in e.value.mensaje
    assert len(d.llamadas) == 2


def test_reintento_max_tokens_conserva_reasoning(modelo, monkeypatch):
    monkeypatch.setenv("OPENROUTER_REASONING_EFFORT", "low")
    d = DobleEjecutar(_bad_request("Unsupported parameter: 'max_tokens'"), _resultado())
    monkeypatch.setattr(ag, "_ejecutar", d)
    ag.evaluar(PERFIL, TEXTO_CV)
    ms1 = d.llamadas[1].agente.model_settings
    assert ms1.max_tokens is None and ms1.reasoning.effort == "low" and ms1.temperature == 0


def test_reintento_varios_parametros_a_la_vez(modelo, monkeypatch):
    monkeypatch.setenv("OPENROUTER_REASONING_EFFORT", "low")
    d = DobleEjecutar(_bad_request("'temperature' and 'max_tokens' not supported"),
                      _resultado())
    monkeypatch.setattr(ag, "_ejecutar", d)
    ag.evaluar(PERFIL, TEXTO_CV)
    ms1 = d.llamadas[1].agente.model_settings
    assert ms1.temperature is None and ms1.max_tokens is None
    assert ms1.reasoning.effort == "low"


# ---- cliente: cabeceras y base URL (espia de AsyncOpenAI, sin red) ----
@pytest.fixture
def espia(monkeypatch):
    capturado = {}
    real = ag.AsyncOpenAI

    def _espia(**kw):
        capturado.update(kw)
        return real(**kw)

    monkeypatch.setattr(ag, "AsyncOpenAI", _espia)
    return capturado


def test_cabecera_x_title_sin_http_referer(modelo, espia):
    m = ag._crear_modelo()
    cab = espia["default_headers"]
    assert cab == {"X-Title": "evaluador_hv"}
    assert "http-referer" not in {k.lower() for k in cab}
    # y el cliente real las incluye
    efectivas = {k.lower(): v for k, v in m._client.default_headers.items()}
    assert efectivas["x-title"] == "evaluador_hv"
    assert "http-referer" not in efectivas
    assert espia["max_retries"] == 0


def test_cliente_usa_clave_del_entorno(modelo, monkeypatch, espia):
    monkeypatch.setenv("OPENROUTER_API_KEY", "clave-sentinela-xyz")
    ag._crear_modelo()
    assert espia["api_key"] == "clave-sentinela-xyz"


def test_base_url_personalizada_se_respeta(modelo, monkeypatch, espia):
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://proxy.invalid/v1")
    ag._crear_modelo()
    assert espia["base_url"] == "https://proxy.invalid/v1"


@pytest.mark.parametrize("valor", [None, "", "   "])
def test_base_url_vacia_o_ausente_usa_defecto(modelo, monkeypatch, espia, valor):
    if valor is None:
        monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("OPENROUTER_BASE_URL", valor)
    ag._crear_modelo()
    assert espia["base_url"] == ag.OPENROUTER_BASE_URL_DEFECTO == "https://openrouter.ai/api/v1"


# ---- .env.example ----
def test_env_example_modelo_con_valor_y_clave_vacia():
    activas = _lineas_activas()
    modelo_ = [l for l in activas if l.startswith("OPENROUTER_MODEL=")]
    assert len(modelo_) == 1 and modelo_[0].split("=", 1)[1].strip() != ""
    assert "OPENROUTER_API_KEY=" in [l.strip() for l in activas]


@pytest.mark.parametrize("prefijo", ["sk-or-", "sk-ant-", "AIza", "sk-"])
def test_env_example_sin_valores_que_parezcan_claves(prefijo):
    for linea in _lineas_activas():
        valor = linea.split("=", 1)[1] if "=" in linea else linea
        assert prefijo not in valor, linea


def test_env_example_secretos_vacios():
    activas = [l.strip() for l in _lineas_activas()]
    for var in ("OPENROUTER_API_KEY", "SECRET_KEY", "APP_PASSWORD"):
        assert var + "=" in activas, var


def test_env_example_y_app_sin_gemini_ni_openai():
    texto = (RAIZ / ".env.example").read_text(encoding="utf-8").upper()
    assert "GEMINI" not in texto
    assert "OPENAI_" not in texto
    infractores = []
    for f in (RAIZ / "app").rglob("*"):
        if not f.is_file() or f.suffix not in {".py", ".html", ".txt", ".json", ".md", ".css", ".js"}:
            continue
        t = f.read_text(encoding="utf-8", errors="ignore")
        if "GEMINI" in t.upper() or "OPENAI_" in t:
            infractores.append(f.relative_to(RAIZ / "app").as_posix())
    assert infractores == []


def test_solo_agente_evaluador_importa_agents_u_openai():
    patron = re.compile(r"^\s*(from|import)\s+(agents|openai)\b", re.MULTILINE)
    fuera = [
        f.relative_to(RAIZ / "app").as_posix() for f in (RAIZ / "app").rglob("*.py")
        if f.name != "agente_evaluador.py"
        and patron.search(f.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert fuera == []


# ---- mensajes de usuario sin OpenAI ni Gemini ----
def test_mensajes_de_usuario_sin_openai_ni_gemini(modelo, monkeypatch):
    import asyncio
    from openai import (AuthenticationError, BadRequestError, NotFoundError,
                        PermissionDeniedError, RateLimitError)
    mensajes = []
    errores = [
        _err_402(), _error_api(AuthenticationError, 401, "x"),
        _error_api(PermissionDeniedError, 403, "x"), _error_api(RateLimitError, 429, "x"),
        _error_api(NotFoundError, 404, "x"), _error_api(BadRequestError, 400, "x"),
        asyncio.TimeoutError(),
    ]
    for err in errores:
        monkeypatch.setattr(ag, "_ejecutar", DobleEjecutar(err))
        with pytest.raises(ag.ErrorAgente) as e:
            ag.evaluar(PERFIL, TEXTO_CV)
        mensajes.append(e.value.mensaje)
    for var, val in (("OPENROUTER_API_KEY", None), ("OPENROUTER_MODEL", None),
                     ("OPENROUTER_MAX_TOKENS", "abc"), ("MAX_TURNOS_AGENTE", "0"),
                     ("TIMEOUT_AGENTE_S", "0")):
        with monkeypatch.context() as m:
            if val is None:
                m.delenv(var, raising=False)
            else:
                m.setenv(var, val)
            with pytest.raises(ag.ErrorConfiguracionIA) as e:
                ag.evaluar(PERFIL, TEXTO_CV)
            mensajes.append(e.value.mensaje)
    for msg in mensajes:
        bajo = msg.lower()
        assert "openai" not in bajo and "gemini" not in bajo, msg
    # solo el 402 nombra a OpenRouter como proveedor
    assert MSG_402 in mensajes
