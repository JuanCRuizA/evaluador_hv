"""Evaluación de un candidato (R4 a R7, R10) y ranking del inicio."""
import json
import logging
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import Candidato, Evaluacion, Vacante
from app.services import agente_evaluador, documentos, reglas


logger = logging.getLogger(__name__)

# Un reclamo "evaluando" más viejo que esto se considera abandonado (el proceso cayó) y
# el candidato vuelve a ser reclamable. Coincide con el límite de 300 s de Vercel.
RECLAMO_CADUCA_S = 300


def _ahora():
    """UTC sin zona (naive): la columna es TIMESTAMP sin zona y SQLite no guarda tz."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive(dt):
    if dt is not None and dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _limite():
    return _ahora() - timedelta(seconds=RECLAMO_CADUCA_S)


def reclamo_vigente(estado, evaluando_desde):
    """True si el candidato está 'evaluando' con un reclamo que no ha caducado."""
    if estado != "evaluando":
        return False
    desde = _naive(evaluando_desde)
    return desde is not None and desde >= _limite()


def estado_efectivo(estado, evaluando_desde):
    """'evaluando' caducado (o sin fecha) cuenta como 'pendiente'."""
    if estado == "evaluando" and not reclamo_vigente(estado, evaluando_desde):
        return "pendiente"
    return estado


class EvaluacionError(Exception):
    def __init__(self, motivo, codigo):
        super().__init__(motivo)
        self.motivo = motivo
        self.codigo = codigo


def _reclamar(candidato_id):
    """UPDATE condicional atómico -> evaluando. True solo si esta petición ganó el reclamo.

    Reclamable: 'pendiente', o 'evaluando' sin fecha o con reclamo caducado."""
    ahora = _ahora()
    filas = (Candidato.query
             .filter(Candidato.id == candidato_id,
                     db.or_(Candidato.estado == "pendiente",
                            db.and_(Candidato.estado == "evaluando",
                                    db.or_(Candidato.evaluando_desde.is_(None),
                                           Candidato.evaluando_desde < _limite()))))
             .update({"estado": "evaluando", "evaluando_desde": ahora},
                     synchronize_session=False))
    db.session.commit()  # el reclamo queda confirmado ANTES de llamar a la IA
    return filas == 1


def _liberar(candidato_id):
    """Devuelve el candidato a 'pendiente' si la evaluación no se pudo completar."""
    try:
        db.session.rollback()
        (Candidato.query.filter_by(id=candidato_id, estado="evaluando")
         .update({"estado": "pendiente", "evaluando_desde": None}, synchronize_session=False))
        db.session.commit()
    except Exception as e:  # no ocultar el error original; solo el tipo
        db.session.rollback()
        logger.warning("No se pudo liberar el candidato: %s", type(e).__name__)


def _estado_actual(candidato_id):
    db.session.expire_all()
    return db.session.query(Candidato.estado).filter_by(id=candidato_id).scalar()


def evaluar_candidato(candidato_id):
    """Evalúa un candidato pendiente. Devuelve dict JSON-able o lanza EvaluacionError.

    Concurrencia: el reclamo atómico (pendiente -> evaluando) garantiza una sola evaluación.
    Recuperación: el reclamo guarda evaluando_desde; si el proceso cae, pasados
    RECLAMO_CADUCA_S segundos el candidato cuenta como pendiente (inicio y botón Evaluar) y
    el mismo UPDATE condicional permite reclamarlo de nuevo. No requiere intervención manual.
    """
    candidato = db.session.get(Candidato, candidato_id)
    if candidato is None:
        raise EvaluacionError("El candidato no existe", 404)
    base = {"candidato": candidato.nombre}
    if estado_efectivo(candidato.estado, candidato.evaluando_desde) != "pendiente":
        return {"ok": True, "sin_cambios": True, "estado": candidato.estado, **base}
    if Evaluacion.query.filter_by(candidato_id=candidato.id).first():
        return {"ok": True, "sin_cambios": True, "estado": candidato.estado, **base}

    if not _reclamar(candidato_id):
        return {"ok": True, "sin_cambios": True, "estado": _estado_actual(candidato_id), **base}

    # Datos como locales: el commit expira los objetos y la IA puede tardar.
    try:
        vacante = db.session.get(Vacante, candidato.vacante_id)
        perfil = vacante.perfil
        # Defensa en profundidad (R3): reaplica la limpieza por si se cargó con reglas antiguas.
        # La IA y R5 usan este MISMO texto; Candidato.texto guardado no se modifica.
        texto = documentos.quitar_datos_personales(candidato.texto or "")
        db.session.rollback()  # no mantener transacción abierta durante la llamada a la IA
    except Exception as e:
        logger.warning("Error leyendo el candidato: %s", type(e).__name__)
        _liberar(candidato_id)
        raise EvaluacionError("No se pudo guardar la evaluación", 502)

    try:
        ev, meta = agente_evaluador.evaluar(perfil, texto)
        r = reglas.resultado(perfil, texto, ev.criterios)
        alertas = list(ev.alertas_seguridad)
        if r["no_verificados"]:
            alertas.append(reglas.alerta_no_verificados(r["no_verificados"]))
    except agente_evaluador.ErrorAgente as e:
        logger.warning("Fallo de la IA: %s", type(e).__name__)
        _liberar(candidato_id)
        raise EvaluacionError(e.mensaje, 502)
    except Exception as e:
        logger.warning("Fallo de la IA: %s", type(e).__name__)
        _liberar(candidato_id)
        raise EvaluacionError("Error con la IA", 502)

    try:
        if Evaluacion.query.filter_by(candidato_id=candidato_id).first() is None:
            db.session.add(Evaluacion(
                candidato_id=candidato_id,
                criterios_json=json.dumps(r["criterios"], ensure_ascii=False),
                avanza=r["avanza"],
                puntaje=r["puntaje"],
                alertas_json=json.dumps(alertas, ensure_ascii=False),
                resumen=ev.resumen,
                modelo=meta.modelo,
                tokens_entrada=meta.tokens_entrada,
                tokens_salida=meta.tokens_salida,
                duracion_s=meta.duracion_s,
            ))
        (Candidato.query.filter_by(id=candidato_id, estado="evaluando")
         .update({"estado": "evaluado", "evaluando_desde": None}, synchronize_session=False))
        db.session.commit()
    except Exception as e:
        logger.warning("Fallo de base de datos al guardar: %s", type(e).__name__)
        _liberar(candidato_id)
        raise EvaluacionError("No se pudo guardar la evaluación", 502)
    return {"ok": True, "sin_cambios": False, "avanza": r["avanza"],
            "puntaje": r["puntaje"], "alertas": alertas, **base}


def _lista(valor):
    try:
        return json.loads(valor) if valor else []
    except ValueError:
        return []


def ranking(vacante_id):
    """Filas del inicio en orden R7 + ids pendientes + contadores. Sin pdf ni texto."""
    cols = (Candidato.id, Candidato.nombre, Candidato.estado, Candidato.fecha,
            Candidato.evaluando_desde, Evaluacion.id, Evaluacion.avanza, Evaluacion.puntaje, Evaluacion.alertas_json,
            Evaluacion.criterios_json, Evaluacion.decision)
    filas = (db.session.query(*cols)
             .outerjoin(Evaluacion, Evaluacion.candidato_id == Candidato.id)
             .filter(Candidato.vacante_id == vacante_id)
             .order_by(Candidato.id, Evaluacion.id.desc())
             .all())
    vistos, evaluados, otros, pendientes = set(), [], [], []
    for cid, nombre, estado, fecha, desde, eid, avanza, puntaje, alertas, crit, decision in filas:
        if cid in vistos:
            continue
        vistos.add(cid)
        estado = estado_efectivo(estado, desde)  # evaluando caducado -> pendiente
        item = {"id": cid, "nombre": nombre or "", "estado": estado, "fecha": fecha,
                "avanza": False, "puntaje": 0, "no_cumple": [], "n_alertas": 0, "decision": ""}
        if estado == "pendiente":
            pendientes.append(cid)
        if eid is not None:
            item.update(
                avanza=bool(avanza), puntaje=puntaje or 0, decision=decision or "",
                no_cumple=[c["codigo"] for c in _lista(crit) if not c.get("cuenta")],
                n_alertas=len(_lista(alertas)),
            )
            evaluados.append(item)
        else:
            otros.append(item)
    evaluados = reglas.ordenar_ranking(evaluados)
    otros.sort(key=lambda x: reglas.normalizar(x["nombre"]))
    pos = 0
    for it in evaluados:
        if it["avanza"]:
            pos += 1
            it["posicion"] = pos
    return {
        "filas": evaluados + otros,
        "pendientes_ids": pendientes,
        "avanzan": pos,
        "con_alertas": sum(1 for i in evaluados if i["n_alertas"] > 0),
    }
