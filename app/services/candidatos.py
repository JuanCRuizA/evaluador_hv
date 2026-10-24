"""R1: validación y guardado de hojas de vida (un PDF por llamada)."""
import hashlib

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer

from app.extensions import db
from app.models import Candidato, Vacante
from app.services import documentos

MAX_BYTES = 4 * 1024 * 1024


class CargaError(Exception):
    def __init__(self, motivo, codigo):
        super().__init__(motivo)
        self.motivo = motivo
        self.codigo = codigo


def validar_un_archivo(archivos):
    """Un archivo por petición: 422 si llegan varios (en un campo o en varios campos).
    `archivos` es request.files (MultiDict)."""
    if len(archivos.getlist("archivo")) > 1 or len(archivos) > 1:
        raise CargaError("Envíe un solo archivo por petición", 422)


def guardar_pdf(vacante_id, nombre_archivo, contenido):
    """Valida y guarda un PDF. Devuelve el Candidato o lanza CargaError (con código HTTP)."""
    nombre_archivo = (nombre_archivo or "").strip()
    if not vacante_id or db.session.get(Vacante, vacante_id) is None:
        raise CargaError("La vacante no existe", 404)
    if not nombre_archivo or not nombre_archivo.lower().endswith(".pdf"):
        raise CargaError("Solo se aceptan archivos .pdf", 422)
    if len(contenido) > MAX_BYTES:
        raise CargaError("Supera el máximo de 4 MB", 413)
    if not contenido.startswith(b"%PDF"):
        raise CargaError("El archivo no es un PDF válido", 422)

    sha = hashlib.sha256(contenido).hexdigest()
    if Candidato.query.filter_by(vacante_id=vacante_id, sha256=sha).first():
        raise CargaError("ya estaba cargado (mismo archivo)", 409)

    try:
        original = documentos.extraer_texto(contenido)
    except documentos.PdfIlegibleError:
        raise CargaError("PDF dañado o ilegible", 422)
    # Decisión: R2 (sin_texto) se mide sobre el texto YA limpio (R3). Un PDF cuyo texto útil
    # sea casi solo datos personales queda sin_texto a propósito: no hay nada que evaluar.
    limpio = documentos.quitar_datos_personales(original)  # nunca se guarda el original
    candidato = Candidato(
        vacante_id=vacante_id,
        archivo=nombre_archivo[:300],
        nombre=documentos.nombre_candidato(nombre_archivo)[:300],
        sha256=sha,
        pdf=contenido,
        texto=limpio,
        estado=documentos.estado_por_texto(limpio),
    )
    db.session.add(candidato)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise CargaError("ya estaba cargado (mismo archivo)", 409)
    return candidato


def listar_candidatos(vacante_id):
    return (
        Candidato.query.options(defer(Candidato.pdf), defer(Candidato.texto))
        .filter_by(vacante_id=vacante_id)
        .order_by(Candidato.fecha.desc(), Candidato.id.desc())
        .all()
    )


def obtener_candidato(candidato_id):
    return db.session.get(Candidato, candidato_id)
