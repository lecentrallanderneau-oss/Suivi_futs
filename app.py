# app.py
from __future__ import annotations

import os
from datetime import datetime
from urllib.parse import urlparse

from flask import Flask, render_template, abort
from flask_sqlalchemy import SQLAlchemy

# -----------------------------------------------------------------------------
# Config Flask
# -----------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")

# -----------------------------------------------------------------------------
# Base de données
# - En prod (Render) : DATABASE_URL -> forcer driver psycopg3
# - En local : SQLite
# -----------------------------------------------------------------------------
db_url = os.environ.get("DATABASE_URL")

if db_url:
    # Render peut donner "postgres://..." -> standardiser
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    # Si l’URL est "postgresql://..." (driver implicite psycopg2),
    # on force psycopg3 avec "postgresql+psycopg://..."
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
else:
    # fallback local
    db_url = "sqlite:///local.db"

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# Option utile avec certains reverse proxies / connexions dormantes
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}

db = SQLAlchemy(app)

# -----------------------------------------------------------------------------
# Modèles minimalistes (structure stable)
# -----------------------------------------------------------------------------
class Client(db.Model):
    __tablename__ = "clients"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)


# -----------------------------------------------------------------------------
# Filtres & context processors
# -----------------------------------------------------------------------------
@app.template_filter("eur")
def eur_filter(cents: int | None) -> str:
    """Formate des centimes en '12,34 €'."""
    value = (cents or 0) / 100.0
    return f"{value:,.2f} €".replace(",", "X").replace(".", ",").replace("X", " ")

@app.context_processor
def inject_now():
    # IMPORTANT : renvoyer un objet datetime, pas la fonction
    return {"now": datetime.utcnow()}


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.route("/")
def index():
    clients = Client.query.order_by(Client.name.asc()).all()
    return render_template("index.html", clients=clients)

@app.route("/clients")
def clients():
    clients = Client.query.order_by(Client.name.asc()).all()
    return render_template("clients.html", clients=clients)

@app.route("/clients/<int:client_id>")
def client_detail(client_id: int):
    client = Client.query.get(client_id)
    if not client:
        abort(404)
    return render_template("client_detail.html", client=client)


# -----------------------------------------------------------------------------
# Lancement local uniquement (création SQLite)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    parsed = urlparse(app.config["SQLALCHEMY_DATABASE_URI"])
    if parsed.scheme.startswith("sqlite"):
        with app.app_context():
            db.create_all()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
