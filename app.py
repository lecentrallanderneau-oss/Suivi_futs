# app.py
from __future__ import annotations
import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash
from werkzeug.middleware.proxy_fix import ProxyFix

# 1) Import des modèles et de la session SQLAlchemy de TON models.py
#    Ton models.py doit exposer au minimum: db et Client
#    (on évite ici d'importer d'autres modèles pour ne pas provoquer
#     d'erreurs si leur table/colonnes n'existent pas encore)
from models import db, Client  # type: ignore

# 2) Import du chargeur de catalogue (ton fichier catalog_loader.py)
#    Il doit fournir une fonction load_catalog() -> list[dict] ou similaire.
#    On normalise et on affiche tel quel dans templates/catalog.html.
try:
    from catalog_loader import load_catalog  # type: ignore
except Exception:
    # Filet de sécurité si le module n'existe pas (dev local, branche incomplète)
    def load_catalog():
        return []

def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # Compat Render derrière proxy
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    # Clé pour les flash messages
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")

    # 3) Connexion base (Render: DATABASE_URL)
    # Render fournit souvent un DSN "postgres://". SQLAlchemy 2.x + psycopg3
    # préfère "postgresql+psycopg://"
    dsn = os.getenv("DATABASE_URL", "")
    if dsn.startswith("postgres://"):
        dsn = dsn.replace("postgres://", "postgresql+psycopg://", 1)

    if dsn:
        app.config["SQLALCHEMY_DATABASE_URI"] = dsn
    else:
        # fallback local (sqlite) pour dev
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///local.db"

    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # 4) Initialisation de SQLAlchemy sur CETTE app
    db.init_app(app)

    # 5) Routes

    @app.route("/")
    def index():
        """
        Page d'accueil :
        - Liste des clients (id, name uniquement, pour éviter les erreurs de colonnes)
        - Lien vers 'Clients' (gestion) et 'Catalogue'
        """
        # On n'utilise volontairement que id / name pour éviter tout mismatch de colonnes
        try:
            clients = Client.query.with_entities(Client.id, Client.name).order_by(Client.name.asc()).all()
        except Exception as e:
            # Si un souci de structure DB survient, on ne casse pas la page
            clients = []
            flash(f"Erreur DB (liste clients): {e}", "danger")

        return render_template("index.html", clients=clients, now=datetime.now())

    @app.route("/clients", methods=["GET", "POST"])
    def clients():
        """
        - GET : liste simple des clients
        - POST : ajout minimal d'un client (name)
        """
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
                flash(f"Erreur DB (ajout client) : {e}", "danger")

            return redirect(url_for("clients"))

        # GET
        try:
            rows = Client.query.with_entities(Client.id, Client.name).order_by(Client.name.asc()).all()
        except Exception as e:
            rows = []
            flash(f"Erreur DB (liste clients) : {e}", "danger")

        return render_template("clients.html", clients=rows)

    @app.route("/client/<int:client_id>")
    def client_detail(client_id: int):
        """
        Fiche client minimaliste (nom et id).
        Tu pourras enrichir ensuite (historique, mouvements, matériel, etc.)
        """
        client = None
        try:
            client = Client.query.get(client_id)
        except Exception as e:
            flash(f"Erreur DB (lecture client) : {e}", "danger")

        if client is None:
            flash("Client introuvable.", "warning")
            return redirect(url_for("clients"))

        return render_template("client_detail.html", client=client)

    @app.route("/catalog")
    def catalog():
        """
        Catalogue produits : lit via catalog_loader.load_catalog() (CSV dans data/products.csv)
        On passe la liste 'products' au template catalog.html
        """
        try:
            products_raw = load_catalog()
        except Exception as e:
            products_raw = []
            flash(f"Erreur chargement catalogue : {e}", "danger")

        # Normalisation douce : on garantit quelques clés pour le template
        products = []
        for p in products_raw or []:
            products.append({
                "name": p.get("name") or p.get("product") or "",
                "size_liters": p.get("size_liters") or p.get("size") or "",
                "price_eur": p.get("price_eur") or p.get("price") or "",
                "package": p.get("package") or p.get("pack") or "",
                "active": p.get("active", True),
                "sku": p.get("sku") or "",
                "category": p.get("category") or "",
            })

        return render_template("catalog.html", products=products)

    return app


# Exposé pour gunicorn: "gunicorn app:app"
app = create_app()

# Optionnel : dev local
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
