# app.py
import os
from datetime import datetime
from flask import Flask, render_template, render_template_string, jsonify, abort

# On réutilise l'instance SQLAlchemy définie dans models.py
from models import db, Client  # Client est présent dans ton starter

# Ces imports sont optionnels : si Product / Gear n'existent pas encore,
# on ne casse pas l'appli (le /catalog fonctionnera quand-même).
try:
    from models import Product  # type: ignore
except Exception:
    Product = None  # pyright: ignore[reportGeneralTypeIssues]

try:
    from models import Gear  # type: ignore
except Exception:
    Gear = None  # pyright: ignore[reportGeneralTypeIssues]


def create_app() -> Flask:
    app = Flask(__name__)

    # ---------- Config DB (Render / local) ----------
    db_url = os.getenv("DATABASE_URL", "").strip()
    if db_url.startswith("postgres://"):
        # Normaliser pour SQLAlchemy moderne (psycopg v3)
        db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)

    app.config["SQLALCHEMY_DATABASE_URI"] = db_url or "sqlite:///local.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Initialisation SQLAlchemy avec CETTE app
    db.init_app(app)

    # ---------- Filtres Jinja ----------
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

    # ---------- Boot (safe) ----------
    with app.app_context():
        # Création des tables uniquement en SQLite local
        if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:///"):
            try:
                db.create_all()
            except Exception as e:
                app.logger.warning(f"create_all() ignoré : {e}")

    # ---------- Routes ----------
    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "time": datetime.utcnow().isoformat() + "Z"})

    @app.route("/")
    def index():
        """Accueil : liste des clients (réutilise index.html)."""
        clients = Client.query.order_by(Client.name.asc()).all()
        return render_template("index.html", clients=clients, now=datetime.now())

    @app.route("/clients", endpoint="clients")
    def clients_route():
        """Endpoint attendu par base.html (url_for('clients'))."""
        clients = Client.query.order_by(Client.name.asc()).all()
        return render_template("index.html", clients=clients, now=datetime.now())

    @app.route("/clients/<int:client_id>", endpoint="client_detail")
    def client_detail(client_id: int):
        """Détail d’un client (placeholder si pas encore de template)."""
        client = Client.query.get(client_id)
        if not client:
            abort(404, description="Client introuvable")
        # Si tu ajoutes templates/client.html, remplace par:
        # return render_template("client.html", client=client)
        return f"<h1>{client.name}</h1><p>ID: {client.id}</p><p>(Page client à compléter)</p>"

    @app.route("/catalog", endpoint="catalog")
    def catalog():
        """
        Endpoint attendu par base.html (url_for('catalog')).
        Fonctionne même sans modèles Product/Gear ni template dédié.
        """
        products = []
        gears = []
        try:
            if Product is not None:
                products = (Product.query.order_by(Product.name.asc()).all())  # type: ignore[attr-defined]
        except Exception as e:
            app.logger.info(f"/catalog: Products indisponibles ({e})")

        try:
            if Gear is not None:
                gears = (Gear.query.order_by(Gear.name.asc()).all())  # type: ignore[attr-defined]
        except Exception as e:
            app.logger.info(f"/catalog: Gears indisponibles ({e})")

        # Si un template catalog.html existe, on l'utilise
        try:
            return render_template("catalog.html", products=products, gears=gears)
        except Exception:
            # Fallback minimal si pas de template
            html = """
            <html>
            <head><title>Catalogue</title></head>
            <body style="font-family: system-ui, sans-serif;">
              <h1>Catalogue</h1>
              {% if products %}
                <h2>Produits</h2>
                <ul>
                  {% for p in products %}
                    <li>{{ p.name }}</li>
                  {% endfor %}
                </ul>
              {% else %}
                <p>Aucun produit à afficher (ajoute Product dans models.py et un template catalog.html pour une meilleure vue).</p>
              {% endif %}

              {% if gears %}
                <h2>Matériel</h2>
                <ul>
                  {% for g in gears %}
                    <li>{{ g.name }}</li>
                  {% endfor %}
                </ul>
              {% else %}
                <p>Aucun matériel à afficher.</p>
              {% endif %}

              <p style="margin-top:2rem;"><a href="{{ url_for('index') }}">⬅︎ Retour</a></p>
            </body>
            </html>
            """
            return render_template_string(html, products=products, gears=gears)

    return app


# Objet WSGI pour gunicorn ("app:app")
app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG", "0") == "1")
