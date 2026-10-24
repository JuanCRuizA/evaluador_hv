import hmac
from urllib.parse import urlparse

from flask import Blueprint, current_app, redirect, render_template, request, session, url_for

bp = Blueprint("auth", __name__)


def _es_ruta_local(destino):
    if not destino or not destino.startswith("/") or destino.startswith("//") or "\\" in destino:
        return False
    partes = urlparse(destino)
    return not partes.scheme and not partes.netloc


@bp.before_app_request
def exigir_sesion():
    if request.endpoint == "auth.login" or session.get("autenticado"):
        return None
    destino = request.full_path.rstrip("?") if request.method == "GET" else ""
    if destino and destino != "/":
        return redirect(url_for("auth.login", next=destino))
    return redirect(url_for("auth.login"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    siguiente = request.values.get("next", "")
    if not _es_ruta_local(siguiente):
        siguiente = ""
    error = None
    if request.method == "POST":
        clave = request.form.get("password", "")
        esperada = current_app.config["APP_PASSWORD"]
        if hmac.compare_digest(clave.encode(), esperada.encode()):
            session.clear()
            session["autenticado"] = True
            return redirect(siguiente or url_for("main.index"))
        error = "Contraseña incorrecta."
    return render_template("login.html", error=error, siguiente=siguiente), (401 if error else 200)


@bp.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
