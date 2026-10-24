"""Configuración de la app: solo variables de entorno (o el dict `config` en tests)."""
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.pool import NullPool, StaticPool

RAIZ = Path(__file__).resolve().parents[1]
VERDADEROS = {"1", "true", "yes", "si", "sí", "on"}


class ConfiguracionError(RuntimeError):
    """Falta una variable de entorno obligatoria."""


def normalizar_url(url):
    """Convierte postgres:// y postgresql:// a postgresql+psycopg://."""
    for prefijo in ("postgres://", "postgresql://"):
        if url.startswith(prefijo):
            return "postgresql+psycopg://" + url[len(prefijo):]
    return url


def es_postgres(url):
    return url.startswith("postgresql")


def cargar_configuracion(config=None):
    config = dict(config or {})
    if (RAIZ / ".env").exists():
        load_dotenv(RAIZ / ".env")

    def valor(clave):
        return config.get(clave) or os.environ.get(clave)

    faltan = [c for c in ("SECRET_KEY", "APP_PASSWORD") if not valor(c)]
    if faltan:
        raise ConfiguracionError(
            "Falta configurar " + " y ".join(faltan) + ". Defínelas como variables "
            "de entorno (en local, en el archivo .env; en la nube, en Vercel). "
            "Revisa .env.example."
        )

    url = config.get("SQLALCHEMY_DATABASE_URI") or valor("DATABASE_URL")
    en_memoria = False
    if not url:
        if str(valor("BD_EN_MEMORIA") or "").strip().lower() in VERDADEROS:
            url = "sqlite://"
            en_memoria = True
        else:
            raise ConfiguracionError(
                "Falta la base de datos. Defina DATABASE_URL (en local la define "
                "compose.yaml para el servicio web; en producción debe apuntar al pooler "
                "de Supabase, puerto 6543), o BD_EN_MEMORIA=1 para una demostración con "
                "datos efímeros que se pierden al reiniciar."
            )
    url = normalizar_url(url)

    resultado = {
        "SECRET_KEY": valor("SECRET_KEY"),
        "APP_PASSWORD": valor("APP_PASSWORD"),
        "SQLALCHEMY_DATABASE_URI": url,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SECURE": bool(os.environ.get("VERCEL")),
        "BD_EN_MEMORIA_ACTIVA": en_memoria,
    }
    if en_memoria:
        # Una sola conexión compartida por todos los hilos: la base vive en memoria.
        resultado["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "poolclass": StaticPool,
            "connect_args": {"check_same_thread": False},
        }
    if es_postgres(url):
        resultado["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "poolclass": NullPool,
            "connect_args": {"prepare_threshold": None},
        }
    # El dict recibido (tests) sobreescribe, salvo la URL ya normalizada.
    for clave, v in config.items():
        if clave not in ("SQLALCHEMY_DATABASE_URI", "DATABASE_URL"):
            resultado[clave] = v
    return resultado
