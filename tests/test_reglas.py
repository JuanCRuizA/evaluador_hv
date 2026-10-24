"""R5 a R7 en Python puro (sin Flask)."""
import json
from pathlib import Path

import pytest

from app.services import reglas

PERFIL_REAL = Path(__file__).resolve().parent.parent / "insumos" / "perfiles" / "ingeniero_ia.json"

TEXTO = (
    "Desarrollé servicios en Python durante 3 años.\n"
    "Consultas SQL en PostgreSQL.   Experiencia con agentes LangChain y RAG.\n"
    "Trabajé con Docker y Azure; evaluación con RAGAS y scikit-learn."
)

PERFIL = {
    "obligatorios": [{"codigo": "O1"}, {"codigo": "O2"}],
    "deseables": [
        {"codigo": "D1", "puntos": 30},
        {"codigo": "D2", "puntos": 15},
        {"codigo": "D3", "puntos": 5},
    ],
}

CITAS = {
    "O1": "servicios en Python",
    "O2": "Consultas SQL en PostgreSQL",
    "D1": "agentes LangChain y RAG",
    "D2": "Docker y Azure",
    "D3": "scikit-learn",
}


def crit(codigo, cumple=True, evidencia=None):
    return {"codigo": codigo, "cumple": cumple,
            "evidencia": CITAS.get(codigo, "") if evidencia is None else evidencia}


def todos(**cambios):
    """Respuesta completa del modelo; cambios: codigo -> dict de campos a sobrescribir."""
    out = []
    for cod in CITAS:
        c = crit(cod)
        c.update(cambios.get(cod, {}))
        out.append(c)
    return out


def por_codigo(r):
    return {c["codigo"]: c for c in r["criterios"]}


# ---- cita_existe ----
def test_cita_literal_existe():
    assert reglas.cita_existe(TEXTO, "servicios en Python")


def test_cita_parcial_literal_existe():
    assert reglas.cita_existe(TEXTO, "Consultas SQL")


def test_cita_ignora_mayusculas():
    assert reglas.cita_existe(TEXTO, "SERVICIOS EN PYTHON")


def test_cita_ignora_tildes():
    assert reglas.cita_existe(TEXTO, "Desarrolle servicios en Python")
    assert reglas.cita_existe(TEXTO, "DURANTE 3 ANOS")
    assert reglas.cita_existe("Gestión de bases de datos", "gestion de bases")


def test_cita_ignora_espacios_y_saltos_extra():
    assert reglas.cita_existe(TEXTO, "SQL   en\n PostgreSQL")
    assert reglas.cita_existe(TEXTO, "Python durante 3 años.\nConsultas")


def test_cita_de_3_caracteres_no_cuenta():
    assert "sql" in TEXTO.lower()
    assert not reglas.cita_existe(TEXTO, "SQL")


def test_cita_de_4_caracteres_cuenta():
    assert reglas.cita_existe(TEXTO, "RAGAS")
    assert reglas.cita_existe(TEXTO, "Azure")
    assert reglas.cita_existe(TEXTO, "Dock")


def test_cita_vacia_o_solo_espacios():
    assert not reglas.cita_existe(TEXTO, "")
    assert not reglas.cita_existe(TEXTO, "     \n ")
    assert not reglas.cita_existe(TEXTO, None)


def test_cita_inventada_no_existe():
    assert not reglas.cita_existe(TEXTO, "Dirigi un equipo de 20 personas")


def test_minimo_se_mide_tras_normalizar():
    # 6 caracteres pero 3 tras normalizar espacios
    assert not reglas.cita_existe("a b c d", "a    b")


def test_normalizar():
    assert reglas.normalizar("  ÁrBol \n  Ñandú ") == "arbol nandu"


# ---- resultado ----
def test_todo_cumple_avanza_y_100_perfil_real():
    perfil = json.loads(PERFIL_REAL.read_text(encoding="utf-8"))
    citas = {"O1": "servicios en Python", "O2": "SQL en PostgreSQL", "O3": "agentes LangChain",
             "O4": "durante 3 años", "D1": "agentes LangChain y RAG", "D2": "Azure",
             "D3": "Docker", "D4": "RAGAS", "D5": "scikit-learn"}
    criterios = [{"codigo": k, "cumple": True, "evidencia": v} for k, v in citas.items()]
    r = reglas.resultado(perfil, TEXTO, criterios)
    assert r["avanza"] is True
    assert r["puntaje"] == 100
    assert r["no_verificados"] == []
    assert all(c["cuenta"] for c in r["criterios"])
    assert len(r["criterios"]) == 9


def test_todo_cumple_sintetico():
    r = reglas.resultado(PERFIL, TEXTO, todos())
    assert r["avanza"] is True
    assert r["puntaje"] == 50
    assert r["no_verificados"] == []


def test_falta_obligatorio_no_avanza_y_puntaje_0_aunque_deseables_cuenten():
    r = reglas.resultado(PERFIL, TEXTO, todos(O2={"cumple": False}))
    assert r["avanza"] is False
    assert r["puntaje"] == 0
    assert por_codigo(r)["D1"]["cuenta"] is True
    assert r["no_verificados"] == []


def test_obligatorio_con_cita_inventada_no_cuenta_ni_avanza():
    r = reglas.resultado(PERFIL, TEXTO, todos(O1={"evidencia": "Doctorado en fisica cuantica"}))
    c = por_codigo(r)["O1"]
    assert c["cumple_ia"] is True and c["verificada"] is False and c["cuenta"] is False
    assert r["no_verificados"] == ["O1"]
    assert r["avanza"] is False
    assert r["puntaje"] == 0


def test_deseable_no_verificado_no_suma():
    r = reglas.resultado(PERFIL, TEXTO, todos(D1={"evidencia": "Maestria en vectores"}))
    assert r["avanza"] is True
    assert r["puntaje"] == 20  # 15 + 5, sin los 30 de D1
    assert r["no_verificados"] == ["D1"]


def test_cumple_false_con_cita_valida_no_es_no_verificado():
    r = reglas.resultado(PERFIL, TEXTO, todos(D1={"cumple": False}))
    assert r["no_verificados"] == []
    assert r["puntaje"] == 20


def test_cita_corta_cuenta_como_no_verificada():
    r = reglas.resultado(PERFIL, TEXTO, todos(D3={"evidencia": "SQL"}))
    assert r["no_verificados"] == ["D3"]
    assert r["puntaje"] == 45


def test_criterio_ausente_cuenta_como_no_cumple():
    criterios = [c for c in todos() if c["codigo"] != "O2"]
    r = reglas.resultado(PERFIL, TEXTO, criterios)
    assert por_codigo(r)["O2"]["cumple_ia"] is False
    assert por_codigo(r)["O2"]["cuenta"] is False
    assert r["avanza"] is False and r["puntaje"] == 0
    assert r["no_verificados"] == []


def test_deseable_ausente_no_suma_pero_avanza():
    criterios = [c for c in todos() if c["codigo"] != "D1"]
    r = reglas.resultado(PERFIL, TEXTO, criterios)
    assert r["avanza"] is True
    assert r["puntaje"] == 20


def test_respuesta_vacia():
    r = reglas.resultado(PERFIL, TEXTO, [])
    assert r["avanza"] is False and r["puntaje"] == 0
    assert len(r["criterios"]) == 5


def test_codigo_desconocido_se_ignora():
    extra = todos() + [{"codigo": "X9", "cumple": True, "evidencia": "servicios en Python"}]
    r = reglas.resultado(PERFIL, TEXTO, extra)
    assert "X9" not in por_codigo(r)
    assert r["puntaje"] == 50 and r["avanza"] is True


def test_puntos_vienen_del_perfil():
    perfil = {"obligatorios": [{"codigo": "O1"}],
              "deseables": [{"codigo": "D1", "puntos": 7}, {"codigo": "D2", "puntos": 40}]}
    r = reglas.resultado(perfil, TEXTO, [crit("O1"), crit("D1", evidencia="Consultas SQL"),
                                         crit("D2", evidencia="Docker y Azure")])
    assert r["puntaje"] == 47
    r = reglas.resultado(perfil, TEXTO, [crit("O1"), crit("D2", evidencia="Docker y Azure")])
    assert r["puntaje"] == 40


def test_acepta_objetos_pydantic():
    from app.models.schemas import CriterioEvaluado
    criterios = [CriterioEvaluado(**c) for c in todos()]
    r = reglas.resultado(PERFIL, TEXTO, criterios)
    assert r["avanza"] is True and r["puntaje"] == 50


# ---- alerta ----
def test_alerta_formato():
    assert reglas.alerta_no_verificados(["O1", "D2"]) == "Evidencia no verificada en: O1, D2"
    assert reglas.alerta_no_verificados(["D3"]) == "Evidencia no verificada en: D3"


def test_alerta_vacia():
    assert reglas.alerta_no_verificados([]) == ""


# ---- ranking ----
def test_ranking_avanzan_primero_luego_puntaje_luego_nombre():
    items = [
        {"nombre": "Zoe", "avanza": False, "puntaje": 0},
        {"nombre": "Ana", "avanza": True, "puntaje": 20},
        {"nombre": "Luis", "avanza": True, "puntaje": 60},
        {"nombre": "Beto", "avanza": True, "puntaje": 20},
        {"nombre": "Abel", "avanza": False, "puntaje": 0},
    ]
    orden = [i["nombre"] for i in reglas.ordenar_ranking(items)]
    assert orden == ["Luis", "Ana", "Beto", "Abel", "Zoe"]


def test_ranking_nombre_sin_mayusculas_ni_tildes():
    items = [
        {"nombre": "zulema", "avanza": True, "puntaje": 10},
        {"nombre": "Ángel", "avanza": True, "puntaje": 10},
        {"nombre": "bruno", "avanza": True, "puntaje": 10},
        {"nombre": "Álvaro", "avanza": True, "puntaje": 10},
        {"nombre": "Carla", "avanza": True, "puntaje": 10},
    ]
    orden = [i["nombre"] for i in reglas.ordenar_ranking(items)]
    assert orden == ["Álvaro", "Ángel", "bruno", "Carla", "zulema"]


def test_ranking_no_avanza_con_puntaje_alto_va_despues():
    items = [{"nombre": "A", "avanza": False, "puntaje": 99},
             {"nombre": "B", "avanza": True, "puntaje": 1}]
    assert [i["nombre"] for i in reglas.ordenar_ranking(items)] == ["B", "A"]


# ---- citas que son solo marcadores de limpieza ----
TEXTO_MARC = "Contacto [correo] y [teléfono] en [enlace] go [etiqueta eliminada] [correo] abcd fin"


@pytest.mark.parametrize("cita", [
    "[correo]", "[teléfono]", "[telefono]", "[enlace]", "[enlace] go", "[etiqueta eliminada]",
    "[CORREO]", "[correo] [teléfono]",
])
def test_cita_solo_marcadores_no_cuenta(cita):
    assert not reglas.cita_existe(TEXTO_MARC, cita)


def test_cita_marcador_con_texto_suficiente_cuenta_si_existe():
    assert reglas.cita_existe(TEXTO_MARC, "[correo] abcd")
    assert not reglas.cita_existe(TEXTO_MARC, "[correo] zzzz")


def test_obligatorio_con_cita_marcador_no_avanza_y_alerta():
    crits = todos(O1={"evidencia": "[correo]"})
    texto = TEXTO + "\n[correo]"
    r = reglas.resultado(PERFIL, texto, crits)
    assert r["avanza"] is False and r["puntaje"] == 0
    assert r["no_verificados"] == ["O1"]
    assert por_codigo(r)["O1"]["cuenta"] is False
    assert reglas.alerta_no_verificados(r["no_verificados"]) == "Evidencia no verificada en: O1"


def test_deseable_con_cita_marcador_no_suma():
    r = reglas.resultado(PERFIL, TEXTO + "\n[enlace]", todos(D1={"evidencia": "[enlace]"}))
    assert r["avanza"] is True and r["puntaje"] == 20
    assert r["no_verificados"] == ["D1"]
