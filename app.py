import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy

# -----------------------------------------------------------------------------
# Config app & DB
# -----------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")

def normalize_db_url(url: str) -> str:
    # Compatibilité Render/Heroku qui donnent parfois postgres://
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    # Forcer l’utilisation du driver psycopg (v3) et non psycopg2
    if url.startswith("postgresql://") and "+psycopg" not in url and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url

DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    DATABASE_URL = normalize_db_url(DATABASE_URL)
    app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
else:
    # Fallback en local
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///data.db"

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------
class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    contact = db.Column(db.String(120), nullable=True)

class Movement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    type = db.Column(db.String(20), nullable=False)  # livraison ou reprise
    product = db.Column(db.String(120), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)

    client = db.relationship("Client", backref=db.backref("movements", cascade="all, delete-orphan"))

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.route("/")
def index():
    clients = Client.query.order_by(Client.name).all()
    return render_template("index.html", clients=clients, now=datetime.utcnow())

@app.route("/clients/<int:client_id>")
def client_detail(client_id):
    client = Client.query.get_or_404(client_id)
    movements = Movement.query.filter_by(client_id=client.id).order_by(Movement.date.desc()).all()
    # Stock = livraisons - reprises
    stock = sum(m.quantity if m.type == "livraison" else -m.quantity for m in movements)
    return render_template("client_detail.html", client=client, movements=movements, client_stock=stock)

@app.route("/clients/add", methods=["POST"])
def add_client():
    name = request.form.get("name")
    contact = request.form.get("contact")
    if not name:
        flash("Le nom du client est obligatoire.", "danger")
    else:
        client = Client(name=name, contact=contact)
        db.session.add(client)
        db.session.commit()
        flash("Client ajouté avec succès.", "success")
    return redirect(url_for("index"))

@app.route("/clients/<int:client_id>/delete", methods=["POST"])
def delete_client(client_id):
    client = Client.query.get_or_404(client_id)
    db.session.delete(client)
    db.session.commit()
    flash("Client supprimé avec succès.", "info")
    return redirect(url_for("index"))

@app.route("/clients/<int:client_id>/movements/add", methods=["POST"])
def add_movement(client_id):
    client = Client.query.get_or_404(client_id)
    product = request.form.get("product")
    quantity = request.form.get("quantity", type=int)
    type_ = request.form.get("type")
    if not product or not quantity or not type_:
        flash("Tous les champs sont obligatoires.", "danger")
    else:
        m = Movement(client_id=client.id, product=product, quantity=quantity, type=type_)
        db.session.add(m)
        db.session.commit()
        flash("Mouvement enregistré.", "success")
    return redirect(url_for("client_detail", client_id=client.id))

@app.route("/movements/<int:movement_id>/delete", methods=["POST"])
def delete_movement(movement_id):
    m = Movement.query.get_or_404(movement_id)
    client_id = m.client_id
    db.session.delete(m)
    db.session.commit()
    flash("Mouvement supprimé.", "info")
    return redirect(url_for("client_detail", client_id=client_id))

# -----------------------------------------------------------------------------
# Init DB
# -----------------------------------------------------------------------------
def ensure_db():
    with app.app_context():
        db.create_all()

if __name__ == "__main__":
    ensure_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
