from datetime import datetime, timezone

from app.extensions import db


ESTADOS = ("pendiente", "evaluando", "evaluado", "sin_texto")


class Candidato(db.Model):
    __tablename__ = "candidato"
    __table_args__ = (db.UniqueConstraint("vacante_id", "sha256", name="uq_candidato_vacante_sha256"),)

    id = db.Column(db.Integer, primary_key=True)
    vacante_id = db.Column(db.Integer, db.ForeignKey("vacante.id"), nullable=False, index=True)
    archivo = db.Column(db.String(300), nullable=False)
    nombre = db.Column(db.String(300))
    sha256 = db.Column(db.String(64), nullable=False)
    pdf = db.Column(db.LargeBinary, nullable=False)
    texto = db.Column(db.Text)
    # "pendiente", "evaluando" (reclamado por una evaluación en curso), "evaluado" o "sin_texto"
    estado = db.Column(db.String(20), nullable=False, default="pendiente")
    fecha = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    # Momento (UTC, naive) en que una evaluación reclamó al candidato; permite recuperar reclamos caducados.
    evaluando_desde = db.Column(db.DateTime, nullable=True)

    vacante = db.relationship("Vacante", back_populates="candidatos")
    evaluaciones = db.relationship("Evaluacion", back_populates="candidato")
