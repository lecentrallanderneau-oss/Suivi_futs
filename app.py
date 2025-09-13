import os
from datetime import datetime, date

from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy

# ---------------------------------------------------------------------
# App & DB
# ---------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-key")

# Render te fournit DATABASE_URL (PostgreSQL). En local, tu peux mettre sqlite:///local.db
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///local.db")
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# ---------------------------------------------------------------------
# Filters (Jinja)
# ---------------------------------------------------------------------
@app.template_filter("eur")
def eur_filter(cents: int | None) -> str:
    try:
        v = (cents or 0) / 100
        return f"{v:,.2f} €".replace(",", " ").replace(".", ",")
    except Exception:
        return "0,00 €"

# ---------------------------------------------------------------------
# Modèles
# ---------------------------------------------------------------------
class Client(db.Model):
    __tablename__ = "clients"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, unique=True)

class Product(db.Model):
    """
    Un produit représente une référence unique par (nom, volume_l).
    Exemple:
      name="COREFF Ambrée", volume_l=22 -> prix 78€ => price_cents=7800
    """
    __tablename__ = "products"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)          # ex: "COREFF Ambrée"
    volume_l = db.Column(db.Integer, nullable=False)           # ex: 22
    price_cents = db.Column(db.Integer, nullable=True)         # prix TTC d'1 fût
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    __table_args__ = (
        db.UniqueConstraint("name", "volume_l", name="uq_product_name_volume"),
    )

class Move(db.Model):
    """
    Mouvement de fûts pour 1 client à une date donnée, ventilé par produit.
    - qty_out     : fûts livrés (sortis de chez toi)
    - qty_in      : fûts repris (rentrés)
    - qty_defect  : fûts défectueux (déconsignés, non facturés)
    La consigne = 30€ * (qty_out - qty_in - qty_defect).
    """
    __tablename__ = "moves"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    date = db.Column(db.Date, nullable=False, default=date.today)

    qty_out = db.Column(db.Integer, nullable=False, default=0)
    qty_in = db.Column(db.Integer, nullable=False, default=0)
    qty_defect = db.Column(db.Integer, nullable=False, default=0)

    note = db.Column(db.String(500), nullable=True)

    client = db.relationship("Client")
    product = db.relationship("Product")

# ---------------------------------------------------------------------
# Bootstrap de la base + seed du catalogue
# ---------------------------------------------------------------------
def ensure_schema_and_seed():
    db.create_all()

    # Seed clients si vide (facultatif)
    if Client.query.count() == 0:
        db.session.add_all([Client(name="Client A"), Client(name="Client B")])
        db.session.commit()

    # Seed catalogue selon tes règles
    SEED = [
        # COREFF Ambrée — uniquement 22L, 78€
        {"name": "COREFF Ambrée", "volumes": [22], "price_map": {22: 7800}},

        # Blanche, Rousse, Cidre — pas de 30L (on crée 20L et 22L, prix inconnus -> None)
        {"name": "COREFF Blanche", "volumes": [20, 22], "price_map": {}},
        {"name": "COREFF Rousse",  "volumes": [20, 22], "price_map": {}},
        {"name": "Cidre",          "volumes": [20, 22], "price_map": {}},
    ]

    created = 0
    for item in SEED:
        for v in item["volumes"]:
            exists = Product.query.filter_by(name=item["name"], volume_l=v).first()
            if not exists:
                price_cents = item.get("price_map", {}).get(v)
                db.session.add(Product(name=item["name"], volume_l=v, price_cents=price_cents, is_active=True))
                created += 1
    if created:
        db.session.commit()

with app.app_context():
    ensure_schema_and_seed()

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
DEPOSIT_PER_KEG_CENTS = 3000  # 30 €

def client_totals(client_id: int) -> dict:
    """
    Renvoie un petit résumé (livrés, repris, défectueux, consigne).
    """
    agg = db.session.query(
        db.func.coalesce(db.func.sum(Move.qty_out), 0),
        db.func.coalesce(db.func.sum(Move.qty_in), 0),
        db.func.coalesce(db.func.sum(Move.qty_defect), 0),
    ).filter(Move.client_id == client_id).one()

    out_qty, in_qty, defect_qty = map(int, agg)
    deposit_cents = DEPOSIT_PER_KEG_CENTS * (out_qty - in_qty - defect_qty)

    return {
        "out_qty": out_qty,
        "in_qty": in_qty,
        "defect_qty": defect_qty,
        "deposit_cents": deposit_cents,
    }

# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------
@app.route("/")
def index():
    clients = Client.query.order_by(Client.name.asc()).all()

    # Somme globale des consignes (tous clients) pour l'encart d'accueil
    agg = db.session.query(
        db.func.coalesce(db.func.sum(Move.qty_out), 0),
        db.func.coalesce(db.func.sum(Move.qty_in), 0),
        db.func.coalesce(db.func.sum(Move.qty_defect), 0),
    ).one()
    out_qty, in_qty, defect_qty = map(int, agg)
    totals = {"deposit_cents": DEPOSIT_PER_KEG_CENTS * (out_qty - in_qty - defect_qty)}

    return render_template("index.html", clients=clients, totals=totals, now=datetime.now())

# -- Clients (liste + ajout simple)
@app.route("/clients", methods=["GET", "POST"])
def clients():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Nom obligatoire.", "warning")
            return redirect(url_for("clients"))
        if Client.query.filter_by(name=name).first():
            flash("Ce client existe déjà.", "warning")
        else:
            db.session.add(Client(name=name))
            db.session.commit()
            flash("Client ajouté.", "success")
        return redirect(url_for("clients"))

    items = Client.query.order_by(Client.name.asc()).all()
    return render_template("clients.html", clients=items)

# -- Fiche client (détail + saisie mouvements)
@app.route("/clients/<int:client_id>", methods=["GET", "POST"])
def client_detail(client_id: int):
    client = Client.query.get_or_404(client_id)

    if request.method == "POST":
        try:
            product_id = int(request.form.get("product_id") or 0)
        except Exception:
            product_id = 0

        mv_date_str = request.form.get("date") or ""
        try:
            mv_date = datetime.strptime(mv_date_str, "%Y-%m-%d").date()
        except Exception:
            mv_date = date.today()

        def _int(v): 
            try: 
                return max(0, int(v or 0))
            except Exception:
                return 0

        qty_out = _int(request.form.get("qty_out"))
        qty_in = _int(request.form.get("qty_in"))
        qty_defect = _int(request.form.get("qty_defect"))
        note = (request.form.get("note") or "").strip()

        product = Product.query.get(product_id)
        if not product or not product.is_active:
            flash("Produit invalide.", "warning")
            return redirect(url_for("client_detail", client_id=client_id))

        # Règles métier supplémentaires (volumes autorisés déjà gérés par le catalogue)
        if qty_out == qty_in == qty_defect == 0:
            flash("Renseigne au moins une quantité (livrée / reprise / défectueux).", "warning")
            return redirect(url_for("client_detail", client_id=client_id))

        mv = Move(
            client_id=client.id,
            product_id=product.id,
            date=mv_date,
            qty_out=qty_out,
            qty_in=qty_in,
            qty_defect=qty_defect,
            note=note if note else None,
        )
        db.session.add(mv)
        db.session.commit()
        flash("Mouvement enregistré.", "success")
        return redirect(url_for("client_detail", client_id=client_id))

    # GET
    products = Product.query.filter_by(is_active=True).order_by(Product.name.asc(), Product.volume_l.asc()).all()
    moves = (db.session.query(Move)
             .filter(Move.client_id == client.id)
             .order_by(Move.date.desc(), Move.id.desc())
             .all())
    totals = client_totals(client.id)

    return render_template("client_detail.html",
                           client=client,
                           products=products,
                           moves=moves,
                           totals=totals)

# -- Catalogue (listing + ajout produit)
@app.route("/catalog", methods=["GET", "POST"])
def catalog():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        volume_l = request.form.get("volume_l") or ""
        price_eur = request.form.get("price_eur") or ""  # champ libre
        is_active = True if request.form.get("is_active") == "on" else False

        try:
            volume_l = int(volume_l)
        except Exception:
            flash("Volume invalide.", "warning")
            return redirect(url_for("catalog"))

        price_cents = None
        if price_eur.strip():
            try:
                price_cents = int(round(float(price_eur.replace(",", ".").strip()) * 100))
            except Exception:
                flash("Prix invalide (ex: 78 ou 78,00).", "warning")
                return redirect(url_for("catalog"))

        if not name:
            flash("Nom du produit obligatoire.", "warning")
            return redirect(url_for("catalog"))

        # Interdictions métier : pas de 30L pour Blanche, Rousse, Cidre
        lowered = name.lower()
        if volume_l == 30 and any(k in lowered for k in ["blanche", "rousse", "cidre"]):
            flash("Règle: pas de 30 L pour Blanche / Rousse / Cidre.", "warning")
            return redirect(url_for("catalog"))

        # Ambrée uniquement 22L
        if "ambrée" in lowered or "ambree" in lowered:
            if volume_l != 22:
                flash("Règle: COREFF Ambrée uniquement en 22 L.", "warning")
                return redirect(url_for("catalog"))

        # Upsert “name+volume”
        existing = Product.query.filter_by(name=name, volume_l=volume_l).first()
        if existing:
            existing.price_cents = price_cents
            existing.is_active = is_active
            db.session.commit()
            flash("Produit mis à jour.", "success")
        else:
            db.session.add(Product(name=name, volume_l=volume_l, price_cents=price_cents, is_active=is_active))
            db.session.commit()
            flash("Produit ajouté.", "success")

        return redirect(url_for("catalog"))

    items = Product.query.order_by(Product.name.asc(), Product.volume_l.asc()).all()
    return render_template("catalog.html", products=items)

# ---------------------------------------------------------------------
# Run (local)
# ---------------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True, port=5000)
