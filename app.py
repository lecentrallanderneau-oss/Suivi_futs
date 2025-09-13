import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy

# ---------------------------------------------------------------------
# App & Config
# ---------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Si tu veux passer sur Postgres plus tard, mets DATABASE_URL dans Render.
# Sinon, SQLite local par défaut (fichier dans le dossier de l’app).
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL:
    # Compat Render / SQLAlchemy
    # ex: postgres:// -> postgresql+psycopg://
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://")
else:
    DATABASE_URL = "sqlite:///" + os.path.join(BASE_DIR, "suivi_futs.db")

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev")
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# ---------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------
class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    contact = db.Column(db.String(120), nullable=True)
    movements = db.relationship("Movement", backref="client", lazy=True, cascade="all, delete-orphan")

class Movement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    product = db.Column(db.String(120), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    type = db.Column(db.String(20), nullable=False)  # "livraison" | "reprise"
    date = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

# ---------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------
@app.context_processor
def inject_now():
    # Permet d'utiliser {{ now.strftime("%d/%m/%Y %H:%M") }} dans tous les templates
    return {"now": datetime.utcnow()}

# ---------------------------------------------------------------------
# DB init (important pour Render/Gunicorn)
# ---------------------------------------------------------------------
def ensure_db():
    """Crée les tables si elles n'existent pas et seed minimal si vide."""
    db.create_all()

    # Seed léger si aucun client, pour éviter une page vide au 1er chargement
    if Client.query.count() == 0:
        c1 = Client(name="Client Démo", contact="demo@example.com")
        db.session.add(c1)
        db.session.flush()  # pour avoir c1.id

        demo_movs = [
            Movement(client_id=c1.id, product="Fût COREFF Blonde 20L", quantity=5, type="livraison"),
            Movement(client_id=c1.id, product="Fût COREFF Blonde 20L", quantity=2, type="reprise"),
        ]
        db.session.add_all(demo_movs)
        db.session.commit()

# Appelé au chargement du module (et donc au démarrage Gunicorn)
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
    mov_type = (request.form.get("type") or "").strip()  # "livraison" ou "reprise"

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
