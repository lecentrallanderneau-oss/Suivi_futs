import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from dotenv import load_dotenv

# Charger les variables d’environnement (.env sur Render)
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev")

# Config BDD Postgres Render
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# ---- Constante : montant de la consigne par fût ----
DEPOSIT_CENTS_PER_KEG = 3000  # 30,00 €


# =====================
# Modèles SQLAlchemy
# =====================

class Client(db.Model):
    __tablename__ = "clients"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)

class Product(db.Model):
    __tablename__ = "products"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    volume_l = db.Column(db.Integer, nullable=False)  # ex: 22, 30
    price_cents = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, default=True)

class KegMove(db.Model):
    __tablename__ = "keg_moves"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=True)
    qty_out = db.Column(db.Integer, default=0)     # fûts livrés
    qty_in = db.Column(db.Integer, default=0)      # fûts repris
    qty_defect = db.Column(db.Integer, default=0)  # fûts défectueux
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# =====================
# Fonction utilitaire
# =====================

def client_keg_summary(client_id: int) -> dict:
    """
    Retourne un résumé des fûts pour un client :
    - fûts livrés, repris, défectueux
    - nombre actuel de fûts encore chez le client
    - montant de consigne associé
    """

    res = db.session.execute(
        text("""
            SELECT
                COALESCE(SUM(qty_out), 0)   AS qty_out_sum,
                COALESCE(SUM(qty_in), 0)    AS qty_in_sum,
                COALESCE(SUM(qty_defect),0) AS qty_def_sum
            FROM keg_moves
            WHERE client_id = :cid
        """),
        {"cid": client_id},
    ).mappings().one()  # renvoie un dict accessible avec ["nom_colonne"]

    qty_out = int(res["qty_out_sum"])
    qty_in = int(res["qty_in_sum"])
    qty_def = int(res["qty_def_sum"])

    # Calcul du nombre de fûts actuels
    current = max(qty_out - qty_in - qty_def, 0)

    # Calcul de la consigne totale
    deposit_cents = DEPOSIT_CENTS_PER_KEG * current

    return {
        "qty_out": qty_out,
        "qty_in": qty_in,
        "qty_defect": qty_def,
        "current": current,
        "deposit_cents": deposit_cents,
    }


# =====================
# Routes principales
# =====================

@app.route("/")
def index():
    clients = Client.query.order_by(Client.name).all()

    # Calcul du total des consignes théoriques
    totals = {"deposit_cents": 0}
    client_summaries = {}
    for c in clients:
        summary = client_keg_summary(c.id)
        client_summaries[c.id] = summary
        totals["deposit_cents"] += summary["deposit_cents"]

    return render_template("index.html",
                           clients=clients,
                           summaries=client_summaries,
                           totals=totals,
                           now=datetime.now())


@app.route("/clients")
def clients():
    clients = Client.query.order_by(Client.name).all()
    return render_template("clients.html", clients=clients)


@app.route("/clients/<int:client_id>")
def client_detail(client_id):
    client = Client.query.get_or_404(client_id)
    moves = KegMove.query.filter_by(client_id=client.id).order_by(KegMove.created_at.desc()).all()
    summary = client_keg_summary(client.id)
    return render_template("client_detail.html",
                           client=client,
                           moves=moves,
                           summary=summary)


@app.route("/catalog")
def catalog():
    products = Product.query.filter_by(is_active=True).order_by(Product.name).all()
    return render_template("catalog.html", products=products)


# =====================
# Création / initialisation de la base
# =====================

def ensure_schema():
    """Crée les tables si elles n’existent pas encore"""
    with app.app_context():
        db.create_all()


ensure_schema()


# =====================
# Filtre Jinja pour afficher en euros
# =====================

@app.template_filter("eur")
def eur(cents: int) -> str:
    return f"{cents / 100:.2f} €"


# =====================
# Lancement
# =====================

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")
