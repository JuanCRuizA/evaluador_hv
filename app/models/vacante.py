import json

from app.extensions import db


class Vacante(db.Model):
    __tablename__ = "vacante"

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(60), unique=True, nullable=False)
    cargo = db.Column(db.String(300), nullable=False)
    perfil_json = db.Column(db.Text, nullable=False)

    candidatos = db.relationship("Candidato", back_populates="vacante")

    @property
    def perfil(self):
        return json.loads(self.perfil_json)
