"""Lógica de vacantes: carga de perfiles y contadores."""
import json

from sqlalchemy.exc import IntegrityError

from app.config import RAIZ
from app.extensions import db
from app.models import Candidato, Vacante

DIR_PERFILES = RAIZ / "insumos" / "perfiles"


def cargar_perfiles(directorio=DIR_PERFILES):
    """Crea las vacantes de insumos/perfiles/*.json cuyo código no exista. Solo lectura."""
    creadas = 0
    for ruta in sorted(directorio.glob("*.json")):
        perfil = json.loads(ruta.read_text(encoding="utf-8"))
        codigo = perfil["codigo"]
        if Vacante.query.filter_by(codigo=codigo).first():
            continue
        db.session.add(
            Vacante(
                codigo=codigo,
                cargo=perfil["cargo"],
                perfil_json=json.dumps(perfil, ensure_ascii=False),
            )
        )
        try:
            db.session.commit()
            creadas += 1
        except IntegrityError:  # otro proceso la creó a la vez
            db.session.rollback()
    return creadas


def listar_vacantes():
    return Vacante.query.order_by(Vacante.id).all()


def obtener_vacante(vacante_id=None):
    """La vacante pedida o, si no existe o no se indica, la primera."""
    vacante = db.session.get(Vacante, vacante_id) if vacante_id else None
    return vacante or Vacante.query.order_by(Vacante.id).first()


def contadores(vacante_id):
    def contar(*filtros):
        return Candidato.query.filter(Candidato.vacante_id == vacante_id, *filtros).count()

    from app.services.evaluacion import _limite  # import tardío: evita ciclos al arrancar

    caducado = db.and_(Candidato.estado == "evaluando",
                       db.or_(Candidato.evaluando_desde.is_(None),
                              Candidato.evaluando_desde < _limite()))
    return {
        "cargadas": contar(),
        "pendientes": contar(db.or_(Candidato.estado == "pendiente", caducado)),
        "evaluando": contar(Candidato.estado == "evaluando", db.not_(caducado)),
        "evaluadas": contar(Candidato.estado == "evaluado"),
    }
