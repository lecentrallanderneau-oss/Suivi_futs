import os
from datetime import datetime, date

from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text

# --------------------------------------------------------------------
# Config de base
# --------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret")

# Render fournit DATABASE_URL ; SQLAlchemy préfère postgresql+psycopg
_db_url = os.getenv("DATABASE_URL") or os.getenv("EXTERNAL_DATABASE_URL")
if _db_url and _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql+psycopg://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = _db_url or "sqlite:///local.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# Constantes métier
DEPOSIT_CENTS_PER_KEG = 3000  # 30 €


# --------------------------------------------------------------------
# Jinja helpers (format €)
# --------------------------------------------------------------------
def eur(value_cents: int | float | None) -> str:
    try:
        cents = int(value_cents or 0)
    except Exception:
        cents = 0
    return f"{cents/100:,.2f} €".replace(",", " ").replace(".", ",")

app.jinja_env.filters["eur"] = eur


# --------------------------------------------------------------------
# Auto-init du schéma + seed catalogue
# --------------------------------------------------------------------
def ensure_schema_and_seed():
    """
    - Crée les tables si elles n'existent pas
    - Ajoute les colonnes manquantes si besoin
    - Insère le catalogue COREFF (avec règles de volumes/prix)
    """
    with db.engine.begin() as conn:
        # Tables
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS clients (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL UNIQUE,
                note TEXT DEFAULT ''
            );
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                volume_l INTEGER NOT NULL,
                price_cents INTEGER NOT NULL DEFAULT 0,
                is_active BOOLEAN NOT NULL DEFAULT TRUE
            );
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS keg_moves (
                id SERIAL PRIMARY KEY,
                client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                move_date DATE NOT NULL DEFAULT CURRENT_DATE,
                qty_out INTEGER NOT NULL DEFAULT 0,     -- fûts livrés (pleins)
                qty_in INTEGER NOT NULL DEFAULT 0,      -- fûts repris (vides)
                qty_defect INTEGER NOT NULL DEFAULT 0,  -- fûts défectueux (déconsignés, non facturés)
                comment TEXT DEFAULT ''
            );
        """))

        # Colonnes (au cas où la table existe déjà mais pas toutes les colonnes)
        conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS volume_l INTEGER NOT NULL DEFAULT 0;"))
        conn.execute(text("ALTER TABLE products ALTER COLUMN volume_l DROP DEFAULT;"))
        conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS price_cents INTEGER NOT NULL DEFAULT 0;"))
        conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;"))

        conn.execute(text("ALTER TABLE clients ADD COLUMN IF NOT EXISTS note TEXT DEFAULT '';"))

        conn.execute(text("ALTER TABLE keg_moves ADD COLUMN IF NOT EXISTS qty_out INTEGER NOT NULL DEFAULT 0;"))
        conn.execute(text("ALTER TABLE keg_moves ADD COLUMN IF NOT EXISTS qty_in INTEGER NOT NULL DEFAULT 0;"))
        conn.execute(text("ALTER TABLE keg_moves ADD COLUMN IF NOT EXISTS qty_defect INTEGER NOT NULL DEFAULT 0;"))
        conn.execute(text("ALTER TABLE keg_moves ADD COLUMN IF NOT EXISTS comment TEXT DEFAULT '';"))

        # Index utiles
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_keg_moves_client ON keg_moves(client_id);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_keg_moves_date ON keg_moves(move_date);"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_product_name_volume ON products(name, volume_l);"))

        # Seed catalogue COREFF
        # Règles données :
        # - COREFF Ambrée : uniquement 22 L à 78 €
        # - Blanche, Rousse, Cidre : pas de 30 L (on autorise 22 L ici, prix fictifs -> ajuste si besoin)
        coreff_items = [
            {"name": "COREFF Ambrée", "volumes": {22: 7800}},
            {"name": "COREFF Blanche", "volumes": {22: 7600}},  # 30L interdit
            {"name": "COREFF Rousse",  "volumes": {22: 7600}},  # 30L interdit
            {"name": "Cidre",          "volumes": {22: 8200}},  # 30L interdit
        ]
        # (Tu pourras compléter librement d'autres références plus tard)

        for item in coreff_items:
            for v, price in item["volumes"].items():
                # upsert simple
                conn.execute(text("""
                    INSERT INTO products (name, volume_l, price_cents, is_active)
                    VALUES (:name, :vol, :price, TRUE)
                    ON CONFLICT (name, volume_l) DO UPDATE
                    SET price_cents = EXCLUDED.price_cents,
                        is_active   = TRUE;
                """), {"name": item["name"], "vol": v, "price": price})


# --------------------------------------------------------------------
# Modèles ORM (simples)
# --------------------------------------------------------------------
class Client(db.Model):
    __tablename__ = "clients"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), unique=True, nullable=False)
    note = db.Column(db.Text, default="")


class Product(db.Model):
    __tablename__ = "products"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    volume_l = db.Column(db.Integer, nullable=False)
    price_cents = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)


class KegMove(db.Model):
    __tablename__ = "keg_moves"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    move_date = db.Column(db.Date, nullable=False, default=date.today)
    qty_out = db.Column(db.Integer, nullable=False, default=0)
    qty_in = db.Column(db.Integer, nullable=False, default=0)
    qty_defect = db.Column(db.Integer, nullable=False, default=0)
    comment = db.Column(db.Text, default="")


# --------------------------------------------------------------------
# Utils: agrégats consignes/fûts pour un client
# --------------------------------------------------------------------
def client_keg_summary(client_id: int) -> dict:
    """Retourne les totaux (sorties, entrées, défectueux) + dépôt théorique."""
    row = db.session.execute(text("""
        SELECT
            COALESCE(SUM(qty_out), 0)   AS out,
            COALESCE(SUM(qty_in), 0)    AS in,
            COALESCE(SUM(qty_defect),0) AS defect
        FROM keg_moves
        WHERE client_id = :cid
    """), {"cid": client_id}).one()

    qty_out = int(row.out or 0)
    qty_in = int(row.in or 0)
    qty_def = int(row.defect or 0)

    # Fûts "en cours" chez le client (sortis - entrés - défectueux)
    current = max(qty_out - qty_in - qty_def, 0)

    # Consigne théorique = 30 € * (sortis - entrés) ; les défectueux déconsignent
    deposit_cents = DEPOSIT_CENTS_PER_KEG * max(qty_out - qty_in - qty_def, 0)

    return {
        "qty_out": qty_out,
        "qty_in": qty_in,
        "qty_defect": qty_def,
        "current": current,
        "deposit_cents": deposit_cents,
    }


# --------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------
@app.route("/")
def index():
    clients = Client.query.order_by(Client.name.asc()).all()

    # Totaux globaux
    totals = {"current": 0, "deposit_cents": 0}
    per_client = {}
    for c in clients:
        s = client_keg_summary(c.id)
        per_client[c.id] = s
        totals["current"] += s["current"]
        totals["deposit_cents"] += s["deposit_cents"]

    return render_template("index.html",
                           clients=clients,
                           summaries=per_client,
                           totals=totals,
                           now=datetime.now())


@app.route("/clients", methods=["GET", "POST"])
def clients():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        note = (request.form.get("note") or "").strip()
        if not name:
            flash("Le nom du client est obligatoire.", "danger")
        else:
            exists = Client.query.filter_by(name=name).first()
            if exists:
                flash("Ce client existe déjà.", "warning")
            else:
                db.session.add(Client(name=name, note=note))
                db.session.commit()
                flash("Client créé.", "success")
        return redirect(url_for("clients"))

    clients = Client.query.order_by(Client.name.asc()).all()
    return render_template("clients.html", clients=clients)


@app.route("/client/<int:client_id>", methods=["GET", "POST"])
def client_detail(client_id: int):
    client = Client.query.get_or_404(client_id)

    if request.method == "POST":
        # Saisie de livraison/reprise/défectueux
        try:
            move_date = request.form.get("move_date") or str(date.today())
            move_date = datetime.strptime(move_date, "%Y-%m-%d").date()
        except Exception:
            move_date = date.today()

        qty_out = int(request.form.get("qty_out") or 0)
        qty_in = int(request.form.get("qty_in") or 0)
        qty_def = int(request.form.get("qty_defect") or 0)
        comment = (request.form.get("comment") or "").strip()

        if qty_out < 0 or qty_in < 0 or qty_def < 0:
            flash("Les quantités doivent être positives.", "danger")
            return redirect(url_for("client_detail", client_id=client_id))

        mv = KegMove(
            client_id=client.id,
            move_date=move_date,
            qty_out=qty_out,
            qty_in=qty_in,
            qty_defect=qty_def,
            comment=comment
        )
        db.session.add(mv)
        db.session.commit()
        flash("Mouvement enregistré.", "success")
        return redirect(url_for("client_detail", client_id=client_id))

    # GET
    moves = KegMove.query.filter_by(client_id=client.id)\
                         .order_by(KegMove.move_date.desc(), KegMove.id.desc())\
                         .all()
    summary = client_keg_summary(client.id)
    return render_template("client_detail.html", client=client, moves=moves, summary=summary, today=date.today())


@app.route("/catalog")
def catalog():
    products = Product.query.filter_by(is_active=True).order_by(Product.name.asc(), Product.volume_l.asc()).all()
    return render_template("catalog.html", products=products)


# --------------------------------------------------------------------
# Démarrage: auto-init schéma + seed
# --------------------------------------------------------------------
with app.app_context():
    ensure_schema_and_seed()


# --------------------------------------------------------------------
# WSGI
# --------------------------------------------------------------------
if __name__ == "__main__":
    # Pour lancer en local: python app.py
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
