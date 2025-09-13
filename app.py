# app.py
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, List, Dict

from flask import Flask, render_template, request, redirect, url_for, flash
from werkzeug.middleware.proxy_fix import ProxyFix

# Ton ORM
from models import db, Client  # type: ignore

# Chargeur de catalogue (optionnel)
try:
    from catalog_loader import load_catalog  # type: ignore
except Exception:
    def load_catalog() -> List[Dict[str, Any]]:
        return []


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    # Clé de session
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")

    # DSN Render -> SQLAlchemy (psycopg 3)
    dsn = os.getenv("DATABASE_URL", "")
    if dsn.startswith("postgres://"):
        dsn = dsn.replace("postgres://", "postgresql+psycopg://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = dsn or "sqlite:///local.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Init DB
    db.init_app(app)

    # ----------------- Filtre Jinja : eur -----------------
    @app.template_filter("eur")
    def eur(value: Any) -> str:
        """
        Formatte une valeur en euros :
          - int : supposé être des centimes
          - float/str : euros
        Retourne une chaîne style '1 234,56 €'
        """
        try:
            if isinstance(value, int):
                euros = value / 100.0
            else:
                euros = float(value)
            txt = f"{euros:,.2f}"
            # Français : espace pour milliers, virgule pour décimales
            txt = txt.replace(",", " ").replace(".", ",")
            return f"{txt} €"
        except Exception:
            return f"{value} €"

    # ----------------- Routes -----------------

    @app.route("/")
    def index():
        # On ne sélectionne QUE id et name pour éviter les erreurs de colonnes
        try:
            clients = (
                Client.query.with_entities(Client.id, Client.name)
                .order_by(Client.name.asc())
                .all()
            )
        except Exception as e:
            clients = []
            flash(f"Erreur DB (liste clients) : {e}", "danger")

        # Valeurs par défaut attendues par index.html (ex. totals.deposit_cents|eur)
        totals = {"deposit_cents": 0}
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
                flash(f"Client « {name} » ajouté.", "success")
            except Exception as e:
                db.session.rollback()
                flash(f"Erreur lors de l'ajout du client : {e}", "danger")
            return redirect(url_for("clients"))

        # GET
        try:
            all_clients = (
                Client.query.with_entities(Client.id, Client.name)
                .order_by(Client.name.asc())
                .all()
            )
        except Exception as e:
            all_clients = []
            flash(f"Erreur DB (clients) : {e}", "danger")
        return render_template("clients.html", clients=all_clients)

    @app.route("/clients/<int:client_id>")
    def client_detail(client_id: int):
        try:
            client = Client.query.get_or_404(client_id)
        except Exception as e:
            flash(f"Erreur DB (client #{client_id}) : {e}", "danger")
            return redirect(url_for("clients"))

        # Placeholders simples tant que l’historique n’est pas codé
        # Tu pourras injecter ici livraisons/reprises/matériel plus tard.
        history: List[Dict[str, Any]] = []
        equipment: List[Dict[str, Any]] = []

        return render_template(
            "client_detail.html",
            client=client,
            history=history,
            equipment=equipment,
            now=datetime.now(),
        )

    @app.route("/catalog")
    def catalog():
        try:
            products = load_catalog()  # lit data/products.csv si présent
        except Exception as e:
            products = []
            flash(f"Impossible de charger le catalogue : {e}", "warning")

        # On peut aussi prévoir un tri par nom
        try:
            products = sorted(products, key=lambda p: (p.get("brand",""), p.get("name","")))
        except Exception:
            pass

        return render_template("catalog.html", products=products)

    return app


# Instance exportée pour gunicorn: "app:app"
app = create_app()

if __name__ == "__main__":
    # Pour exécuter en local facilement : python app.py
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
