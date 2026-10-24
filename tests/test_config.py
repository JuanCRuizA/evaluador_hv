import pytest

from app import config as cfg
from app.config import ConfiguracionError, cargar_configuracion, normalizar_url

BASE = {
    "SQLALCHEMY_DATABASE_URI": "sqlite://",
    "SECRET_KEY": "test",
    "APP_PASSWORD": "clave-de-prueba",
}


@pytest.fixture(autouse=True)
def entorno_limpio(monkeypatch):
    for v in ("SECRET_KEY", "APP_PASSWORD", "DATABASE_URL"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(cfg, "load_dotenv", lambda *a, **k: None)


def test_config_completa_ok():
    assert cargar_configuracion(BASE)["APP_PASSWORD"] == "clave-de-prueba"


@pytest.mark.parametrize("falta", ["APP_PASSWORD", "SECRET_KEY"])
def test_falta_variable_obligatoria(falta):
    c = {k: v for k, v in BASE.items() if k != falta}
    with pytest.raises(ConfiguracionError) as e:
        cargar_configuracion(c)
    assert falta in str(e.value)


@pytest.mark.parametrize("prefijo", ["postgres://", "postgresql://"])
def test_normaliza_url_postgres(prefijo):
    url = prefijo + "u:p@host:6543/db"
    assert normalizar_url(url) == "postgresql+psycopg://u:p@host:6543/db"
    r = cargar_configuracion({**BASE, "SQLALCHEMY_DATABASE_URI": url})
    assert r["SQLALCHEMY_DATABASE_URI"] == "postgresql+psycopg://u:p@host:6543/db"
    opts = r["SQLALCHEMY_ENGINE_OPTIONS"]
    assert opts["connect_args"] == {"prepare_threshold": None}
    from sqlalchemy.pool import NullPool

    assert opts["poolclass"] is NullPool


def test_sqlite_no_se_modifica():
    r = cargar_configuracion(BASE)
    assert r["SQLALCHEMY_DATABASE_URI"] == "sqlite://"
    assert "SQLALCHEMY_ENGINE_OPTIONS" not in r
