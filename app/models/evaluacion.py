from datetime import datetime, timezone

from app.extensions import db


class Evaluacion(db.Model):
    __tablename__ = "evaluacion"

    id = db.Column(db.Integer, primary_key=True)
    candidato_id = db.Column(db.Integer, db.ForeignKey("candidato.id"), nullable=False, index=True)
    criterios_json = db.Column(db.Text)
    avanza = db.Column(db.Boolean, nullable=False, default=False)
    puntaje = db.Column(db.Integer, nullable=False, default=0)
    alertas_json = db.Column(db.Text)
    resumen = db.Column(db.Text)
    modelo = db.Column(db.String(100))
    tokens_entrada = db.Column(db.Integer)
    tokens_salida = db.Column(db.Integer)
    duracion_s = db.Column(db.Float)
    decision = db.Column(db.String(20), nullable=False, default="sin revisar")
    comentario = db.Column(db.Text)
    fecha = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    candidato = db.relationship("Candidato", back_populates="evaluaciones")
