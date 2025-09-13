# app.py
import os
from flask import Flask, render_template, jsonify
from datetime import datetime

# IMPORTANT : on réutilise l'instance db depuis models.py
# (pour éviter "The current Flask app is not registered with this 'SQLAlchemy' instance")
from models import db, Client  # importe uniquement ce dont on est sûr qu'existe

# Chargement initial du catalogue (produits + matériel)
# -> ce module est optionnel mais recommandé (cf. fichiers fournis précédemment)
try:
    from catalog_loader import load_initial_catalog_if_empty
except Exception:
    load_initial_catalog_if_empty = None  # si absent, on ignore

# ---------------------------------------------------------------------
# Création et configuration de l'application
# ---------------------------------------------------------------------
def create_app() -> Flask:
    app = Flask(__name__)

    # --- Config DB pour Render ---
    # Render fournit la variable DATABASE_URL
    db_url = os.getenv("DATABASE_URL", "").strip()
    if db_url.startswith("postgres://"):
        # SQLAlchemy moderne préfère 'postgresql+psycopg'
        db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)

    if db_url:
        app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    else:
        # fallback local si besoin
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///local.db"

    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Initialise l'extension SQLAlchemy avec CETTE app
    db.init_app(app)

    # -----------------------------------------------------------------
    # Filtres Jinja
    # -----------------------------------------------------------------
    @app.template_filter("eur")
    def eur_filter(value):
        """
        Formatage EUR : 1234.5 -> '1 234,50 €'
        Supporte int/float/Decimal/str.
        """
        try:
            num = float(value)
        except Exception:
            return value
        s = f"{num:,.2f}"
        # US -> FR (virgule décimale + espace fine insécable pour milliers)
        s = s.replace(",", "X").replace(".", ",").replace("X", " ")
        return s + " €"

    # -----------------------------------------------------------------
    # Hooks de démarrage : on ne fait que ce qui est sûr
    # - Pas de create_all() sur PostgreSQL géré (migrations ailleurs)
    # - On tente le chargement du catalogue si la base est accessible
    # -----------------------------------------------------------------
    with app.app_context():
        # Sur SQLite local uniquement on peut créer les tables (facilite tests)
        uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
        if uri.startswith("sqlite:///"):
            try:
                db.create_all()
            except Exception as e:
                # On ne bloque pas le déploiement si create_all échoue
                app.logger.warning(f"create_all() ignoré : {e}")

        # Charge le catalogue si le module est présent
        if load_initial_catalog_if_empty is not None:
            try:
                load_initial_catalog_if_empty()
            except Exception as e:
                app.logger.warning(f"Catalogue non chargé (info) : {e}")

    # -----------------------------------------------------------------
    # Routes
    # -----------------------------------------------------------------
    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "time": datetime.utcnow().isoformat() + "Z"})

    @app.route("/")
    def index():
        """
        Page d'accueil ultra-safe : on envoie UNIQUEMENT la liste des clients.
        -> Pas de champs 'city' / 'note', pas d'agrégations sur d'autres tables.
        Le template 'templates/index.html' devra boucler sur 'clients'.
        """
        clients = Client.query.order_by(Client.name.asc()).all()
        return render_template("index.html", clients=clients, now=datetime.now())

    return app


# Objet WSGI pour gunicorn ("app:app")
app = create_app()

# Lancement local éventuel
if __name__ == "__main__":
    # host=0.0.0.0 + port = Render/Heroku-like
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG", "0") == "1")
