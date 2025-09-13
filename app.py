import os
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

# -----------------------------------------------------------------------------
# Configuration de l’application
# -----------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev")

# Configuration SQLite (simple et portable)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///suivi_futs.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# -----------------------------------------------------------------------------
# Modèles
# -----------------------------------------------------------------------------
class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    contact = db.Column(db.String(120), nullable=True)
    movements = db.relationship("Movement", backref="client", lazy=True)

class Movement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    product = db.Column(db.String(120), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    type = db.Column(db.String(20), nullable=False)  # "livraison" ou "reprise"
    date = db.Column(db.DateTime, default=datetime.utcnow)

# -----------------------------------------------------------------------------
# Context processor → rend 'now' disponible dans tous les templates
# -----------------------------------------------------------------------------
@app.context_processor
def inject_now():
    return {"now": datetime.utcnow()}

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
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
    product = request.form["product"]
    quantity = int(request.form["quantity"])
    mov_type = request.form["type"]

    movement = Movement(client_id=client.id, product=product, quantity=quantity, type=mov_type)
    db.session.add(movement)
    db.session.commit()
    flash("Mouvement enregistré avec succès.", "success")
    return redirect(url_for("client_detail", client_id=client.id))

@app.route("/clients/new", methods=["GET", "POST"])
def new_client():
    if request.method == "POST":
        name = request.form["name"]
        contact = request.form.get("contact", "")
        client = Client(name=name, contact=contact)
        db.session.add(client)
        db.session.commit()
        flash("Client ajouté avec succès.", "success")
        return redirect(url_for("index"))
    return render_template("new_client.html")

# -----------------------------------------------------------------------------
# Lancement local
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True, host="0.0.0.0", port=5000)
