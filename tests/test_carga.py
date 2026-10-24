"""R1 por HTTP: carga de PDF, duplicados, límites y descarga."""
import io
import re

import pytest
from markupsafe import escape

from app.models import Candidato, Vacante
from app.services import documentos
from app.services.candidatos import MAX_BYTES
from tests.helpers import cabeceras, pdf_con_texto, pdf_sin_texto

CORREO = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


@pytest.fixture
def vacante_id(app):
    with app.app_context():
        v = Vacante.query.first()
        assert v is not None, "el arranque debe cargar vacantes de insumos/perfiles"
        return v.id


def subir(client, vid, nombre, contenido, ctype="application/pdf"):
    return client.post(
        "/cargar",
        data={"vacante": str(vid), "archivo": (io.BytesIO(contenido), nombre, ctype)},
        content_type="multipart/form-data",
        headers=cabeceras(client),
    )


def fake_pdf(n):
    return b"%PDF-1.4\n" + b"0" * (n - 9)


def test_carga_pdf_real(app, client_autenticado, vacante_id):
    archivo = pdf_con_texto(MAX_BYTES)
    datos = archivo.read_bytes()
    r = subir(client_autenticado, vacante_id, archivo.name, datos)
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    with app.app_context():
        c = Candidato.query.one()
        assert c.estado == "pendiente"
        assert bytes(c.pdf) == datos
        assert not CORREO.search(c.texto)
        assert c.nombre == documentos.nombre_candidato(archivo.name)
        assert c.vacante_id == vacante_id


def test_duplicado_409(app, client_autenticado, vacante_id):
    datos = pdf_con_texto(MAX_BYTES).read_bytes()
    assert subir(client_autenticado, vacante_id, "a.pdf", datos).status_code == 200
    r = subir(client_autenticado, vacante_id, "a.pdf", datos)
    assert r.status_code == 409
    assert r.get_json()["ok"] is False
    # misma huella con otro nombre también se rechaza
    assert subir(client_autenticado, vacante_id, "otro.pdf", datos).status_code == 409
    with app.app_context():
        assert Candidato.query.count() == 1


def test_pdf_sin_texto(app, client_autenticado, vacante_id):
    archivo = pdf_sin_texto(MAX_BYTES)
    r = subir(client_autenticado, vacante_id, archivo.name, archivo.read_bytes())
    assert r.status_code == 200
    with app.app_context():
        assert Candidato.query.one().estado == "sin_texto"


def test_supera_4mb_413(app, client_autenticado, vacante_id):
    r = subir(client_autenticado, vacante_id, "grande.pdf", fake_pdf(MAX_BYTES + 1))
    assert r.status_code == 413
    assert r.get_json()["ok"] is False
    with app.app_context():
        assert Candidato.query.count() == 0


def test_exactamente_4mb_no_es_413(client_autenticado, vacante_id):
    r = subir(client_autenticado, vacante_id, "justo.pdf", fake_pdf(MAX_BYTES))
    assert r.status_code != 413


def test_mucho_mayor_que_max_content_length_413_json(app, client_autenticado, vacante_id):
    limite = app.config.get("MAX_CONTENT_LENGTH")
    tam = (limite or 5 * 1024 * 1024) + 2 * 1024 * 1024
    r = subir(client_autenticado, vacante_id, "enorme.pdf", fake_pdf(tam))
    assert r.status_code == 413
    assert r.is_json, "413 por MAX_CONTENT_LENGTH debe responder JSON"
    assert r.get_json()["ok"] is False


def test_txt_422(client_autenticado, vacante_id):
    r = subir(client_autenticado, vacante_id, "notas.txt", b"hola mundo", "text/plain")
    assert r.status_code == 422
    assert r.get_json()["ok"] is False


def test_pdf_falso_422(client_autenticado, vacante_id):
    r = subir(client_autenticado, vacante_id, "falso.pdf", b"no soy un pdf")
    assert r.status_code == 422


def test_sin_archivo_422(client_autenticado, vacante_id):
    r = client_autenticado.post(
        "/cargar", data={"vacante": str(vacante_id)}, headers=cabeceras(client_autenticado))
    assert r.status_code == 422


def test_sin_sesion_redirige_al_login(app, client, vacante_id):
    r = subir(client, vacante_id, "x.pdf", fake_pdf(1000))
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]
    with app.app_context():
        assert Candidato.query.count() == 0


def test_vacante_inexistente_404(client_autenticado):
    r = subir(client_autenticado, 99999, "x.pdf", fake_pdf(1000))
    assert r.status_code == 404


def test_descarga_pdf_mismos_bytes(app, client_autenticado, vacante_id):
    archivo = pdf_con_texto(MAX_BYTES)
    datos = archivo.read_bytes()
    subir(client_autenticado, vacante_id, archivo.name, datos)
    with app.app_context():
        cid = Candidato.query.one().id
    r = client_autenticado.get(f"/candidato/{cid}/pdf")
    assert r.status_code == 200
    assert r.mimetype == "application/pdf"
    assert r.data == datos
    assert client_autenticado.get("/candidato/99999/pdf").status_code == 404


def test_descarga_requiere_sesion(client):
    assert client.get("/candidato/1/pdf").status_code == 302


def test_inicio_lista_candidatos(client_autenticado, vacante_id):
    sin_texto, con_texto = pdf_sin_texto(MAX_BYTES), pdf_con_texto(MAX_BYTES)
    subir(client_autenticado, vacante_id, sin_texto.name, sin_texto.read_bytes())
    subir(client_autenticado, vacante_id, con_texto.name, con_texto.read_bytes())
    r = client_autenticado.get(f"/?vacante={vacante_id}")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert str(escape(documentos.nombre_candidato(sin_texto.name))) in html
    assert str(escape(documentos.nombre_candidato(con_texto.name))) in html
    assert "PDF sin texto" in html
