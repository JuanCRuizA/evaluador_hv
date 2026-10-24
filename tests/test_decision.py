"""Detalle del candidato y decision de la persona (R10). Sin llamadas a la IA."""
import hashlib
import json
import re

import pytest

from app.extensions import db
from app.models import Candidato, Evaluacion, Vacante

XSS = "<script>alert(1)</script>"


def _vacante():
    return Vacante.query.filter_by(codigo="ING-IA-2026").first() or Vacante.query.first()


def crear(app, nombre="Ana", estado="evaluado", con_eval=True, **ev):
    with app.app_context():
        v = _vacante()
        c = Candidato(vacante_id=v.id, archivo=f"{nombre}.pdf", nombre=nombre,
                      sha256=hashlib.sha256(nombre.encode()).hexdigest(),
                      pdf=b"%PDF-1.4 x", texto="t" * 300, estado=estado)
        db.session.add(c)
        db.session.flush()
        if con_eval:
            cod = v.perfil["obligatorios"][0]["codigo"]
            datos = dict(
                candidato_id=c.id, avanza=True, puntaje=7,
                criterios_json=json.dumps([{"codigo": cod, "tipo": "O", "cumple_ia": True,
                                            "evidencia": "servicios en Python",
                                            "verificada": True, "cuenta": True}]),
                alertas_json=json.dumps(["Orden dirigida a la IA"]),
                resumen="Resumen neutral", modelo="modelo-falso",
                tokens_entrada=10, tokens_salida=5, duracion_s=0.1)
            datos.update(ev)
            db.session.add(Evaluacion(**datos))
        db.session.commit()
        return c.id


def token(client, cid):
    html = client.get(f"/candidato/{cid}").get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def estado_ev(app, cid):
    with app.app_context():
        e = Evaluacion.query.filter_by(candidato_id=cid).first()
        return e.decision, e.comentario


def post(client, cid, decision="entrevistar", comentario="Buen perfil", tok=None):
    data = {"csrf_token": tok if tok is not None else token(client, cid)}
    if decision is not None:
        data["decision"] = decision
    if comentario is not None:
        data["comentario"] = comentario
    return client.post(f"/candidato/{cid}/decision", data=data)


def sin_guardar(app, cid):
    d, c = estado_ev(app, cid)
    assert d == "sin revisar" and not c


@pytest.mark.parametrize("vacio", ["", "   ", " \n\t "])
def test_sin_comentario_422(app, client_autenticado, vacio):
    cid = crear(app)
    r = post(client_autenticado, cid, "entrevistar", vacio)
    assert r.status_code == 422
    assert "El comentario es obligatorio" in r.get_data(as_text=True)
    sin_guardar(app, cid)


def test_422_conserva_lo_escrito(app, client_autenticado):
    cid = crear(app)
    r = post(client_autenticado, cid, "descartar", "x" * 2001)
    html = r.get_data(as_text=True)
    assert r.status_code == 422
    assert "x" * 2001 in html
    assert re.search(r'value="descartar"\s+checked', html)


@pytest.mark.parametrize("dec", [None, "", "contratar", "ENTREVISTAR"])
def test_decision_invalida_422(app, client_autenticado, dec):
    cid = crear(app)
    r = post(client_autenticado, cid, dec, "comentario")
    assert r.status_code == 422
    assert "Elija una decisión" in r.get_data(as_text=True)
    sin_guardar(app, cid)


def test_comentario_largo_422_y_limite_exacto_ok(app, client_autenticado):
    cid = crear(app)
    r = post(client_autenticado, cid, "entrevistar", "a" * 2001)
    assert r.status_code == 422
    assert "2000" in r.get_data(as_text=True)
    sin_guardar(app, cid)
    assert post(client_autenticado, cid, "entrevistar", "a" * 2000).status_code == 302


@pytest.mark.parametrize("tok", [None, "", "incorrecto"])
def test_csrf_403(app, client_autenticado, tok):
    cid = crear(app)
    token(client_autenticado, cid)  # deja un token en la sesion
    data = {"decision": "entrevistar", "comentario": "ok"}
    if tok is not None:
        data["csrf_token"] = tok
    r = client_autenticado.post(f"/candidato/{cid}/decision", data=data)
    assert r.status_code == 403
    sin_guardar(app, cid)


def test_csrf_sin_token_en_sesion_403(app, client_autenticado):
    cid = crear(app)
    r = client_autenticado.post(f"/candidato/{cid}/decision",
                                data={"csrf_token": "x", "decision": "descartar", "comentario": "ok"})
    assert r.status_code == 403
    sin_guardar(app, cid)


@pytest.mark.parametrize("dec", ["entrevistar", "descartar"])
def test_caso_feliz(app, client_autenticado, dec):
    cid = crear(app, nombre="Zoe")
    r = post(client_autenticado, cid, dec, "  Comentario válido  ")
    assert r.status_code == 302
    assert r.headers["Location"].endswith(f"/candidato/{cid}")
    assert estado_ev(app, cid) == (dec, "Comentario válido")
    html = client_autenticado.get(f"/candidato/{cid}").get_data(as_text=True)
    assert html.count("Decisión guardada") == 1
    assert "Comentario válido" in html
    assert "Decisión guardada" not in client_autenticado.get(f"/candidato/{cid}").get_data(as_text=True)
    with app.app_context():
        vid = _vacante().id
    inicio = client_autenticado.get(f"/?vacante={vid}").get_data(as_text=True)
    assert dec in inicio


def test_redecidir_sobrescribe(app, client_autenticado):
    cid = crear(app)
    post(client_autenticado, cid, "entrevistar", "primero")
    post(client_autenticado, cid, "descartar", "segundo")
    assert estado_ev(app, cid) == ("descartar", "segundo")


def test_sin_evaluacion_409(app, client_autenticado):
    cid = crear(app, estado="pendiente", con_eval=False)
    otro = crear(app, nombre="Otro")
    tok = token(client_autenticado, otro)
    r = client_autenticado.post(f"/candidato/{cid}/decision",
                                data={"csrf_token": tok, "decision": "entrevistar", "comentario": "ok"})
    assert r.status_code == 409
    with app.app_context():
        assert Evaluacion.query.filter_by(candidato_id=cid).count() == 0


def test_inexistente_404(app, client_autenticado):
    cid = crear(app)
    tok = token(client_autenticado, cid)
    assert client_autenticado.get("/candidato/99999").status_code == 404
    r = client_autenticado.post("/candidato/99999/decision",
                                data={"csrf_token": tok, "decision": "entrevistar", "comentario": "ok"})
    assert r.status_code == 404


def test_detalle_contenido(app, client_autenticado):
    cid = crear(app)
    with app.app_context():
        o = _vacante().perfil["obligatorios"][0]
    r = client_autenticado.get(f"/candidato/{cid}")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert f"/candidato/{cid}/pdf" in html and "<iframe" in html
    assert o["codigo"] in html
    assert o["descripcion"][:20] in html
    assert "servicios en Python" in html
    assert "Orden dirigida a la IA" in html
    assert "Resumen neutral" in html
    assert "modelo-falso" in html and "10 tokens de entrada" in html and "5 tokens de salida" in html
    assert "Avanza" in html and "7 puntos" in html


def test_detalle_escapa_html(app, client_autenticado):
    cid = crear(app, nombre=XSS, alertas_json=json.dumps([XSS]), resumen=XSS, comentario=XSS)
    html = client_autenticado.get(f"/candidato/{cid}").get_data(as_text=True)
    assert XSS not in html
    assert "&lt;script&gt;" in html


def test_comentario_malicioso_se_escapa_tras_guardar(app, client_autenticado):
    cid = crear(app)
    post(client_autenticado, cid, "entrevistar", XSS)
    html = client_autenticado.get(f"/candidato/{cid}").get_data(as_text=True)
    assert XSS not in html


def test_detalle_pendiente_y_sin_texto(app, client_autenticado):
    p = crear(app, nombre="Pen", estado="pendiente", con_eval=False)
    s = crear(app, nombre="Sin", estado="sin_texto", con_eval=False)
    hp = client_autenticado.get(f"/candidato/{p}").get_data(as_text=True)
    hs = client_autenticado.get(f"/candidato/{s}").get_data(as_text=True)
    assert "Pendiente de evaluar" in hp and 'name="decision"' not in hp
    assert "PDF sin texto" in hs and 'name="decision"' not in hs


def test_sin_sesion_redirige_login(app, client):
    cid = crear(app)
    r = client.get(f"/candidato/{cid}")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    r = client.post(f"/candidato/{cid}/decision",
                    data={"decision": "entrevistar", "comentario": "ok"})
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    sin_guardar(app, cid)
