from flask import Flask, jsonify
from sqlalchemy import text

from app.config import cargar_configuracion, es_postgres
from app.extensions import db


def create_app(config=None):
    app = Flask(__name__, static_folder=None)
    app.config.update(cargar_configuracion(config))

    app.config.setdefault("MAX_CONTENT_LENGTH", 4 * 1024 * 1024 + 512 * 1024)

    @app.errorhandler(413)
    def demasiado_grande(_error):
        return jsonify(archivo="", ok=False, motivo="Supera el máximo de 4 MB"), 413

    db.init_app(app)

    from app import models  # noqa: F401  (registra las tablas)
    from app.controllers.auth import bp as auth_bp
    from app.controllers.main import bp as main_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    with app.app_context():
        db.create_all()
        if es_postgres(app.config["SQLALCHEMY_DATABASE_URI"]):
            for tabla in ("vacante", "candidato", "evaluacion"):
                db.session.execute(text(f"ALTER TABLE {tabla} ENABLE ROW LEVEL SECURITY"))
            # create_all no altera tablas existentes: columna añadida después (idempotente).
            db.session.execute(text(
                "ALTER TABLE candidato ADD COLUMN IF NOT EXISTS evaluando_desde TIMESTAMP"))
            db.session.commit()
        from app.services.vacantes import cargar_perfiles

        cargar_perfiles()

    return app
