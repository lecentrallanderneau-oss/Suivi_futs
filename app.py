# app.py
from __future__ import annotations
from datetime import datetime, date
import os

from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text

# -----------------------------------------------------------------------------
# Config & initialisation
# -----------------------------------------------------------------------------
def _normalize_db_url(url: str | None) -> str:
    """Assure l’usage du driver psycopg (v3) et un fallback SQLite en local."""
    if not url:
        return "sqlite:///local.db"
    # Render/Heroku fournissent parfois postgres:// -> on bascule vers postgresql+psycopg://
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.split("://", 1)[1]
    if url.startswith("postgresql://") and "+psycopg" not in url:
        return "postgresql+psycopg://" + url.split("://", 1)[1]
    return url

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret")
app.config["SQLALCHEMY_DATABASE_URI"] = _normalize_db_url(os.environ.get("DATABASE_URL"))
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}

db = SQLAlchemy(app)

# -----------------------------------------------------------------------------
# Modèles
# -----------------------------------------------------------------------------
class Client(db.Model):
    __tablename__ = "clients"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, unique=True)

    movements = db.relationship(
        "Movement", backref="client", lazy="dynamic", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Client {self.name}>"

class Product(db.Model):
    __tablename__ = "products"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)               # ex: "COREFF Ambrée"
    volume_l = db.Column(db.Integer, nullable=False, default=22)   # 22 ou 30
    price_cents = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    __table_args__ = (
        db.UniqueConstraint("name", "volume_l", name="uq_product_name_volume"),
    )

    def __repr__(self):
        return f"<Product {self.name} {self.volume_l}L {self.price_cents/100:.2f}€>"

class Movement(db.Model):
    __tablename__ = "movements"
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=date.today)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    qty_in = db.Column(db.Integer, nullable=False, default=0)    # livrés
    qty_out = db.Column(db.Integer, nullable=False, default=0)   # repris
    defective = db.Column(db.Boolean, nullable=False, default=False)

    product = db.relationship("Product")

    def __repr__(self):
        return (f"<Movement {self.date} C{self.client_id} P{self.product_id} "
                f"+{self.qty_in} -{self.qty_out}{' DEF' if self.defective else ''}>")

# -----------------------------------------------------------------------------
# Mise à niveau du schéma existant (safe sur Postgres & SQLite)
# -----------------------------------------------------------------------------
_schema_done = False

def ensure_schema():
    """
    - Crée toutes les tables si absentes
    - Ajoute les colonnes manquantes dans 'products' (volume_l, price_cents, is_active)
    - Ajoute la contrainte d'unicité (name, volume_l) si absente
    """
    db.create_all()

    insp = inspect(db.engine)
    # 1) Colonnes manquantes
    if insp.has_table("products"):
        cols = {c["name"] for c in insp.get_columns("products")}
        alter_sqls = []

        if "volume_l" not in cols:
            # DEFAULT 22 NOT NULL
            alter_sqls.append(
                "ALTER TABLE products ADD COLUMN IF NOT EXISTS volume_l INTEGER DEFAULT 22 NOT NULL"
            )
        if "price_cents" not in cols:
            alter_sqls.append(
                "ALTER TABLE products ADD COLUMN IF NOT EXISTS price_cents INTEGER DEFAULT 0 NOT NULL"
            )
        if "is_active" not in cols:
            alter_sqls.append(
                "ALTER TABLE products ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE NOT NULL"
            )

        if alter_sqls:
            with db.engine.begin() as conn:
                for sql in alter_sqls:
                    conn.execute(text(sql))

        # 2) Contrainte d'unicité name+volume_l
        # Vérifie si la contrainte existe déjà
        existing_uks = set()
        try:
            for uk in insp.get_unique_constraints("products"):
                name = uk.get("name")
                if name:
                    existing_uks.add(name)
        except Exception:
            pass

        if "uq_product_name_volume" not in existing_uks:
            # On vérifie aussi côté catalogue système (au cas où le nom diffère)
            # et on ne crée que si non présente.
            with db.engine.begin() as conn:
                # Sur certaines versions de PG, IF NOT EXISTS n'est pas dispo pour ADD CONSTRAINT.
                # On protège en testant la présence logique (via pg_constraint) puis création.
                conn.execute(text("""
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        WHERE rel.relname = 'products' AND con.conname = 'uq_product_name_volume'
    ) THEN
        ALTER TABLE products
        ADD CONSTRAINT uq_product_name_volume UNIQUE (name, volume_l);
    END IF;
END $$;
"""))

def ensure_schema_once():
    global _schema_done
    if not _schema_done:
        ensure_schema()
        _schema_done = True

# -----------------------------------------------------------------------------
# Filtres & contextes Jinja
# -----------------------------------------------------------------------------
@app.template_filter("eur")
def jinja_eur(cents):
    try:
        cents = int(cents or 0)
    except Exception:
        cents = 0
    euros = cents / 100.0
    # format fr : espace comme séparateur milliers, virgule comme décimale
    return f"{euros:,.2f} €".replace(",", "X").replace(".", ",").replace("X", " ")

@app.context_processor
def inject_now():
    return {"now": datetime.utcnow}

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.before_request
def _before_any_request():
    # garantit que le schéma est OK avant toute requête (évite l'erreur UndefinedColumn)
    ensure_schema_once()

@app.route("/")
def index():
    clients = Client.query.order_by(Client.name.asc()).all()
    return render_template("index.html", clients=clients)

# --- Catalogue (lecture) ---
@app.route("/catalog")
def catalog():
    products = Product.query.order_by(Product.name.asc(), Product.volume_l.asc()).all()
    return render_template("catalog.html", products=products)

# --- Clients : liste + création ---
@app.get("/clients")
def clients():
    clis = Client.query.order_by(Client.name.asc()).all()
    return render_template("clients.html", clients=clis)

@app.post("/clients/add")
def add_client():
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Nom du client requis.", "warning")
        return redirect(url_for("clients"))
    if Client.query.filter_by(name=name).first():
        flash("Ce client existe déjà.", "warning")
        return redirect(url_for("clients"))
    db.session.add(Client(name=name))
    db.session.commit()
    flash("Client ajouté.", "success")
    return redirect(url_for("clients"))

# --- Détail client + saisie mouvements ---
@app.get("/clients/<int:client_id>")
def client_detail(client_id: int):
    client = Client.query.get_or_404(client_id)
    moves = (
        Movement.query.filter_by(client_id=client.id)
        .order_by(Movement.date.desc(), Movement.id.desc())
        .all()
    )
    products = Product.query.filter_by(is_active=True).order_by(
        Product.name.asc(), Product.volume_l.asc()
    ).all()
    return render_template("client_detail.html",
                           client=client, movements=moves, products=products)

@app.post("/clients/<int:client_id>/movements/add")
def add_movement(client_id: int):
    client = Client.query.get_or_404(client_id)

    # Date
    date_raw = request.form.get("date")
    if date_raw:
        try:
            move_date = datetime.strptime(date_raw, "%Y-%m-%d").date()
        except Exception:
            move_date = datetime.utcnow().date()
    else:
        move_date = datetime.utcnow().date()

    # Produit
    try:
        product_id = int(request.form.get("product_id"))
    except Exception:
        flash("Produit invalide.", "danger")
        return redirect(url_for("client_detail", client_id=client.id))

    product = Product.query.get(product_id)
    if not product or not product.is_active:
        flash("Produit introuvable ou inactif.", "danger")
        return redirect(url_for("client_detail", client_id=client.id))

    # Quantités (toujours positives)
    def to_int(v):
        try:
            return int(v or 0)
        except Exception:
            return 0

    qty_in = to_int(request.form.get("qty_in"))
    qty_out = to_int(request.form.get("qty_out"))
    defective = request.form.get("defective") in ("1", "on", "true", "True")

    if qty_in < 0 or qty_out < 0:
        flash("Les quantités doivent être positives.", "warning")
        return redirect(url_for("client_detail", client_id=client.id))

    mv = Movement(
        date=move_date,
        client_id=client.id,
        product_id=product.id,
        qty_in=qty_in,
        qty_out=qty_out,
        defective=defective,
    )
    db.session.add(mv)
    db.session.commit()
    flash("Mouvement enregistré.", "success")
    return redirect(url_for("client_detail", client_id=client.id))

# -----------------------------------------------------------------------------
# Templates minimaux (si tu as déjà tes fichiers, garde-les)
# -----------------------------------------------------------------------------
# NOTE: Ce code suppose que tu as déjà :
# templates/base.html, templates/index.html, templates/clients.html,
# templates/client_detail.html, templates/catalog.html
# (Ce sont ceux que nous avons échangés plus tôt.)


# -----------------------------------------------------------------------------
# Point d’entrée gunicorn
# -----------------------------------------------------------------------------
# Sur Render : Start command = `gunicorn app:app`
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # Exécution locale : python app.py
    app.run(host="0.0.0.0", port=5000, debug=True)
