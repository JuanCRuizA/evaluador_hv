"""Token CSRF simple guardado en la sesión (reutilizable por cualquier formulario)."""
import hmac
import secrets
from functools import wraps

from flask import jsonify, request, session

CLAVE = "_csrf_token"


def obtener_token():
    """Devuelve el token de la sesión; lo crea si no existe."""
    token = session.get(CLAVE)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CLAVE] = token
    return token


def token_valido(recibido):
    esperado = session.get(CLAVE)
    if not esperado or not recibido:
        return False
    return hmac.compare_digest(str(esperado).encode(), str(recibido).encode())


def exigir_fetch_csrf(vista):
    """Decorador para rutas JSON llamadas con fetch: exige X-Requested-With: fetch y el token
    en X-CSRF-Token (comparación en tiempo constante). Responde 403 JSON si falta o no coincide."""
    @wraps(vista)
    def envuelta(*args, **kwargs):
        if request.headers.get("X-Requested-With") != "fetch":
            return jsonify(ok=False, error="Petición no permitida", motivo="Petición no permitida"), 403
        if not token_valido(request.headers.get("X-CSRF-Token")):
            msg = "Sesión vencida, recargue la página"
            return jsonify(ok=False, error=msg, motivo=msg, csrf=True), 403
        return vista(*args, **kwargs)
    return envuelta
