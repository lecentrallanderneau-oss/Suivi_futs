# app.py
import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash
from dotenv import load_dotenv

from models import db, Client, Product, Movement

load_dotenv()

# ------------------------------------------------------------------
# App & DB
# ------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

# Render/Heroku donnent souvent une URL qui commence par postgres://
db_url = os.environ.get("DATABASE_URL", "sqlite:///local.db")
if db_url.startswith("postgres://"):
    # SQLAlchemy moderne + psycopg3
    db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
@app.template_filter("eur")
def eur_filter(value_cents: int) -> str:
    try:
        euros = (value_cents or 0) / 100.0
        return f"{euros:,.2f} €".replace(",", " ").replace(".", ",")
    except Exception:
        return "0,00 €"


def ensure_schema_and_seed():
    """
    - Crée les tables manquantes.
    - Ajoute les colonnes manquantes (ADD COLUMN IF NOT EXISTS) côté Postgres.
    - Seed du catalogue COREFF si vide (ou si le couple name/volume n'existe pas).
    """
    with app.app_context():
        # 1) Créer toutes les tables ORM manquantes
        db.create_all()

        # 2) Sécurité : si on est sur Postgres, on s'assure que les colonnes existent
        if app.config["SQLALCHEMY_DATABASE_URI"].startswith("postgresql"):
            from sqlalchemy import text
            ddl = [
                # products
                "ALTER TABLE IF EXISTS products ADD COLUMN IF NOT EXISTS volume_l INTEGER NOT NULL DEFAULT 0;",
                "ALTER TABLE IF EXISTS products ADD COLUMN IF NOT EXISTS price_cents INTEGER NOT NULL DEFAULT 0;",
                "ALTER TABLE IF EXISTS products ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;",
                "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_product_name_volume') THEN "
                "ALTER TABLE products ADD CONSTRAINT uq_product_name_volume UNIQUE (name, volume_l); END IF; END $$;",
                # movements
                "ALTER TABLE IF EXISTS movements ADD COLUMN IF NOT EXISTS unit_deposit_cents INTEGER NOT NULL DEFAULT 0;",
                "ALTER TABLE IF EXISTS movements ADD COLUMN IF NOT EXISTS unit_price_cents INTEGER NOT NULL DEFAULT 0;",
                "ALTER TABLE IF EXISTS movements ADD COLUMN IF NOT EXISTS note TEXT;",
            ]
            for stmt in ddl:
                db.session.execute(text(stmt))
            db.session.commit()

        # 3) Seed catalogue COREFF
        coreff_catalog = [
            # ⚠️ Tu ajusteras prix/volumes si besoin. J’ai mis 22L car on l’a vu passer dans tes logs,
            # et 30L qui est courant. Tu peux compléter à volonté.
            {"name": "COREFF Blonde", "volumes": [22, 30], "price_cents": 0},
            {"name": "COREFF Ambrée", "volumes": [22, 30], "price_cents": 0},
            {"name": "COREFF Blanche", "volumes": [22], "price_cents": 0},
            {"name": "COREFF IPA",     "volumes": [22], "price_cents": 0},
        ]

        created = 0
        for item in coreff_catalog:
            for vol in item["volumes"]:
                exists = Product.query.filter_by(name=item["name"], volume_l=vol).first()
                if not exists:
                    p = Product(
                        name=item["name"],
                        volume_l=vol,
                        price_cents=item.get("price_cents", 0),
                        is_active=True,
                    )
                    db.session.add(p)
                    created += 1
        if created:
            db.session.commit()
            app.logger.info("Catalogue COREFF seedé : %s produits créés", created)


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------
@app.route("/")
def index():
    ensure_schema_and_seed()
    clients = Client.query.order_by(Client.name.asc()).all()
    # On passe 'now' pour éviter l'erreur de template .strftime()
    return render_template("index.html", clients=clients, now=datetime.utcnow())


@app.route("/clients")
def clients():
    clients = Client.query.order_by(Client.name.asc()).all()
    return render_template("clients.html", clients=clients)


@app.route("/clients/<int:client_id>")
def client_detail(client_id: int):
    client = Client.query.get_or_404(client_id)

    # Récup mouvements récents (si tu veux les afficher plus tard)
    recent_movements = (
        Movement.query.filter_by(client_id=client_id)
        .order_by(Movement.created_at.desc())
        .limit(20)
        .all()
    )

    # Récap simple par produit (livraisons - reprises)
    from sqlalchemy import func, case
    balance_rows = (
        db.session.query(
            Product.name,
            Product.volume_l,
            func.sum(
                case((Movement.type == "delivery", Movement.quantity), else_=0)
            ).label("delivered"),
            func.sum(
                case((Movement.type == "pickup", Movement.quantity), else_=0)
            ).label("picked"),
        )
        .join(Product, Product.id == Movement.product_id)
        .filter(Movement.client_id == client_id)
        .group_by(Product.name, Product.volume_l)
        .all()
    )

    return render_template(
        "client_detail.html",
        client=client,
        recent_movements=recent_movements,
        balance_rows=balance_rows,
    )


# (Optionnel) API POST pour ajouter un mouvement — tu pourras brancher ton formulaire plus tard
@app.post("/clients/<int:client_id>/movements")
def add_movement(client_id: int):
    """
    Expects form fields:
      - product_id (int)
      - type ('delivery'|'pickup')
      - quantity (int)
      - unit_deposit_cents (int, optionnel)
      - unit_price_cents   (int, optionnel)
      - note (str, optionnel)
    """
    client = Client.query.get_or_404(client_id)

    try:
        product_id = int(request.form.get("product_id", "0"))
        move_type = request.form.get("type", "delivery")
        qty = int(request.form.get("quantity", "0"))

        unit_deposit_cents = int(request.form.get("unit_deposit_cents", "0") or 0)
        unit_price_cents = int(request.form.get("unit_price_cents", "0") or 0)
        note = request.form.get("note") or None

        if move_type not in ("delivery", "pickup"):
            raise ValueError("Type de mouvement invalide.")

        if qty <= 0:
            raise ValueError("La quantité doit être > 0.")

        # Vérifie produit
        product = Product.query.get(product_id)
        if not product:
            raise ValueError("Produit introuvable.")

        m = Movement(
            client_id=client.id,
            product_id=product.id,
            type=move_type,
            quantity=qty,
            unit_deposit_cents=unit_deposit_cents,
            unit_price_cents=unit_price_cents,
            note=note,
        )
        db.session.add(m)
        db.session.commit()
        flash("Mouvement enregistré ✅", "success")
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Erreur add_movement")
        flash(f"Erreur: {e}", "danger")

    return redirect(url_for("client_detail", client_id=client.id))


# ------------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------------
if __name__ == "__main__":
    with app.app_context():
        ensure_schema_and_seed()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
