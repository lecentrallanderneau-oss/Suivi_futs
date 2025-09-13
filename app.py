# app.py
from __future__ import annotations

import os
from datetime import datetime
from urllib.parse import urlparse

from flask import Flask, render_template, redirect, url_for, abort
from flask_sqlalchemy import SQLAlchemy

# -----------------------------------------------------------------------------
# Config Flask
# -----------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")

# -----------------------------------------------------------------------------
# Base de données (Render: DATABASE_URL; local: sqlite)
# -----------------------------------------------------------------------------
db_url = os.environ.get("DATABASE_URL")
if db_url:
    # Render fournit souvent "postgres://", SQLAlchemy attend "postgresql://"
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
else:
    db_url = "sqlite:///local.db"

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# -----------------------------------------------------------------------------
# Modèles (minimaux, compatibles "structure d'abord")
# !!! Ne déclenche aucune création/altération de colonnes automatiquement.
# -----------------------------------------------------------------------------
class Client(db.Model):
    __tablename__ = "clients"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)

    # Ajoute ce que tu as déjà dans ta base si besoin,
    # mais on garde ici le strict minimum pour la stabilité.


# -----------------------------------------------------------------------------
# Filtres & context processors
# -----------------------------------------------------------------------------
@app.template_filter("eur")
def eur_filter(cents: int | None) -> str:
    """
    Formate des centimes en '12,34 €'. Si None, retourne '0,00 €'.
    """
    value = (cents or 0) / 100.0
    # Remplacer le point par une virgule pour le format FR
    return f"{value:,.2f} €".replace(",", "X").replace(".", ",").replace("X", " ")

@app.context_processor
def inject_now():
    # Très important : renvoyer la **valeur** datetime, pas la fonction
    return {"now": datetime.utcnow()}


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.route("/")
def index():
    # Tableau de bord très simple : liste des clients
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
    # Page de détail minimaliste pour ne pas casser la nav
    return render_template("client_detail.html", client=client)


# -----------------------------------------------------------------------------
# Lancement local
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # En local seulement : créer le fichier sqlite s'il n'existe pas,
    # sans forcer de migrations sur Postgres en prod.
    parsed = urlparse(app.config["SQLALCHEMY_DATABASE_URI"])
    if parsed.scheme.startswith("sqlite"):
        with app.app_context():
            db.create_all()

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
