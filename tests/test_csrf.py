"""CSRF de POST /cargar y POST /evaluar: 403 JSON sin efectos; un solo archivo por peticion."""
import hashlib
import io

import pytest

from app.extensions import db
from app.models import Candidato, Evaluacion, Vacante
from app.services import agente_evaluador
from tests.conftest import CLAVE
from tests.helpers import cabeceras, token_csrf

PDF = b"%PDF-1.4\n" + b"0" * 500


@pytest.fixture
def vid(app):
    with app.app_context():
        return Vacante.query.first().id


@pytest.fixture
def llamadas_ia(monkeypatch):
    llamadas = []

    def espia(perfil, texto):
        llamadas.append(1)
        raise AssertionError("no debia llamarse a la IA")

    monkeypatch.setattr(agente_evaluador, "evaluar", espia)
    return llamadas


def _cargar(client, vid, headers, archivos=None):
    archivos = archivos or [("a.pdf", PDF)]
    data = {"vacante": str(vid)}
    if len(archivos) == 1:
        data["archivo"] = (io.BytesIO(archivos[0][1]), archivos[0][0], "application/pdf")
    else:
        data["archivo"] = [(io.BytesIO(c), n, "application/pdf") for n, c in archivos]
    return client.post("/cargar", data=data, content_type="multipart/form-data", headers=headers)


def _candidato(app, vid):
    with app.app_context():
        c = Candidato(vacante_id=vid, archivo="x.pdf", nombre="X",
                      sha256=hashlib.sha256(b"x").hexdigest(), pdf=b"%PDF x",
                      texto="Python " * 50, estado="pendiente")
        db.session.add(c)
        db.session.commit()
        return c.id


def _sin_efectos_carga(app):
    with app.app_context():
        assert Candidato.query.count() == 0


def _sin_efectos_eval(app, cid):
    with app.app_context():
        assert Evaluacion.query.count() == 0
        assert db.session.get(Candidato, cid).estado == "pendiente"


def test_meta_csrf_presente_en_pagina_autenticada(client_autenticado):
    assert len(token_csrf(client_autenticado)) >= 20


def test_sin_sesion_no_hay_token(client):
    assert token_csrf(client) == ""


@pytest.mark.parametrize("headers", [
    {"X-Requested-With": "fetch"},
    {"X-Requested-With": "fetch", "X-CSRF-Token": "malo"},
    {"X-Requested-With": "fetch", "X-CSRF-Token": ""},
])
def test_cargar_sin_token_o_token_malo_403(app, client_autenticado, vid, headers):
    r = _cargar(client_autenticado, vid, headers)
    assert r.status_code == 403
    assert r.is_json and r.get_json()["ok"] is False
    _sin_efectos_carga(app)


def test_cargar_sin_cabecera_fetch_403(app, client_autenticado, vid):
    token = token_csrf(client_autenticado)
    r = _cargar(client_autenticado, vid, {"X-CSRF-Token": token})
    assert r.status_code == 403 and r.is_json and r.get_json()["ok"] is False
    _sin_efectos_carga(app)


def test_cargar_cabecera_distinta_de_fetch_403(app, client_autenticado, vid):
    h = {"X-Requested-With": "XMLHttpRequest", "X-CSRF-Token": token_csrf(client_autenticado)}
    assert _cargar(client_autenticado, vid, h).status_code == 403
    _sin_efectos_carga(app)


def test_cargar_token_de_otra_sesion_403(app, client_autenticado, vid):
    otro = app.test_client()
    otro.post("/login", data={"password": CLAVE})
    h = {"X-Requested-With": "fetch", "X-CSRF-Token": token_csrf(otro)}
    assert _cargar(client_autenticado, vid, h).status_code == 403
    _sin_efectos_carga(app)


def test_cargar_con_token_valido_pasa_csrf(client_autenticado, vid):
    r = _cargar(client_autenticado, vid, cabeceras(client_autenticado))
    assert r.status_code != 403


@pytest.mark.parametrize("headers", [
    {"X-Requested-With": "fetch"},
    {"X-Requested-With": "fetch", "X-CSRF-Token": "malo"},
    {},
])
def test_evaluar_sin_token_malo_o_sin_cabecera_403(app, client_autenticado, vid, llamadas_ia, headers):
    cid = _candidato(app, vid)
    r = client_autenticado.post(f"/evaluar/{cid}", headers=headers)
    assert r.status_code == 403
    assert r.is_json and r.get_json()["ok"] is False
    assert llamadas_ia == []
    _sin_efectos_eval(app, cid)


def test_evaluar_solo_token_sin_cabecera_403(app, client_autenticado, vid, llamadas_ia):
    cid = _candidato(app, vid)
    r = client_autenticado.post(f"/evaluar/{cid}",
                                headers={"X-CSRF-Token": token_csrf(client_autenticado)})
    assert r.status_code == 403 and r.is_json
    assert llamadas_ia == []
    _sin_efectos_eval(app, cid)


def test_cargar_dos_archivos_422_sin_guardar(app, client_autenticado, vid):
    r = _cargar(client_autenticado, vid, cabeceras(client_autenticado),
                [("a.pdf", PDF), ("b.pdf", PDF + b"1")])
    assert r.status_code == 422
    assert r.get_json()["ok"] is False
    _sin_efectos_carga(app)


def test_cargar_dos_campos_distintos_422_sin_guardar(app, client_autenticado, vid):
    data = {"vacante": str(vid),
            "archivo": (io.BytesIO(PDF), "a.pdf", "application/pdf"),
            "otro": (io.BytesIO(PDF + b"1"), "b.pdf", "application/pdf")}
    r = client_autenticado.post("/cargar", data=data, content_type="multipart/form-data",
                                headers=cabeceras(client_autenticado))
    assert r.status_code == 422
    _sin_efectos_carga(app)
