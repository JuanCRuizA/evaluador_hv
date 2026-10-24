import io

from flask import (Blueprint, abort, flash, jsonify, redirect, render_template,
                   request, send_file, url_for)

from app.services import candidatos, csrf, decisiones, evaluacion, vacantes

bp = Blueprint("main", __name__)


@bp.get("/")
def index():
    vacante = vacantes.obtener_vacante(request.args.get("vacante", type=int))
    return render_template(
        "inicio.html",
        vacantes=vacantes.listar_vacantes(),
        vacante=vacante,
        conteos=vacantes.contadores(vacante.id) if vacante else None,
        rk=evaluacion.ranking(vacante.id) if vacante else None,
    )


@bp.post("/evaluar/<int:candidato_id>")
@csrf.exigir_fetch_csrf
def evaluar(candidato_id):
    try:
        return jsonify(evaluacion.evaluar_candidato(candidato_id))
    except evaluacion.EvaluacionError as e:
        return jsonify(ok=False, error=e.motivo), e.codigo


@bp.get("/cargar")
def cargar():
    vacante = vacantes.obtener_vacante(request.args.get("vacante", type=int))
    return render_template("cargar.html", vacantes=vacantes.listar_vacantes(), vacante=vacante)


@bp.post("/cargar")
@csrf.exigir_fetch_csrf
def cargar_pdf():
    archivo = request.files.get("archivo")
    nombre = archivo.filename if archivo and archivo.filename else ""
    try:
        candidatos.validar_un_archivo(request.files)
        if archivo is None:
            raise candidatos.CargaError("No se recibió ningún archivo", 422)
        candidatos.guardar_pdf(request.form.get("vacante", type=int), nombre, archivo.read())
    except candidatos.CargaError as e:
        return jsonify(archivo=nombre, ok=False, motivo=e.motivo), e.codigo
    return jsonify(archivo=nombre, ok=True, motivo="guardada")


@bp.app_context_processor
def helpers_csrf():
    return {"csrf_token": csrf.obtener_token}


def _vista_detalle(candidato_id, status=200, **extra):
    datos = decisiones.detalle(candidato_id)
    if datos is None:
        abort(404)
    return render_template("detalle.html", **datos, **extra), status


@bp.get("/candidato/<int:candidato_id>")
def detalle(candidato_id):
    return _vista_detalle(candidato_id)


@bp.post("/candidato/<int:candidato_id>/decision")
def decision(candidato_id):
    if not csrf.token_valido(request.form.get("csrf_token")):
        abort(403)
    decision_f = request.form.get("decision", "")
    comentario = request.form.get("comentario", "")
    try:
        decisiones.registrar_decision(candidato_id, decision_f, comentario)
    except decisiones.DecisionError as e:
        if e.codigo == 404:
            abort(404)
        return _vista_detalle(candidato_id, e.codigo, error=e.motivo,
                              decision_previa=decision_f, comentario_previo=comentario)
    flash("Decisión guardada")
    return redirect(url_for("main.detalle", candidato_id=candidato_id))


@bp.get("/candidato/<int:candidato_id>/pdf")
def pdf(candidato_id):
    candidato = candidatos.obtener_candidato(candidato_id)
    if candidato is None:
        abort(404)
    respuesta = send_file(
        io.BytesIO(candidato.pdf),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=candidato.archivo,
    )
    respuesta.headers["X-Content-Type-Options"] = "nosniff"
    respuesta.headers["Content-Security-Policy"] = "sandbox"
    return respuesta
