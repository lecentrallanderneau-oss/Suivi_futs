import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy

# -------------------------
# Configuration de l’application
# -------------------------
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev_key")

# Récupération et correction de l’URL de la base Render
raw_db_url = os.getenv("DATABASE_URL")
if not raw_db_url:
    raise RuntimeError("DATABASE_URL manquant dans les variables d'environnement.")

# Forcer psycopg v3 comme driver
if raw_db_url.startswith("postgres://"):
    db_url = raw_db_url.replace("postgres://", "postgresql+psycopg://", 1)
elif raw_db_url.startswith("postgresql://") and "+psycopg" not in raw_db_url:
    db_url = raw_db_url.replace("postgresql://", "postgresql+psycopg://", 1)
else:
    db_url = raw_db_url

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# -------------------------
# Modèles
# -------------------------
class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    movements = db.relationship("Movement", backref="client", lazy=True)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    volume_l = db.Column(db.Integer, nullable=False)  # Ex: 22 ou 30
    price_cents = db.Column(db.Integer, nullable=False)
    is_active = db.Column(db.Boolean, default=True)

class Movement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    qty_in = db.Column(db.Integer, default=0)   # Livraisons
    qty_out = db.Column(db.Integer, default=0)  # Reprises
    defective = db.Column(db.Boolean, default=False)
    date = db.Column(db.Date, default=datetime.utcnow)
    product = db.relationship("Product")

# -------------------------
# Initialisation DB + seed
# -------------------------
def ensure_schema_and_seed():
    with app.app_context():
        db.create_all()

        # Catalogue initial
        catalog = [
            {"name": "COREFF Ambrée", "volumes": [22], "price_cents": 7800},
            {"name": "COREFF Blanche", "volumes": [22], "price_cents": 7800},
            {"name": "COREFF Rousse", "volumes": [22], "price_cents": 7800},
            {"name": "COREFF Blonde", "volumes": [22, 30], "price_cents": 7800},
            {"name": "Cidre Brut", "volumes": [22], "price_cents": 7800},
        ]

        for item in catalog:
            for v in item["volumes"]:
                exists = Product.query.filter_by(name=item["name"], volume_l=v).first()
                if not exists:
                    db.session.add(Product(
                        name=item["name"],
                        volume_l=v,
                        price_cents=item["price_cents"],
                        is_active=True
                    ))
        db.session.commit()

# -------------------------
# Routes
# -------------------------
@app.route("/")
def index():
    clients = Client.query.all()
    return render_template("index.html", clients=clients, now=datetime.now())

@app.route("/clients")
def clients():
    clients = Client.query.all()
    return render_template("clients.html", clients=clients)

@app.route("/client/<int:client_id>")
def client_detail(client_id):
    client = Client.query.get_or_404(client_id)
    return render_template("client_detail.html", client=client)

@app.route("/catalog")
def catalog():
    products = Product.query.filter_by(is_active=True).all()
    return render_template("catalog.html", products=products)

@app.route("/add_client", methods=["POST"])
def add_client():
    name = request.form.get("name")
    if name:
        db.session.add(Client(name=name))
        db.session.commit()
        flash(f"Client {name} ajouté.", "success")
    return redirect(url_for("clients"))

@app.route("/add_movement/<int:client_id>", methods=["POST"])
def add_movement(client_id):
    client = Client.query.get_or_404(client_id)
    product_id = request.form.get("product_id")
    qty_in = int(request.form.get("qty_in") or 0)
    qty_out = int(request.form.get("qty_out") or 0)
    defective = bool(request.form.get("defective"))
    date = datetime.strptime(request.form.get("date"), "%Y-%m-%d")

    if product_id and (qty_in or qty_out):
        move = Movement(
            client_id=client.id,
            product_id=int(product_id),
            qty_in=qty_in,
            qty_out=qty_out,
            defective=defective,
            date=date
        )
        db.session.add(move)
        db.session.commit()
        flash("Mouvement enregistré.", "success")
    else:
        flash("Veuillez renseigner un produit et une quantité.", "danger")

    return redirect(url_for("client_detail", client_id=client.id))

# -------------------------
# Filtres Jinja2
# -------------------------
@app.template_filter("eur")
def format_eur(cents):
    return f"{cents/100:.2f} €"

# -------------------------
# Lancement
# -------------------------
if __name__ == "__main__":
    ensure_schema_and_seed()
    app.run(debug=True, host="0.0.0.0", port=5000)
