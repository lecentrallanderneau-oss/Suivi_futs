# app.py
import os
from datetime import datetime
from flask import Flask, render_template, jsonify, abort

# On réutilise l'instance SQLAlchemy déclarée dans models.py
from models import db, Client

# Chargement (facultatif) du catalogue initial si le module existe
try:
    from catalog_loader import load_initial_catalog_if_empty
except Exception:
    load_initial_catalog_if_empty = None


def create_app() -> Flask:
    app = Flask(__name__)

    # --------- Config DB (Render) ---------
    db_url = os.getenv("DATABASE_URL", "").strip()
    if db_url.startswith("postgres://"):
        # Normalise pour SQLAlchemy moderne
        db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)

    app.config["SQLALCHEMY_DATABASE_URI"] = db_url or "sqlite:///local.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Initialise SQLAlchemy avec CETTE app (évite l'erreur init_app)
    db.init_app(app)

    # --------- Filtres Jinja ---------
    @app.template_filter("eur")
    def eur_filter(value):
        """Format EUR simple : 1234.5 -> '1 234,50 €'"""
        try:
            num = float(value)
        except Exception:
            return value
        s = f"{num:,.2f}"
        s = s.replace(",", "X").replace(".", ",").replace("X", " ")
        return s + " €"

    # --------- Démarrage (safe) ---------
    with app.app_context():
        # Sur SQLite local uniquement, on peut créer les tables
        if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:///"):
            try:
                db.create_all()
            except Exception as e:
                app.logger.warning(f"create_all() ignoré : {e}")

        # Essai de chargement du catalogue si présent
        if load_initial_catalog_if_empty is not None:
            try:
                load_initial_catalog_if_empty()
            except Exception as e:
                app.logger.warning(f"Catalogue non chargé (info) : {e}")

    # --------- Routes ---------
    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "time": datetime.utcnow().isoformat() + "Z"})

    @app.route("/")
    def index():
        """Accueil : on passe simplement la liste des clients au template."""
        clients = Client.query.order_by(Client.name.asc()).all()
        return render_template("index.html", clients=clients, now=datetime.now())

    @app.route("/clients", endpoint="clients")
    def clients_route():
        """
        Route attendue par base.html (url_for('clients')).
        Pour l’instant, on réutilise index.html pour afficher la liste.
        """
        clients = Client.query.order_by(Client.name.asc()).all()
        return render_template("index.html", clients=clients, now=datetime.now())

    @app.route("/clients/<int:client_id>", endpoint="client_detail")
    def client_detail(client_id: int):
        """
        Détail client minimal pour éviter d'autres erreurs si un lien existe.
        Si tu n'as pas encore de template 'client.html', on renvoie une page simple.
        """
        client = Client.query.get(client_id)
        if not client:
            abort(404, description="Client introuvable")
        # Si tu as un template dédié, décommente la ligne suivante et ajoute le fichier.
        # return render_template("client.html", client=client)
        return f"<h1>{client.name}</h1><p>ID: {client.id}</p><p>(Page client à compléter)</p>"

    return app


# Objet WSGI pour gunicorn ("app:app")
app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG", "0") == "1")
