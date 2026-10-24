"""Detalle de un candidato y decisión de la persona (R10)."""
import json

from app.extensions import db
from app.models import Candidato, Evaluacion, Vacante

DECISIONES = ("entrevistar", "descartar")
MAX_COMENTARIO = 2000


class DecisionError(Exception):
    def __init__(self, motivo, codigo=422):
        super().__init__(motivo)
        self.motivo = motivo
        self.codigo = codigo


def _lista(valor):
    try:
        datos = json.loads(valor) if valor else []
    except ValueError:
        return []
    return datos if isinstance(datos, list) else []


def _evaluacion(candidato_id):
    return (Evaluacion.query.filter_by(candidato_id=candidato_id)
            .order_by(Evaluacion.id.desc()).first())


def detalle(candidato_id):
    """Datos para la pantalla de detalle, o None si el candidato no existe."""
    candidato = db.session.get(Candidato, candidato_id)
    if candidato is None:
        return None
    vacante = db.session.get(Vacante, candidato.vacante_id)
    ev = _evaluacion(candidato.id)
    nombre = candidato.nombre or candidato.archivo

    if ev is not None:
        perfil = vacante.perfil if vacante else {}
        descr = {c["codigo"]: c.get("descripcion", "")
                 for c in perfil.get("obligatorios", []) + perfil.get("deseables", [])}
        criterios = []
        for c in _lista(ev.criterios_json):
            criterios.append({
                "codigo": c.get("codigo", ""),
                "tipo": c.get("tipo", ""),
                "descripcion": descr.get(c.get("codigo"), ""),
                "cumple_ia": bool(c.get("cumple_ia")),
                "evidencia": c.get("evidencia") or "",
                "verificada": bool(c.get("verificada")),
                "cuenta": bool(c.get("cuenta")),
            })
        titulo = f"{nombre} · {'Avanza' if ev.avanza else 'No avanza'} · {ev.puntaje or 0} puntos"
    else:
        criterios = []
        titulo = f"{nombre} · " + {"sin_texto": "PDF sin texto",
                                    "evaluando": "Evaluando…"}.get(_estado_ef(candidato),
                                                                   "Pendiente de evaluar")
    return {
        "candidato": candidato,
        "vacante": vacante,
        "evaluacion": ev,
        "titulo": titulo,
        "criterios": criterios,
        "alertas": _lista(ev.alertas_json) if ev else [],
    }


def _estado_ef(candidato):
    from app.services.evaluacion import estado_efectivo
    return estado_efectivo(candidato.estado, candidato.evaluando_desde)


def validar(decision, comentario):
    """Devuelve (decision, comentario_limpio) o lanza DecisionError."""
    decision = (decision or "").strip()
    comentario = (comentario or "").strip()
    if decision not in DECISIONES:
        raise DecisionError("Elija una decisión: entrevistar o descartar")
    if not comentario:
        raise DecisionError("El comentario es obligatorio")
    if len(comentario) > MAX_COMENTARIO:
        raise DecisionError(f"El comentario supera los {MAX_COMENTARIO} caracteres")
    return decision, comentario


def registrar_decision(candidato_id, decision, comentario):
    """Valida y guarda la decisión en la evaluación del candidato."""
    candidato = db.session.get(Candidato, candidato_id)
    if candidato is None:
        raise DecisionError("El candidato no existe", 404)
    decision, comentario = validar(decision, comentario)
    ev = _evaluacion(candidato_id)
    if ev is None:
        raise DecisionError("El candidato aún no tiene evaluación", 409)
    ev.decision = decision
    ev.comentario = comentario
    db.session.commit()
