import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy

# ---------------------------------------------------------------------
# Config DB (normalisation Render -> SQLAlchemy psycopg v3)
# ---------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def normalize_db_url(url: str | None) -> str:
    """
    Convertit toutes les variantes Render/Heroku en URL explicite psycopg v3.
    - postgres://...                -> postgresql+psycopg://...
    - postgresql://... (sans driver) -> postgresql+psycopg://...
    Laisse inchangé si déjà 'postgresql+psycopg://'.
    """
    if not url or url.strip() == "":
        return "sqlite:///" + os.path.join(BASE_DIR, "suivi_futs.db")

    url = url.strip()

    if url.startswith("postgresql+psycopg://"):
        return url

    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)

    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)

    # fallback : renvoie tel quel (utile pour sqlite:/// ou autres)
    return url

DATABASE_URL = normalize_db_url(os.getenv("DATABASE_URL"))

# ---------------------------------------------------------------------
# App Flask
# ---------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev")
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# ---------------------------------------------------------------------
# Modèles
# ---------------------------------------------------------------------
class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    contact = db.Column(db.String(120), nullable=True)
    movements = db.relationship(
        "Movement", backref="client", lazy=True, cascade="all, delete-orphan"
    )

class Movement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    product = db.Column(db.String(120), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    type = db.Column(db.String(20), nullable=False)  # "livraison" | "reprise"
    date = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

# ---------------------------------------------------------------------
# Helpers template
# ---------------------------------------------------------------------
@app.context_processor
def inject_now():
    return {"now": datetime.utcnow()}

# ---------------------------------------------------------------------
# Init DB au démarrage (Gunicorn compris)
# ---------------------------------------------------------------------
def ensure_db():
    db.create_all()
    if Client.query.count() == 0:
        c1 = Client(name="Client Démo", contact="demo@example.com")
        db.session.add(c1)
        db.session.flush()
        db.session.add_all([
            Movement(client_id=c1.id, product="Fût COREFF Blonde 20L", quantity=5, type="livraison"),
            Movement(client_id=c1.id, product="Fût COREFF Blonde 20L", quantity=2, type="reprise"),
        ])
        db.session.commit()

with app.app_context():
    ensure_db()

# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------
@app.route("/")
def index():
    clients = Client.query.order_by(Client.name).all()
    return render_template("index.html", clients=clients)

@app.route("/clients/<int:client_id>")
def client_detail(client_id):
    client = Client.query.get_or_404(client_id)
    return render_template("client_detail.html", client=client)

@app.route("/clients/<int:client_id>/add_movement", methods=["POST"])
def add_movement(client_id):
    client = Client.query.get_or_404(client_id)
    product = (request.form.get("product") or "").strip()
    quantity = int(request.form.get("quantity") or 0)
    mov_type = (request.form.get("type") or "").strip()  # "livraison" | "reprise"

    if not product or quantity <= 0 or mov_type not in ("livraison", "reprise"):
        flash("Vérifie le produit, la quantité (>0) et le type (livraison/reprise).", "danger")
        return redirect(url_for("client_detail", client_id=client.id))

    movement = Movement(client_id=client.id, product=product, quantity=quantity, type=mov_type)
    db.session.add(movement)
    db.session.commit()
    flash("Mouvement enregistré avec succès.", "success")
    return redirect(url_for("client_detail", client_id=client.id))

@app.route("/clients/new", methods=["GET", "POST"])
def new_client():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        contact = (request.form.get("contact") or "").strip()
        if not name:
            flash("Le nom du client est obligatoire.", "danger")
            return redirect(url_for("new_client"))

        client = Client(name=name, contact=contact)
        db.session.add(client)
        db.session.commit()
        flash("Client ajouté avec succès.", "success")
        return redirect(url_for("index"))

    return render_template("new_client.html")

# ---------------------------------------------------------------------
# Local run
# ---------------------------------------------------------------------
if __name__ == "__main__":
    with app.app_context():
        ensure_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
