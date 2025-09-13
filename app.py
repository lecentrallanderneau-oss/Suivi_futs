# app.py
from __future__ import annotations
import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash
from werkzeug.middleware.proxy_fix import ProxyFix

# Import depuis TON models.py
from models import db, Client  # type: ignore

# Import du chargeur de catalogue
try:
    from catalog_loader import load_catalog  # type: ignore
except Exception:
    def load_catalog():
        return []

def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")

    # DSN Render → SQLAlchemy
    dsn = os.getenv("DATABASE_URL", "")
    if dsn.startswith("postgres://"):
        dsn = dsn.replace("postgres://", "postgresql+psycopg://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = dsn or "sqlite:///local.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    # ---------- Filtre Jinja: eur ----------
    @app.template_filter("eur")
    def eur(value) -> str:
        """
        Accepte :
          - int (centimes)
          - float / Decimal (euros)
          - str convertible
        Retourne '1 234,56 €'
        """
        try:
            # si c’est un int « centimes »
            if isinstance(value, int):
                euros = value / 100.0
            else:
                euros = float(value)
            txt = f"{euros:,.2f}"
        except Exception:
            # en dernier recours : affiche brut
            return f"{value} €"

        # Français : espace fin comme séparateur milliers, virgule décimale
        txt = txt.replace(",", " ").replace(".", ",")
        return f"{txt} €"

    # ---------- Routes ----------
    @app.route("/")
    def index():
        # clients: on reste minimal (id, name) pour éviter les soucis de schéma
        try:
            clients = Client.query.with_entities(Client.id, Client.name)\
                                  .order_by(Client.name.asc()).all()
        except Exception as e:
            clients = []
            flash(f"Erreur DB (liste clients): {e}", "danger")

        # 'totals' par défaut si des templates s’y attendent
        totals = {
            "deposit_cents": 0,   # utilisé avec le filtre |eur dans index.html
        }
        return render_template("index.html", clients=clients, totals=totals, now=datetime.now())

    @app.route("/clients", methods=["GET", "POST"])
    def clients():
        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            if not name:
                flash("Le nom du client est obligatoire.", "warning")
                return redirect(url_for("clients"))
            try:
                c = Client(name=name)
                db.session.add(c)
                db.session.commit()
                flash(f
