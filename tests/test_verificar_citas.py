"""Herramienta verificar_citas (una llamada, N citas) y flujo de turnos. SIN red ni API."""
import copy
import json
from pathlib import Path

import pytest

from app.extensions import db
from app.models import Candidato, Evaluacion, Vacante
from app.services import agente_evaluador as ag
from tests.helpers import cabeceras
from tests.test_agente import PERFIL, _invocar_verificar

# El conftest sustituye Runner.run y ag._ejecutar por un guard en cada test; se guardan los
# reales al importar para el test de turnos (que solo usa un modelo falso, sin red).
RUN_REAL = ag.Runner.run
EJECUTAR_REAL = ag._ejecutar

RAIZ = Path(__file__).resolve().parent.parent
FRASES = [f"habilidad numero{i:02d} comprobada en produccion" for i in range(40)]
TEXTO = ". ".join(FRASES) + "."
NO_EXISTE = "NO EXISTE: copia el texto exacto o marca cumple=false"


def ctx(texto=TEXTO):
    return ag.Contexto(perfil=copy.deepcopy(PERFIL), texto=texto)


def lineas(citas, texto=TEXTO):
    return _invocar_verificar(ctx(texto), citas).split("\n")


# ---- constantes, esquema y exposicion ----
def test_constantes():
    assert ag.MAX_CITAS_VERIFICAR == 30
    assert ag.MAX_LARGO_CITA == 500


def test_esquema_citas_array_de_string_no_estricto():
    t = ag.verificar_citas
    assert t.name == "verificar_citas"
    assert t.strict_json_schema is False
    esq = t.params_json_schema
    assert esq["properties"]["citas"]["type"] == "array"
    assert esq["properties"]["citas"]["items"]["type"] == "string"
    assert list(esq["properties"]) == ["citas"]
    assert esq.get("additionalProperties") is not False
    assert "citas" in esq.get("required", [])


def test_construir_agente_solo_expone_verificar_citas(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", "modelo-falso")
    assert not hasattr(ag, "verificar_cita")
    a = ag.construir_agente()
    assert [t.name for t in a.tools] == ["verificar_citas"]


def test_instrucciones_piden_una_sola_llamada():
    assert "verificar_citas" in ag.INSTRUCCIONES
    assert "una sola vez" in ag.INSTRUCCIONES.lower()
    assert "verificar_cita " not in ag.INSTRUCCIONES


# ---- cantidad de citas ----
def test_cero_citas_mensaje_exacto():
    assert _invocar_verificar(ctx(), []) == (
        "Sin citas: no hay nada que verificar. Responde con cumple=false donde no haya evidencia.")


def test_una_cita():
    assert lineas([FRASES[3]]) == ["1: EXISTE"]
    assert lineas(["frase que no esta en el documento"]) == [f"1: {NO_EXISTE}"]


def test_nueve_citas_en_orden_con_mezcla():
    citas, esperado = [], []
    for i in range(9):
        if i % 2 == 0:
            citas.append(FRASES[i])
            esperado.append(f"{i + 1}: EXISTE")
        else:
            citas.append(f"inventada {i} que no aparece jamas")
            esperado.append(f"{i + 1}: {NO_EXISTE}")
    assert lineas(citas) == esperado


def test_treinta_citas_todas_verificadas():
    assert lineas(FRASES[:30]) == [f"{i}: EXISTE" for i in range(1, 31)]


def test_31_citas_la_ultima_no_verificada():
    r = lineas(FRASES[:31])
    assert len(r) == 31
    assert r[29] == "30: EXISTE"
    assert r[30] == "31: NO VERIFICADA: demasiadas citas"


def test_35_citas_desde_la_31_no_verificadas():
    r = lineas(FRASES[:35])
    assert len(r) == 35
    assert r[:30] == [f"{i}: EXISTE" for i in range(1, 31)]
    assert r[30:] == [f"{i}: NO VERIFICADA: demasiadas citas" for i in range(31, 36)]


def test_excedentes_no_verificadas_exista_o_no_la_cita():
    r = lineas(FRASES[:30] + ["inventada que no aparece"] + [FRASES[31]])
    assert r[30] == "31: NO VERIFICADA: demasiadas citas"
    assert r[31] == "32: NO VERIFICADA: demasiadas citas"


# ---- largo ----
def test_cita_de_2000_caracteres_se_trunca_sin_error():
    texto = "x" * 600 + " fin"
    assert lineas(["x" * 2000], texto) == ["1: EXISTE"]  # solo cuentan los primeros 500


def test_cita_larga_cuyo_inicio_no_esta_no_existe():
    assert lineas(["no esta " + "y" * 1992]) == [f"1: {NO_EXISTE}"]


def test_cita_mayor_a_500_se_compara_solo_el_prefijo():
    prefijo = "z" * ag.MAX_LARGO_CITA
    r = lineas([prefijo + " cola inventada"], "inicio " + prefijo + " otro final")
    assert r == ["1: EXISTE"]


# ---- normalizacion R5 ----
def test_normalizacion_tildes_mayusculas_y_espacios():
    texto = "Experiencia en Gestión de Información y Análisis"
    r = lineas(["GESTION   DE  INFORMACION", "analisis", "experiencia en gestión"], texto)
    assert r == ["1: EXISTE", "2: EXISTE", "3: EXISTE"]


def test_saltos_de_linea_en_la_cita_y_en_el_texto():
    texto = "Lideré el diseño de arquitectura en proyectos de datos"
    assert lineas(["LIDERE el\ndiseño de\n\narquitectura"], texto) == ["1: EXISTE"]
    assert lineas(["Lideré el diseño"], "Lideré el\ndiseño de arquitectura") == ["1: EXISTE"]


@pytest.mark.parametrize("cita", ["", "   ", "abc", " ab ", "\n\n"])
def test_cita_vacia_o_menor_de_4_caracteres_no_existe(cita):
    assert lineas([cita], "abc abcd contenido con texto suficiente") == [f"1: {NO_EXISTE}"]


def test_cita_exactamente_4_caracteres_existe():
    assert lineas(["abcd"], "xx abcd yy") == ["1: EXISTE"]


@pytest.mark.parametrize("cita", ["[correo]", "[enlace]", "[teléfono]", "[correo] [enlace]",
                                  "[etiqueta eliminada]"])
def test_citas_solo_marcadores_no_existen(cita):
    texto = f"Contacto {cita} y mas texto de la hoja de vida"
    assert lineas([cita], texto) == [f"1: {NO_EXISTE}"]


def test_cita_con_marcador_y_contenido_real_si_existe():
    texto = "Escribir a [correo] para Arquitectura de soluciones"
    assert lineas(["[correo] para Arquitectura"], texto) == ["1: EXISTE"]


# ---- solo lectura y no filtrado ----
def test_contexto_queda_identico_tras_invocar():
    c = ctx()
    antes_perfil = copy.deepcopy(c.perfil)
    antes_texto = c.texto
    _invocar_verificar(c, FRASES[:35] + ["x" * 2000, "", "[correo]"])
    assert c.perfil == antes_perfil
    assert c.texto == antes_texto


def test_salida_no_contiene_texto_del_documento_ni_la_cita():
    centinela = "CENTINELA-ZETA-" + "q7k2m9x4" * 12
    texto = f"Inicio del documento. {centinela} fin del documento con mas contenido."
    citas = [centinela, centinela[:60], "algo inventado largo " + centinela]
    salida = _invocar_verificar(ctx(texto), citas)
    assert "CENTINELA" not in salida and "q7k2m9x4" not in salida
    assert "Inicio del documento" not in salida and "fin del documento" not in salida
    assert salida.split("\n")[0] == "1: EXISTE"
    for i, linea in enumerate(salida.split("\n"), start=1):
        assert linea.startswith(f"{i}: ")


def test_salida_no_hace_eco_de_cita_inexistente():
    salida = _invocar_verificar(ctx(), ["frase-inventada-unica-98765 que no esta"])
    assert "98765" not in salida


# ---- flujo completo con modelo falso (sin red) ----
def _modelo_falso(citas, llamadas_herramienta=1, registro=None):
    """Modelo del SDK que pide `llamadas_herramienta` veces la herramienta y luego responde."""
    from agents import Model, ModelResponse, Usage
    from openai.types.responses import (ResponseFunctionToolCall, ResponseOutputMessage,
                                        ResponseOutputText)

    final = json.dumps({
        "criterios": [{"codigo": "O1", "cumple": True, "evidencia": FRASES[0]}],
        "alertas_seguridad": [], "resumen": "Resumen neutral"})

    class Falso(Model):
        def __init__(self):
            self.n = 0

        async def get_response(self, *args, **kwargs):
            self.n += 1
            if registro is not None:
                registro.append(self.n)
            if self.n <= llamadas_herramienta:
                item = ResponseFunctionToolCall(
                    type="function_call", call_id=f"c{self.n}", name="verificar_citas",
                    arguments=json.dumps({"citas": citas}))
            else:
                item = ResponseOutputMessage(
                    id="m1", type="message", role="assistant", status="completed",
                    content=[ResponseOutputText(type="output_text", text=final, annotations=[])])
            return ModelResponse(output=[item], usage=Usage(), response_id=None)

        def stream_response(self, *args, **kwargs):
            raise AssertionError("no se usa streaming")

    return Falso()


def _correr(monkeypatch, modelo, max_turnos):
    monkeypatch.setattr(ag.Runner, "run", RUN_REAL)  # el guard lo prohibe; aqui no hay red
    monkeypatch.setenv("OPENROUTER_MODEL", "modelo-falso")
    agente = ag.construir_agente()
    agente.model = modelo  # sustituye el modelo OpenRouter: nunca hay peticion HTTP
    return EJECUTAR_REAL(agente, "mensaje", ctx(), max_turnos, 30)


def test_flujo_una_llamada_con_9_citas_mas_respuesta_cabe_en_12_turnos(monkeypatch):
    registro = []
    res = _correr(monkeypatch, _modelo_falso(FRASES[:9], registro=registro), 12)
    assert registro == [1, 2]  # turno 1: herramienta con 9 citas; turno 2: respuesta final
    assert res.final_output.criterios[0].codigo == "O1"
    salidas = [i for i in res.new_items if type(i).__name__ == "ToolCallOutputItem"]
    assert len(salidas) == 1
    assert salidas[0].output.split("\n") == [f"{i}: EXISTE" for i in range(1, 10)]


def test_flujo_excede_max_turns_si_el_modelo_insiste(monkeypatch):
    from agents.exceptions import MaxTurnsExceeded
    with pytest.raises(MaxTurnsExceeded):
        _correr(monkeypatch, _modelo_falso(FRASES[:9], llamadas_herramienta=99), 3)


# ---- integracion: POST /evaluar con el perfil real y un doble de evaluar ----
def test_post_evaluar_con_perfil_real_ai_ml_senior(app, client_autenticado, monkeypatch):
    from app.models.schemas import EvaluacionHV
    from app.services.agente_evaluador import Metadatos
    ruta = RAIZ / "insumos" / "perfiles" / "ai_ml_senior.json"
    perfil_real = json.loads(ruta.read_text(encoding="utf-8"))  # solo lectura
    codigos = [c["codigo"] for c in perfil_real["obligatorios"] + perfil_real["deseables"]]
    assert len(codigos) == 9

    texto = ("Arquitecto de soluciones con Python, Azure ML y RAG. Diseno de pipelines "
             "de datos y bases vectoriales. " * 4)
    recibido = {}

    def doble(perfil, texto_cv):
        recibido["perfil"] = perfil
        criterios = [{"codigo": c, "cumple": True, "evidencia": "Azure ML y RAG"}
                     for c in codigos]
        return (EvaluacionHV(criterios=criterios, alertas_seguridad=[], resumen="ok"),
                Metadatos("modelo-falso", 10, 5, 0.1))
    monkeypatch.setattr(ag, "evaluar", doble)

    with app.app_context():
        v = Vacante.query.filter_by(codigo=perfil_real["codigo"]).one()
        c = Candidato(vacante_id=v.id, archivo="a.pdf", nombre="Ana", sha256="e" * 64,
                      pdf=b"%PDF-1.4 x", texto=texto, estado="pendiente")
        db.session.add(c)
        db.session.commit()
        cid = c.id

    r = client_autenticado.post(f"/evaluar/{cid}", headers=cabeceras(client_autenticado))
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] and j["sin_cambios"] is False
    vistos = recibido["perfil"]["obligatorios"] + recibido["perfil"]["deseables"]
    assert [c["codigo"] for c in vistos] == codigos
    with app.app_context():
        e = Evaluacion.query.one()
        assert e.avanza is True
        assert e.puntaje == sum(d["puntos"] for d in perfil_real["deseables"])
        assert db.session.get(Candidato, cid).estado == "evaluado"
