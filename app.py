import os
import csv
from pathlib import Path
from datetime import datetime

from flask import (
    Flask, render_template, request, redirect, url_for, flash, Blueprint
)
from sqlalchemy import text
from dotenv import load_dotenv

# ---------------------------------------------------------
# Import des modèles et du db fournis par ton models.py
# (NE PAS recréer un autre SQLAlchemy() ici !)
# ---------------------------------------------------------
from models import db, Client  # importe d'autres modèles si besoin

# ---------------------------------------------------------
# App & Config
# ---------------------------------------------------------
load_dotenv()

def _normalize_db_url(url: str) -> str:
    # Render fournit souvent postgres:// -> SQLAlchemy veut postgresql://
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = _normalize_db_url(
    os.getenv("DATABASE_URL", "sqlite:///local.db")
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Important: on utilise le db de models.py
db.init_app(app)

# ---------------------------------------------------------
# Helpers de formatage simples utilisables en template
# ---------------------------------------------------------
@app.template_filter("eur")
def eur(value):
    try:
        n = float(value)
    except Exception:
        return value
    return f"{n:,.2f} €".replace(",", " ").replace(".", ",")

# ---------------------------------------------------------
# ROUTES PRINCIPALES MINIMALES
# ---------------------------------------------------------
@app.route("/", methods=["GET"])
def index():
    """Accueil: liste des clients. (Simple, stable)"""
    clients = Client.query.order_by(Client.name.asc()).all()
    return render_template("index.html", clients=clients, now=datetime.now())

# Certains de tes templates/link nav utilisent /clients (endpoint 'clients')
# -> on fournit l’endpoint pour éviter les BuildError.
@app.route("/clients", methods=["GET"])
def clients():
    return redirect(url_for("index"))

# Fiche client minimale (si ton template l’utilise)
@app.route("/clients/<int:client_id>", methods=["GET"])
def client_detail(client_id: int):
    client = Client.query.get_or_404(client_id)
    # Rends un template s'il existe, sinon affiche une page simple
    # (Laisse tel quel si tu as déjà templates/client_detail.html)
    try:
        return render_template("client_detail.html", client=client)
    except Exception:
        return f"<h1>{client.name}</h1><p>ID: {client.id}</p>"

# ---------------------------------------------------------
# CATALOGUE (DB + Seed depuis data/products.csv)
# -> NE CHANGE PAS models.py. On crée une table légère 'products'.
# ---------------------------------------------------------
catalog_bp = Blueprint("catalog", __name__, template_folder="templates")

PRODUCTS_DDL = """
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    family TEXT NOT NULL,               -- ex: "bière", "cidre", "boisson"
    volume_l INTEGER NOT NULL CHECK (volume_l IN (20,22,30)),
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (name, volume_l)
);
"""

def ensure_products_table_and_seed():
    """
    Crée la table products si besoin et importe data/products.csv si la table est vide.
    Rappels métiers :
      - COREFF Ambrée uniquement en 22L.
      - Blanche / Rousse / Cidre : pas de 30L.
    """
    with db.engine.begin() as conn:
        # 1) Créer la table si nécessaire
        conn.execute(text(PRODUCTS_DDL))

        # 2) Déjà des produits ?
        count = conn.execute(text("SELECT COUNT(*) FROM products")).scalar_one()

        # 3) Si vide, tenter d'importer depuis CSV
        if count == 0:
            csv_path = Path("data/products.csv")
            if not csv_path.exists():
                # Rien à importer : on laisse la table vide
                return

            with csv_path.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)

            for r in rows:
                name = (r.get("name") or "").strip()
                family = (r.get("family") or "").strip() or "bière"

                # volume autorisé
                try:
                    volume_l = int((r.get("volume_l") or "0").strip())
                except ValueError:
                    volume_l = 0

                # prix (euros -> cents)
                price_eur_str = (r.get("price_eur") or "").replace(",", ".").strip()
                try:
                    price_cents = int(round(float(price_eur_str) * 100))
                except ValueError:
                    price_cents = -1

                # Validation basique
                if not name or volume_l not in (20, 22, 30) or price_cents < 0:
                    continue

                # Règles métier
                name_low = name.lower()
                if "ambrée" in name_low and "coreff" in name_low and volume_l != 22:
                    # COREFF Ambrée uniquement 22L -> on ignore les autres volumes
                    continue
                if any(k in name_low for k in ["blanche", "rousse", "cidre"]) and volume_l == 30:
                    # Pas de 30L pour Blanche / Rousse / Cidre -> on ignore ces lignes
                    continue

                # UPSERT (name, volume_l)
                conn.execute(text("""
                    INSERT INTO products (name, family, volume_l, price_cents)
                    VALUES (:name, :family, :volume_l, :price_cents)
                    ON CONFLICT (name, volume_l)
                    DO UPDATE SET
                        family = EXCLUDED.family,
                        price_cents = EXCLUDED.price_cents,
                        is_active = TRUE
                """), {
                    "name": name,
                    "family": family,
                    "volume_l": volume_l,
                    "price_cents": price_cents,
                })

@catalog_bp.route("/catalog", methods=["GET"])
def catalog_index():
    """Liste des produits actifs depuis la DB, groupés par nom avec leurs volumes/prix."""
    with db.engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT id, name, family, volume_l, price_cents, is_active
            FROM products
            WHERE is_active = TRUE
            ORDER BY name ASC, volume_l ASC
        """)).mappings().all()

    grouped = {}
    for r in rows:
        grouped.setdefault(r["name"], {"family": r["family"], "variants": []})
        grouped[r["name"]]["variants"].append({
            "id": r["id"],
            "volume_l": r["volume_l"],
            "price_eur": r["price_cents"] / 100.0
        })

    return render_template("catalog.html", products=grouped)

@catalog_bp.route("/catalog/add", methods=["POST"])
def catalog_add():
    """
    Ajout rapide d’un produit/volume/prix depuis le formulaire de la page /catalog.
    Applique les règles métier :
      - COREFF Ambrée uniquement en 22L
      - Blanche / Rousse / Cidre pas de 30L
    """
    name = (request.form.get("name") or "").strip()
    family = (request.form.get("family") or "bière").strip() or "bière"
    volume_l_str = (request.form.get("volume_l") or "").strip()
    price_eur_str = (request.form.get("price_eur") or "").replace(",", ".").strip()

    # Validation
    try:
        volume_l = int(volume_l_str)
    except Exception:
        volume_l = 0

    try:
        price_cents = int(round(float(price_eur_str) * 100))
    except Exception:
        price_cents = -1

    if not name or volume_l not in (20, 22, 30) or price_cents < 0:
        flash("Données invalides. Vérifie le nom, le volume (20/22/30) et le prix.", "danger")
        return redirect(url_for("catalog.catalog_index"))

    # Règles métier
    name_low = name.lower()
    if "ambrée" in name_low and "coreff" in name_low and volume_l != 22:
        flash("Règle : COREFF Ambrée uniquement en 22L.", "danger")
        return redirect(url_for("catalog.catalog_index"))
    if any(k in name_low for k in ["blanche", "rousse", "cidre"]) and volume_l == 30:
        flash("Règle : Blanche / Rousse / Cidre ne se font pas en 30L.", "danger")
        return redirect(url_for("catalog.catalog_index"))

    # UPSERT
    with db.engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO products (name, family, volume_l, price_cents)
            VALUES (:name, :family, :volume_l, :price_cents)
            ON CONFLICT (name, volume_l)
            DO UPDATE SET
                family = EXCLUDED.family,
                price_cents = EXCLUDED.price_cents,
                is_active = TRUE
        """), {
            "name": name,
            "family": family,
            "volume_l": volume_l,
            "price_cents": price_cents,
        })

    flash("Produit enregistré.", "success")
    return redirect(url_for("catalog.catalog_index"))

# Enregistrer le blueprint
app.register_blueprint(catalog_bp)

# Certains anciens templates utilisaient url_for('catalog') directement;
# On ajoute une règle qui redirige vers l'endpoint correct du blueprint.
@app.route("/catalog", endpoint="catalog")
def _catalog_redirect():
    return redirect(url_for("catalog.catalog_index"))

# ---------------------------------------------------------
# Démarrage/Seed
# ---------------------------------------------------------
with app.app_context():
    # IMPORTANT: crée la table products si besoin et importe le CSV à froid
    ensure_products_table_and_seed()

# ---------------------------------------------------------
# Entrypoint (utile en local); sur Render c'est 'gunicorn app:app'
# ---------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
