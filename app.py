import os
from datetime import datetime, date

from flask import (
    Flask, render_template, request, redirect, url_for, flash
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text, inspect

# -----------------------------------------------------------------------------
# Config Flask + SQLAlchemy
# -----------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")

# Render fournit DATABASE_URL. On force le driver psycopg moderne si besoin.
db_url = os.environ.get("DATABASE_URL", "sqlite:///local.db")
if db_url.startswith("postgres://"):
    # ancien prefix Heroku -> psycopg3/SQLAlchemy attend 'postgresql://'
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

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

    def __repr__(self) -> str:
        return f"<Client {self.name}>"


class Product(db.Model):
    __tablename__ = "products"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)              # ex : "COREFF Ambrée"
    volume_l = db.Column(db.Integer, nullable=False, default=22)  # 22 ou 30
    price_cents = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    __table_args__ = (
        db.UniqueConstraint("name", "volume_l", name="uq_product_name_volume"),
    )

    def __repr__(self) -> str:
        return f"<Product {self.name} {self.volume_l}L {self.price_cents/100:.2f}€>"


class Movement(db.Model):
    __tablename__ = "movements"
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=date.today)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    qty_in = db.Column(db.Integer, nullable=False, default=0)   # livrés (pleins)
    qty_out = db.Column(db.Integer, nullable=False, default=0)  # repris (vides)
    defective = db.Column(db.Boolean, nullable=False, default=False)

    product = db.relationship("Product")

    def __repr__(self) -> str:
        return f"<Movement {self.date} C{self.client_id} P{self.product_id} +{self.qty_in} -{self.qty_out}{' DEF' if self.defective else ''}>"

# -----------------------------------------------------------------------------
# Jinja helpers
# -----------------------------------------------------------------------------
@app.template_filter("eur")
def as_eur(cents: int) -> str:
    try:
        cents = int(cents or 0)
    except Exception:
        cents = 0
    euros = cents / 100.0
    # format fr : 1 234,56 €
    return f"{euros:,.2f} €".replace(",", "X").replace(".", ",").replace("X", " ")

@app.context_processor
def inject_now():
    # permet d'utiliser now() dans les templates
    return {"now": datetime.utcnow}

# -----------------------------------------------------------------------------
# Création schéma + seed catalogue
# -----------------------------------------------------------------------------
def ensure_schema_and_seed():
    # 1) Crée les tables si absentes
    db.create_all()

    # 2) Ajoute la colonne volume_l sur products si elle n'existe pas (cas ancien schéma)
    insp = inspect(db.engine)
    cols = {c["name"] for c in insp.get_columns("products")} if insp.has_table("products") else set()
    if "volume_l" not in cols:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS volume_l INTEGER DEFAULT 22 NOT NULL;"))
        # il faut aussi forcer le cache d’inspection à se mettre à jour
        insp = inspect(db.engine)

    # 3) Seed du catalogue (idempotent)
    seed_items = [
        # COREFF Blonde : 22L et 30L — prix à ajuster si besoin
        {"name": "COREFF Blonde",  "volumes": {22: 0, 30: 0}, "active": True},
        # Contraintes demandées :
        {"name": "COREFF Ambrée",  "volumes": {22: 7800},     "active": True},  # 78 €
        {"name": "COREFF Blanche", "volumes": {22: 0},        "active": True},  # pas de 30L
        {"name": "COREFF Rousse",  "volumes": {22: 0},        "active": True},  # pas de 30L
        {"name": "Cidre Brut",     "volumes": {22: 0},        "active": True},  # pas de 30L
    ]

    for item in seed_items:
        for vol, price in item["volumes"].items():
            existing = Product.query.filter_by(name=item["name"], volume_l=vol).first()
            if existing:
                # on met à jour le prix/actif au passage (pratique si tu modifies plus tard)
                changed = False
                if existing.price_cents != price:
                    existing.price_cents = price
                    changed = True
                if existing.is_active != item["active"]:
                    existing.is_active = item["active"]
                    changed = True
                if changed:
                    db.session.add(existing)
            else:
                db.session.add(Product(
                    name=item["name"],
                    volume_l=vol,
                    price_cents=price,
                    is_active=item["active"]
                ))
    db.session.commit()

# -----------------------------------------------------------------------------
# Règles métier (volumes autorisés)
# -----------------------------------------------------------------------------
def is_volume_allowed(product_name: str, volume_l: int) -> bool:
    name = (product_name or "").strip().lower()
    if "ambrée" in name:
        return volume_l == 22
    if "blanche" in name:
        return volume_l == 22
    if "rousse" in name:
        return volume_l == 22
    if "cidre" in name:
        return volume_l == 22
    # Blonde : 22 et 30 autorisés
    return True

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.route("/")
def index():
    ensure_schema_and_seed()
    clients = Client.query.order_by(Client.name.asc()).all()
    return render_template("index.html", clients=clients, now=datetime.now())

@app.route("/clients")
def clients():
    clients = Client.query.order_by(Client.name.asc()).all()
    return render_template("clients.html", clients=clients)

@app.route("/clients/add", methods=["POST"])
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

@app.route("/client/<int:client_id>")
def client_detail(client_id: int):
    client = Client.query.get_or_404(client_id)
    # On charge les mouvements triés récents d’abord
    moves = (
        Movement.query.filter_by(client_id=client.id)
        .order_by(Movement.date.desc(), Movement.id.desc())
        .all()
    )
    # Produits actifs pour le sélecteur
    products = Product.query.filter_by(is_active=True).order_by(Product.name.asc(), Product.volume_l.asc()).all()
    return render_template("client_detail.html", client=client, movements=moves, products=products)

@app.route("/client/<int:client_id>/movements/add", methods=["POST"])
def add_movement(client_id: int):
    client = Client.query.get_or_404(client_id)

    # date : si vide => aujourd’hui (UTC)
    date_raw = request.form.get("date")
    move_date = datetime.strptime(date_raw, "%Y-%m-%d").date() if date_raw else datetime.utcnow().date()

    try:
        product_id = int(request.form.get("product_id"))
    except Exception:
        flash("Produit invalide.", "danger")
        return redirect(url_for("client_detail", client_id=client.id))

    product = Product.query.get(product_id)
    if not product or not product.is_active:
        flash("Produit introuvable ou inactif.", "danger")
        return redirect(url_for("client_detail", client_id=client.id))

    # Règles volumes autorisés (sécurité côté serveur)
    if not is_volume_allowed(product.name, product.volume_l):
        flash(f"Volume non autorisé pour {product.name}.", "danger")
        return redirect(url_for("client_detail", client_id=client.id))

    def _to_int(val):
        try:
            return int(val or 0)
        except Exception:
            return 0

    qty_in = _to_int(request.form.get("qty_in"))
    qty_out = _to_int(request.form.get("qty_out"))
    defective = True if request.form.get("defective") in ("1", "on", "true", "True") else False

    if qty_in < 0 or qty_out < 0:
        flash("Les quantités doivent être positives.", "warning")
        return redirect(url_for("client_detail", client_id=client.id))

    mv = Movement(
        date=move_date,
        client_id=client.id,
        product_id=product.id,
        qty_in=qty_in,
        qty_out=qty_out,
        defective=defective
    )
    db.session.add(mv)
    db.session.commit()
    flash("Mouvement enregistré.", "success")
    return redirect(url_for("client_detail", client_id=client.id))

@app.route("/catalog")
def catalog():
    products = Product.query.order_by(Product.name.asc(), Product.volume_l.asc()).all()
    return render_template("catalog.html", products=products)

# -----------------------------------------------------------------------------
# Main (utile en local)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    ensure_schema_and_seed()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
