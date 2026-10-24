import pytest
from sqlalchemy.pool import StaticPool

from app import create_app

CLAVE = "clave-de-prueba"
MENSAJE_GUARD = "Los tests no deben llamar a la API"


class LlamadaApiProhibida(BaseException):
    """Hereda de BaseException a proposito: un `except Exception` del codigo bajo prueba
    no puede tragarse el guard ni convertirlo en un 502 silencioso."""


@pytest.fixture(autouse=True)
def sin_api_real(monkeypatch):
    """Guard: ningun test llega a la API. Quien necesite el agente debe sustituir
    agente_evaluador.evaluar o agente_evaluador._ejecutar."""
    from app.services import agente_evaluador

    # Variables de proveedores anteriores: ni existen ni cuentan.
    for nombre in ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL",
                   "GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_BASE_URL",
                   "GEMINI_REASONING_EFFORT"):
        monkeypatch.delenv(nombre, raising=False)
    for nombre in ("OPENROUTER_BASE_URL", "OPENROUTER_REASONING_EFFORT",
                   "OPENROUTER_MAX_TOKENS"):
        monkeypatch.delenv(nombre, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "clave-de-prueba-invalida")
    # Con modelo configurado, evaluar() llega a _ejecutar (y al guard) en vez de fallar antes
    # con ErrorConfiguracionIA, que la app convertiria en un 502 silencioso.
    monkeypatch.setenv("OPENROUTER_MODEL", "modelo-guard")
    monkeypatch.delenv("MAX_TURNOS_AGENTE", raising=False)
    monkeypatch.delenv("TIMEOUT_AGENTE_S", raising=False)

    def _prohibido(*args, **kwargs):
        raise LlamadaApiProhibida(MENSAJE_GUARD)

    monkeypatch.setattr(agente_evaluador, "_ejecutar", _prohibido)
    for nombre in ("run", "run_sync", "run_streamed"):
        monkeypatch.setattr(agente_evaluador.Runner, nombre, _prohibido)


@pytest.fixture
def app():
    return create_app(
        {
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "SQLALCHEMY_ENGINE_OPTIONS": {
                "poolclass": StaticPool,
                "connect_args": {"check_same_thread": False},
            },
            "SECRET_KEY": "test",
            "APP_PASSWORD": CLAVE,
            "TESTING": True,
        }
    )


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def client_autenticado(client):
    client.post("/login", data={"password": CLAVE})
    return client
