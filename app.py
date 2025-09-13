from __future__ import annotations

import os
from datetime import datetime
from urllib.parse import urlparse

from flask import (
    Flask, render_template, abort,
    redirect, url_for, request, flash,
)
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")

# -------------------------------------------------------------------
# Base de données
#  - Prod (Render): DATABASE_URL -> psycopg (psycopg3)
#  - Local: SQLite
# -------------------------------------------------------------------
db_url = os.environ.get("DATABASE_URL")
if db_url:
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
else:
    db_url = "sqlite:///local.db"

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}

db = SQLAlchemy(app)

# -------------------------------------------------------------------
# Modèles MINIMAUX
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
    value = (cents or 0) / 100.0
    return f"{value:,.2f} €".replace(",", "X").replace(".", ",").replace("X", " ")

@app.context_processor
def inject_now():
    return {"now": datetime.utcnow()}

# -------------------------------------------------------------------
# Routes
# -------------------------------------------------------------------
@app.route("/")
def index():
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

# ✅ Stub pour éviter le 500 depuis client_detail.html
@app.route("/clients/<int:client_id>/movements/add", methods=["POST"], endpoint="add_movement")
def add_movement(client_id: int):
    # Pas de modèle Movement pour l’instant : on stabilise seulement le flux.
    # On récupère les champs du formulaire si jamais ils existent déjà.
    movement_type = request.form.get("movement_type", "").strip()  # "delivery" / "return"
    note = request.form.get("note", "").strip()

    try:
        client = Client.query.get(client_id)
    except Exception:
        client = None

    if not client:
        abort(404)

    flash("Saisie des livraisons & reprises — structure OK, logique BDD à venir.", "info")
    # Quand on aura les modèles, on insérera ici et on redirigera pareil.
    return redirect(url_for("client_detail", client_id=client.id))

# Page catalogue neutre pour l’instant
@app.route("/catalog", endpoint="catalog")
def catalog_page():
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
