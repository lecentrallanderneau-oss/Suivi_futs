import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, abort, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text, select, func

# -----------------------------------------------------------------------------
# Config app & DB
# -----------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")

# Préfère DATABASE_URL si présent (Render / Postgres), sinon SQLite fichier
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    # Correction Render (urls heroku-style): postgres:// -> postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///data.db"

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


# -----------------------------------------------------------------------------
# Modèles
# -----------------------------------------------------------------------------
class Client(db.Model):
    __tablename__ = "client"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    # La colonne qui posait problème en prod : nous l'avons dans le modèle
    contact = db.Column(db.String(120), nullable=True)

    movements = db.relationship("Movement", backref="client", cascade="all, delete-orphan")


class Movement(db.Model):
    __tablename__ = "movement"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    type = db.Column(db.String(20), nullable=False)  # 'livraison' ou 'reprise'
    product = db.Column(db.String(200), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


# -----------------------------------------------------------------------------
# Garde-fou schéma : création tables + ajout colonne manquante
# -----------------------------------------------------------------------------
def ensure_schema():
    """
    1) Crée les tables qui n'existent pas
    2) Ajoute la colonne client.contact si elle manque (Postgres & SQLite)
    """
    # 1) Crée tables manquantes (n'ajoute pas de colonnes manquantes)
    with app.app_context():
        db.create_all()

    # 2) Ajoute colonne client.contact si absente
    with db.engine.begin() as conn:
        backend = db.engine.url.get_backend_name()

        if backend == "postgresql":
            missing = conn.execute(text("""
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'client' AND column_name = 'contact'
                LIMIT 1
            """)).first() is None

            if missing:
                conn.execute(text("ALTER TABLE client ADD COLUMN contact VARCHAR(120)"))

        else:
            # SQLite (dev/local)
            rows = conn.execute(text("PRAGMA table_info(client)")).fetchall()
            names = {row[1] for row in rows}  # row[1] = column name
            if "contact" not in names:
                conn.execute(text("ALTER TABLE client ADD COLUMN contact TEXT"))


def safe_count_clients():
    """Count idempotent sans sélectionner toutes les colonnes (évite surprises)."""
    with app.app_context():
        return db.session.execute(
            select(func.count()).select_from(Client)
        ).scalar() or 0


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def compute_client_stock(client_id: int) -> int:
    """Stock = livraisons - reprises."""
    q = db.session.execute(
        select(
            func.sum(func.case((Movement.type == "livraison", Movement.quantity), else_=0)),
            func.sum(func.case((Movement.type == "reprise", Movement.quantity), else_=0)),
        ).where(Movement.client_id == client_id)
    ).first()
    if not q:
        return 0
    liv, rep = q
    liv = liv or 0
    rep = rep or 0
    return int(liv - rep)


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.route("/")
def index():
    ensure_schema()  # Toujours avant premières requêtes
    # Liste clients, tri par nom
    clients = Client.query.order_by(Client.name).all()
    # Calcule un stock rapide par client (facultatif)
    stocks = {c.id: compute_client_stock(c.id) for c in clients}
    return render_template("index.html", clients=clients, stocks=stocks)


@app.route("/clients/<int:client_id>")
def client_detail(client_id):
    ensure_schema()
    client = Client.query.get_or_404(client_id)
    movements = Movement.query.filter_by(client_id=client.id).order_by(Movement.date.desc()).all()
    client_stock = compute_client_stock(client.id)
    return render_template(
        "client_detail.html",
        client=client,
        movements=movements,
        client_stock=client_stock,
    )


@app.route("/clients/add", methods=["POST"])
def add_client():
    ensure_schema()
    name = (request.form.get("name") or "").strip()
    contact = (request.form.get("contact") or "").strip() or None
    if not name:
        flash("Le nom du client est obligatoire.", "warning")
        return redirect(url_for("index"))

    # Unicité simple par nom
    exists = db.session.execute(
        select(Client.id).where(func.lower(Client.name) == name.lower())
    ).first()
    if exists:
        flash("Ce client existe déjà.", "warning")
        return redirect(url_for("index"))

    c = Client(name=name, contact=contact)
    db.session.add(c)
    db.session.commit()
    flash("Client ajouté.", "success")
    return redirect(url_for("index"))


@app.route("/clients/<int:client_id>/delete", methods=["POST"])
def delete_client(client_id):
    ensure_schema()
    client = Client.query.get_or_404(client_id)
    db.session.delete(client)  # cascade supprime ses mouvements
    db.session.commit()
    flash("Client supprimé.", "success")
    return redirect(url_for("index"))


@app.route("/clients/<int:client_id>/movements/add", methods=["POST"])
def add_movement(client_id):
    ensure_schema()
    client = Client.query.get_or_404(client_id)

    mtype = request.form.get("type")
    product = (request.form.get("product") or "").strip()
    quantity = request.form.get("quantity", type=int)

    if mtype not in ("livraison", "reprise"):
        flash("Type invalide.", "danger")
        return redirect(url_for("client_detail", client_id=client.id))

    if not product or not quantity or quantity <= 0:
        flash("Produit et quantité sont obligatoires (quantité > 0).", "warning")
        return redirect(url_for("client_detail", client_id=client.id))

    m = Movement(
        client_id=client.id,
        type=mtype,
        product=product,
        quantity=quantity,
        date=datetime.utcnow(),
    )
    db.session.add(m)
    db.session.commit()
    flash("Mouvement enregistré.", "success")
    return redirect(url_for("client_detail", client_id=client.id))


@app.route("/movements/<int:movement_id>/delete", methods=["POST"])
def delete_movement(movement_id):
    ensure_schema()
    m = Movement.query.get_or_404(movement_id)
    cid = m.client_id
    db.session.delete(m)
    db.session.commit()
    flash("Mouvement supprimé.", "success")
    return redirect(url_for("client_detail", client_id=cid))


# -----------------------------------------------------------------------------
# Lancement
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # Démarrage local
    ensure_schema()
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
