"""Ruta POST /evaluar, ranking del inicio y arquitectura. La IA siempre es un doble."""
import hashlib
import importlib
import json
import re
import sys
from pathlib import Path

import pytest

from app.extensions import db
from app.models import Candidato, Evaluacion, EvaluacionHV, Vacante
from app.services import agente_evaluador
from app.services.agente_evaluador import Metadatos
from tests.helpers import cabeceras

RAIZ = Path(__file__).resolve().parent.parent

TEXTO_CV = (
    "Maria Secreta Gomez. Desarrollo de servicios en Python y consultas SQL en PostgreSQL. "
    "Agentes con LangChain y RAG para clientes. Trabajo con Docker y Azure. " * 3
)


def crear(app, nombre="Ana", estado="pendiente", texto=TEXTO_CV, evaluando_desde=None):
    with app.app_context():
        v = Vacante.query.filter_by(codigo="ING-IA-2026").first() or Vacante.query.first()
        c = Candidato(vacante_id=v.id, archivo=f"{nombre}.pdf", nombre=nombre,
                      sha256=hashlib.sha256(nombre.encode()).hexdigest(),
                      pdf=b"%PDF-1.4 x", texto=texto, estado=estado,
                      evaluando_desde=evaluando_desde)
        db.session.add(c)
        db.session.commit()
        return c.id


def respuesta(perfil, citas_malas=(), no_cumple=()):
    """EvaluacionHV con todos los criterios del perfil; citas reales salvo las indicadas."""
    criterios = []
    for c in perfil["obligatorios"] + perfil["deseables"]:
        cod = c["codigo"]
        evid = "Dato inventado que no aparece jamas" if cod in citas_malas else "servicios en Python"
        criterios.append({"codigo": cod, "cumple": cod not in no_cumple, "evidencia": evid})
    return EvaluacionHV(criterios=criterios, alertas_seguridad=["Orden dirigida a la IA"],
                        resumen="Resumen neutral")


class Doble:
    def __init__(self, citas_malas=(), no_cumple=(), error=None):
        self.llamadas = 0
        self.citas_malas, self.no_cumple, self.error = citas_malas, no_cumple, error

    def __call__(self, perfil, texto):
        self.llamadas += 1
        if self.error:
            raise self.error
        return (respuesta(perfil, self.citas_malas, self.no_cumple),
                Metadatos("modelo-falso", 10, 5, 0.1))


@pytest.fixture
def doble(monkeypatch):
    d = Doble()
    monkeypatch.setattr(agente_evaluador, "evaluar", d)
    return d


def post(client, cid, headers=None):
    if headers is None:
        headers = cabeceras(client)
    return client.post(f"/evaluar/{cid}", headers=headers)


def test_sin_cabecera_403(app, client_autenticado, doble):
    cid = crear(app)
    assert post(client_autenticado, cid, headers={}).status_code == 403
    assert doble.llamadas == 0


def test_id_inexistente_404(client_autenticado, doble):
    assert post(client_autenticado, 9999).status_code == 404
    assert doble.llamadas == 0


def test_sin_sesion_redirige_al_login(app, client, doble):
    cid = crear(app)
    r = post(client, cid)
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert doble.llamadas == 0


def test_sin_texto_no_llama_a_la_ia(app, client_autenticado, doble):
    cid = crear(app, "Sin", estado="sin_texto", texto="corto")
    r = post(client_autenticado, cid)
    assert r.status_code == 200 and r.get_json()["sin_cambios"] is True
    assert doble.llamadas == 0
    with app.app_context():
        assert Evaluacion.query.count() == 0
        assert db.session.get(Candidato, cid).estado == "sin_texto"


def test_evaluacion_ok_guarda_resultado(app, client_autenticado, doble):
    cid = crear(app)
    r = post(client_autenticado, cid)
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] and j["sin_cambios"] is False
    assert doble.llamadas == 1
    with app.app_context():
        e = Evaluacion.query.one()
        assert e.candidato_id == cid
        assert e.avanza is True and e.puntaje == 100
        assert e.modelo == "modelo-falso" and e.tokens_entrada == 10
        alertas = json.loads(e.alertas_json)
        assert "Orden dirigida a la IA" in alertas
        assert not any(a.startswith("Evidencia no verificada") for a in alertas)
        assert db.session.get(Candidato, cid).estado == "evaluado"


def test_cita_inventada_genera_alerta_y_no_cuenta(app, client_autenticado, monkeypatch):
    monkeypatch.setattr(agente_evaluador, "evaluar", Doble(citas_malas=("O2", "D1")))
    cid = crear(app)
    assert post(client_autenticado, cid).status_code == 200
    with app.app_context():
        e = Evaluacion.query.one()
        assert e.avanza is False and e.puntaje == 0
        assert "Evidencia no verificada en: O2, D1" in json.loads(e.alertas_json)
        assert db.session.get(Candidato, cid).estado == "evaluado"


def test_deseable_inventado_baja_puntaje(app, client_autenticado, monkeypatch):
    monkeypatch.setattr(agente_evaluador, "evaluar", Doble(citas_malas=("D1",)))
    cid = crear(app)
    post(client_autenticado, cid)
    with app.app_context():
        e = Evaluacion.query.one()
        assert e.avanza is True and e.puntaje == 80
        assert "Evidencia no verificada en: D1" in json.loads(e.alertas_json)


def test_falta_obligatorio_puntaje_0(app, client_autenticado, monkeypatch):
    monkeypatch.setattr(agente_evaluador, "evaluar", Doble(no_cumple=("O3",)))
    cid = crear(app)
    post(client_autenticado, cid)
    with app.app_context():
        e = Evaluacion.query.one()
        assert e.avanza is False and e.puntaje == 0


def test_segunda_llamada_no_reevalua(app, client_autenticado, doble):
    cid = crear(app)
    post(client_autenticado, cid)
    r = post(client_autenticado, cid)
    assert r.status_code == 200 and r.get_json()["sin_cambios"] is True
    assert doble.llamadas == 1
    with app.app_context():
        assert Evaluacion.query.count() == 1


def test_falla_la_ia_502_y_sigue_pendiente(app, client_autenticado, monkeypatch):
    monkeypatch.setattr(agente_evaluador, "evaluar",
                        Doble(error=RuntimeError("fallo con Maria Secreta Gomez en el texto")))
    cid = crear(app)
    r = post(client_autenticado, cid)
    assert r.status_code == 502
    j = r.get_json()
    assert j["ok"] is False and j["error"]
    assert "Maria" not in j["error"] and "Secreta" not in j["error"] and "Python" not in j["error"]
    with app.app_context():
        assert db.session.get(Candidato, cid).estado == "pendiente"
        assert Evaluacion.query.count() == 0
    # se puede reintentar
    monkeypatch.setattr(agente_evaluador, "evaluar", Doble())
    assert post(client_autenticado, cid).status_code == 200


def test_inicio_sin_sesion_redirige(client):
    assert client.get("/").status_code == 302


def test_ranking_en_inicio_orden_r7(app, client_autenticado):
    with app.app_context():
        vid = Vacante.query.first().id

        def nuevo(nombre, estado, avanza=None, puntaje=0):
            c = Candidato(vacante_id=vid, archivo=nombre + ".pdf", nombre=nombre,
                          sha256=hashlib.sha256(nombre.encode()).hexdigest(),
                          pdf=b"x", texto="t", estado=estado)
            db.session.add(c)
            db.session.flush()
            if avanza is not None:
                db.session.add(Evaluacion(candidato_id=c.id, criterios_json="[]", avanza=avanza,
                                          puntaje=puntaje, alertas_json="[]"))

        nuevo("Zzz Pendiente", "pendiente")
        nuevo("Aaa SinTexto", "sin_texto")
        nuevo("Noavanza Alto", "evaluado", False, 0)
        nuevo("Beto Veinte", "evaluado", True, 20)
        nuevo("Ana Veinte", "evaluado", True, 20)
        nuevo("Carlos Sesenta", "evaluado", True, 60)
        db.session.commit()
    html = client_autenticado.get(f"/?vacante={vid}").get_data(as_text=True)
    orden = ["Carlos Sesenta", "Ana Veinte", "Beto Veinte", "Noavanza Alto",
             "Aaa SinTexto", "Zzz Pendiente"]
    pos = [html.find(n) for n in orden]
    assert all(p >= 0 for p in pos), pos
    assert pos == sorted(pos)


# ---- arquitectura ----
def test_solo_agente_evaluador_importa_agents_u_openai():
    patron = re.compile(r"^\s*(import|from)\s+(agents|openai)\b")
    infractores = set()
    for f in (RAIZ / "app").rglob("*.py"):
        for linea in f.read_text(encoding="utf-8").splitlines():
            if patron.match(linea):
                infractores.add(f.relative_to(RAIZ).as_posix())
    assert infractores == {"app/services/agente_evaluador.py"}


def test_importar_la_app_no_exige_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    nombres = ("app.services.agente_evaluador", "app.services.evaluacion", "app.controllers.main")
    # monkeypatch restaura sys.modules al terminar: sin esto los tests posteriores parchean un
    # modulo viejo y la app usa el recien importado (el parche no surte efecto).
    for m in nombres:
        monkeypatch.setitem(sys.modules, m, sys.modules.get(m))
    for m in nombres:
        sys.modules.pop(m, None)
        importlib.import_module(m)


# ---- reaplicación de R3 antes de la IA ----
def test_reaplica_limpieza_antes_de_la_ia_y_r5_usa_el_mismo_texto(app, client_autenticado,
                                                                 monkeypatch):
    sucio = (TEXTO_CV + "\nContacto: ana.secreta@correo.example.co | Tel: 300 123 4567\n"
             "Implementé servicios en Python para clientes.")
    cid = crear(app, texto=sucio)
    recibido = {}

    def doble(perfil, texto):
        recibido["texto"] = texto
        # cita copiada del texto LIMPIO que recibe la IA
        cita = "Implementé servicios en Python"
        criterios = [{"codigo": c["codigo"], "cumple": True, "evidencia": cita}
                     for c in perfil["obligatorios"] + perfil["deseables"]]
        return (EvaluacionHV(criterios=criterios, alertas_seguridad=[], resumen="r"),
                Metadatos("modelo-falso", 1, 1, 0.1))

    monkeypatch.setattr(agente_evaluador, "evaluar", doble)
    r = post(client_autenticado, cid)
    assert r.status_code == 200
    t = recibido["texto"]
    assert "@" not in t and "ana.secreta" not in t
    assert "300 123 4567" not in t and "4567" not in t
    assert "[correo]" in t and "[teléfono]" in t
    with app.app_context():
        e = Evaluacion.query.one()
        assert e.avanza is True and e.puntaje == 100  # R5 verificó contra el texto limpio
        assert not any(a.startswith("Evidencia no verificada") for a in json.loads(e.alertas_json))
        # el texto guardado no se modifica
        assert db.session.get(Candidato, cid).texto == sucio


def test_cita_con_el_correo_original_no_cuenta(app, client_autenticado, monkeypatch):
    sucio = TEXTO_CV + "\nEscribir a ana.secreta@correo.example.co\n"
    cid = crear(app, texto=sucio)

    def doble(perfil, texto):
        criterios = [{"codigo": c["codigo"], "cumple": True,
                      "evidencia": "ana.secreta@correo.example.co"}
                     for c in perfil["obligatorios"] + perfil["deseables"]]
        return (EvaluacionHV(criterios=criterios, alertas_seguridad=[], resumen="r"),
                Metadatos("m", 1, 1, 0.1))

    monkeypatch.setattr(agente_evaluador, "evaluar", doble)
    assert post(client_autenticado, cid).status_code == 200
    with app.app_context():
        e = Evaluacion.query.one()
        assert e.avanza is False and e.puntaje == 0
        assert any(a.startswith("Evidencia no verificada en:") for a in json.loads(e.alertas_json))
