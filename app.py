from __future__ import annotations

import os
from datetime import datetime
from urllib.parse import urlparse

from flask import Flask, render_template, abort
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")

# -------------------------------------------------------------------
# Base de données
#  - Prod (Render): DATABASE_URL -> forcer le driver psycopg3
#  - Local: SQLite
# -------------------------------------------------------------------
db_url = os.environ.get("DATABASE_URL")
if db_url:
    # Render peut fournir "postgres://..."
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    # On force psycopg3 (sinon Flask/SQLAlchemy tente psycopg2)
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
else:
    db_url = "sqlite:///local.db"

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}

db = SQLAlchemy(app)

# -------------------------------------------------------------------
# Modèles MINIMAUX (structure stable). On ajoute au fur et à mesure.
# -------------------------------------------------------------------
class Client(db.Model):
    __tablename__ = "clients"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)


# -------------------------------------------------------------------
# Filtres & context
# -------------------------------------------------------------------
@app.template_filter("eur")
def eur_filter(cents: int | None) -> str:
    """Formate des centimes en '12,34 €'."""
    value = (cents or 0) / 100.0
    return f"{value:,.2f} €".replace(",", "X").replace(".", ",").replace("X", " ")

@app.context_processor
def inject_now():
    # IMPORTANT : injecter un objet datetime (pas la fonction)
    return {"now": datetime.utcnow()}


# -------------------------------------------------------------------
# Routes
# -------------------------------------------------------------------
@app.route("/")
def index():
    # Si la table n'existe pas encore en prod, on évite de crasher.
    try:
        clients = Client.query.order_by(Client.name.asc()).all()
    except Exception:
        clients = []
    return render_template("index.html", clients=clients)

@app.route("/clients")
def clients():
    try:
        clients = Client.query.order_by(Client.name.asc()).all()
    except Exception:
        clients = []
    return render_template("clients.html", clients=clients)

@app.route("/clients/<int:client_id>")
def client_detail(client_id: int):
    try:
        client = Client.query.get(client_id)
    except Exception:
        client = None
    if not client:
        abort(404)
    return render_template("client_detail.html", client=client)

# ✅ Nouveau : endpoint 'catalog' pour que les templates ne plantent plus
@app.route("/catalog", endpoint="catalog")
def catalog_page():
    # Page neutre pour l’instant : pas de requête sur un modèle “Product”
    # tant que le schéma n’est pas stabilisé.
    return render_template("catalog.html")


# -------------------------------------------------------------------
# Lancement local : création des tables SQLite si besoin
# -------------------------------------------------------------------
if __name__ == "__main__":
    parsed = urlparse(app.config["SQLALCHEMY_DATABASE_URI"])
    if parsed.scheme.startswith("sqlite"):
        with app.app_context():
            db.create_all()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
