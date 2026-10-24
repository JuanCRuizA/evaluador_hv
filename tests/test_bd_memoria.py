"""Modo opt-in BD_EN_MEMORIA: base SQLite en memoria compartida (demostración)."""
import io
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool

from app import config as cfg
from app import create_app
from app.config import ConfiguracionError, cargar_configuracion
from app.models import Candidato, Vacante
from app.services import vacantes  # noqa: F401  (fija DIR_PERFILES antes de parchear RAIZ)
from tests.conftest import CLAVE
from tests.helpers import cabeceras

OPCIONES_MEMORIA = {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
VERDADEROS = ["1", "true", "yes", "si", "sí", "on", " TRUE ", "Sí"]
FALSOS = ["0", "", "no", "false"]


@pytest.fixture(autouse=True)
def entorno_limpio(monkeypatch, tmp_path):
    """Sin DATABASE_URL del contenedor, sin .env real (RAIZ apunta a una carpeta vacía)."""
    monkeypatch.setattr(cfg, "RAIZ", tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("BD_EN_MEMORIA", raising=False)
    monkeypatch.setenv("SECRET_KEY", "secreto-de-prueba")
    monkeypatch.setenv("APP_PASSWORD", CLAVE)


def pdf_sintetico(texto):
    """PDF mínimo válido de una página con texto, generado en memoria."""
    contenido = f"BT /F1 10 Tf 20 700 Td ({texto}) Tj ET".encode("latin-1")
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 2000 800] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(contenido) + contenido + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    salida = bytearray(b"%PDF-1.4\n")
    posiciones = []
    for i, obj in enumerate(objetos, start=1):
        posiciones.append(len(salida))
        salida += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(salida)
    salida += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objetos) + 1)
    for p in posiciones:
        salida += b"%010d 00000 n \n" % p
    salida += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objetos) + 1, xref)
    return bytes(salida)


TEXTO_CV = ("Analista de datos con experiencia en Python, SQL y visualizacion de informacion. "
            "Ha trabajado en proyectos de analitica, limpieza de datos y reportes para areas "
            "comerciales, con enfoque en calidad y documentacion de procesos internos.")


# ---- (1) valores verdaderos ----
@pytest.mark.parametrize("valor", VERDADEROS)
def test_valores_verdaderos_activan_memoria(monkeypatch, valor):
    monkeypatch.setenv("BD_EN_MEMORIA", valor)
    c = cargar_configuracion()
    assert c["SQLALCHEMY_DATABASE_URI"] == "sqlite://"
    assert c["BD_EN_MEMORIA_ACTIVA"] is True
    assert c["SQLALCHEMY_ENGINE_OPTIONS"]["poolclass"] is StaticPool
    assert c["SQLALCHEMY_ENGINE_OPTIONS"]["connect_args"] == {"check_same_thread": False}


@pytest.mark.parametrize("valor", ["1", " TRUE ", "Sí"])
def test_create_app_en_memoria_crea_tablas_y_carga_perfiles(monkeypatch, valor):
    monkeypatch.setenv("BD_EN_MEMORIA", valor)
    app = create_app({"TESTING": True})
    assert app.config["BD_EN_MEMORIA_ACTIVA"] is True
    n_perfiles = len(list((Path(cfg.__file__).resolve().parents[1] / "insumos" / "perfiles")
                          .glob("*.json")))
    assert n_perfiles == 2
    with app.app_context():
        assert Vacante.query.count() == n_perfiles
        assert Candidato.query.count() == 0


# ---- (2) valores falsos o ausente ----
@pytest.mark.parametrize("valor", FALSOS)
def test_valores_falsos_sin_database_url_fallan(monkeypatch, valor):
    monkeypatch.setenv("BD_EN_MEMORIA", valor)
    with pytest.raises(ConfiguracionError) as e:
        cargar_configuracion()
    assert "DATABASE_URL" in str(e.value) and "BD_EN_MEMORIA" in str(e.value)


def test_variable_ausente_falla():
    with pytest.raises(ConfiguracionError) as e:
        cargar_configuracion()
    assert "DATABASE_URL" in str(e.value) and "BD_EN_MEMORIA" in str(e.value)


def test_create_app_sin_opcion_falla():
    with pytest.raises(ConfiguracionError):
        create_app({"TESTING": True})


# ---- (3) DATABASE_URL ----
def test_database_url_no_vacia_gana(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("BD_EN_MEMORIA", "1")
    c = cargar_configuracion()
    assert c["BD_EN_MEMORIA_ACTIVA"] is False
    assert c["SQLALCHEMY_DATABASE_URI"] == "sqlite:///:memory:"
    assert "SQLALCHEMY_ENGINE_OPTIONS" not in c


def test_database_url_vacia_cuenta_como_ausente(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("BD_EN_MEMORIA", "1")
    c = cargar_configuracion()
    assert c["BD_EN_MEMORIA_ACTIVA"] is True
    assert c["SQLALCHEMY_DATABASE_URI"] == "sqlite://"


def test_database_url_vacia_sin_memoria_falla(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    with pytest.raises(ConfiguracionError):
        cargar_configuracion()


# ---- (4) SQLALCHEMY_DATABASE_URI en el dict ----
def test_uri_en_dict_se_respeta(monkeypatch):
    monkeypatch.setenv("BD_EN_MEMORIA", "1")
    c = cargar_configuracion({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    assert c["BD_EN_MEMORIA_ACTIVA"] is False
    assert c["SQLALCHEMY_DATABASE_URI"] == "sqlite:///:memory:"


# ---- (5) banner ----
def test_banner_solo_en_modo_memoria(monkeypatch):
    monkeypatch.setenv("BD_EN_MEMORIA", "1")
    app = create_app({"TESTING": True})
    assert "Modo demostración" in app.test_client().get("/login").get_data(as_text=True)


def test_sin_banner_con_config_normal(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert "Modo demostración" not in r.get_data(as_text=True)


# ---- (6) persistencia entre peticiones ----
def test_persistencia_entre_peticiones(monkeypatch):
    monkeypatch.setenv("BD_EN_MEMORIA", "1")
    app = create_app({"TESTING": True})
    client = app.test_client()
    assert client.post("/login", data={"password": CLAVE}).status_code == 302
    with app.app_context():
        vid = Vacante.query.first().id
    r = client.post(
        "/cargar",
        data={"vacante": str(vid),
              "archivo": (io.BytesIO(pdf_sintetico(TEXTO_CV)), "Maria_Prueba.pdf",
                          "application/pdf")},
        content_type="multipart/form-data",
        headers=cabeceras(client),
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["ok"] is True
    with app.app_context():
        nombre = Candidato.query.one().nombre
    html = client.get(f"/?vacante={vid}").get_data(as_text=True)
    assert nombre in html


# ---- (7) nada en disco ----
def test_no_escribe_en_disco(monkeypatch, tmp_path):
    monkeypatch.setenv("BD_EN_MEMORIA", "1")
    trabajo = tmp_path / "trabajo"
    trabajo.mkdir()
    monkeypatch.chdir(trabajo)
    app = create_app({"TESTING": True})
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    assert uri == "sqlite://" and "/" not in uri.replace("sqlite://", "")
    assert list(trabajo.iterdir()) == []
    assert not list(Path(cfg.__file__).resolve().parents[1].glob("*.db"))
    with app.app_context():
        assert app.extensions["sqlalchemy"].engine.url.database in (None, "", ":memory:")
