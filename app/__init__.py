from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from os import getenv

db = SQLAlchemy()
migrate = Migrate()

def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # Basic config
    database_url = getenv("DATABASE_URL", "sqlite:///local.db")
    # Render sometimes provides postgres://, SQLAlchemy needs postgresql://
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    app.config.update(
        SQLALCHEMY_DATABASE_URI=database_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SECRET_KEY=getenv("SECRET_KEY", "dev-secret"),
    )

    db.init_app(app)
    migrate.init_app(app, db)

    # Import models so migrations see them
    from . import models  # noqa: F401

    # Register blueprints / routes
    from .views import bp as main_bp
    app.register_blueprint(main_bp)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}, 200

    return app
