import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text

# ----------- Config DB -----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def normalize_db_url(url: str | None) -> str:
    if not url or url.strip() == "":
        return "sqlite:///" + os.path.join(BASE_DIR, "suivi_futs.db")
    url = url.strip()
    if url.startswith("postgresql+psycopg://"):
        return url
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url

DATABASE_URL = normalize_db_url(os.getenv("DATABASE_URL"))

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev")
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# ----------- Modèles -----------
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

# ----------- Helpers template -----------
@app.context_processor
def inject_now():
    return {"now": datetime.utcnow()}

def add_missing_contact_column_if_needed():
    with db.engine.begin() as conn:
        engine_name = db.engine.url.get_backend_name()
        if engine_name == "postgresql":
            res = conn.execute(text("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'client' AND column_name = 'contact' LIMIT 1
            """)).first()
            if not res:
                conn.execute(text("ALTER TABLE client ADD COLUMN contact VARCHAR(120)"))
        else:
            cols = conn.execute(text("PRAGMA table_info(client)")).fetchall()
            names = {row[1] for row in cols}
            if "contact" not in names:
                conn.execute(text("ALTER TABLE client ADD COLUMN contact VARCHAR(120)"))

def ensure_db():
    db.create_all()
    add_missing_contact_column_if_needed()
    # Seed si vide
    try:
        if Client.query.count() == 0:
            c = Client(name="Client Démo", contact="demo@example.com")
            db.session.add(c); db.session.flush()
            db.session.add_all([
                Movement(client_id=c.id, product="Fût COREFF Blonde 20L", quantity=5, type="livraison"),
                Movement(client_id=c.id, product="Fût COREFF Blonde 20L", quantity=2, type="reprise"),
            ])
            db.session.commit()
    except Exception:
        db.session.rollback()
        raise

with app.app_context():
    ensure_db()

# ----------- Utilitaires métier -----------
def stock_client(client: Client) -> int:
    """Calcul du stock net par addition des livraisons et soustraction des reprises."""
    total = 0
    for m in client.movements:
        total += m.quantity if m.type == "livraison" else -m.quantity
    return total

# ----------- Routes -----------
@app.route("/")
def index():
    clients = Client.query.order_by(Client.name).all()
    # Pré-calcul des stocks pour affichage rapide
    stocks = {c.id: stock_client(c) for c in clients}
    return render_template("index.html", clients=clients, stocks=stocks)

@app.route("/clients/new", methods=["GET", "POST"])
def new_client():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        contact = (request.form.get("contact") or "").strip()
        if not name:
            flash("Le nom du client est obligatoire.", "danger")
            return redirect(url_for("new_client"))
        client = Client(name=name, contact=contact or None)
        db.session.add(client)
        db.session.commit()
        flash("Client ajouté avec succès.", "success")
        return redirect(url_for("index"))
    return render_template("new_client.html")

@app.route("/clients/<int:client_id>")
def client_detail(client_id):
    client = Client.query.get_or_404(client_id)
    client_stock = stock_client(client)
    movements = Movement.query.filter_by(client_id=client.id).order_by(Movement.date.desc(), Movement.id.desc()).all()
    return render_template("client_detail.html", client=client, movements=movements, client_stock=client_stock)

@app.route("/clients/<int:client_id>/add_movement", methods=["POST"])
def add_movement(client_id):
    client = Client.query.get_or_404(client_id)
    product = (request.form.get("product") or "").strip()
    mov_type = (request.form.get("type") or "").strip()  # "livraison"|"reprise"
    try:
        quantity = int(request.form.get("quantity") or 0)
    except ValueError:
        quantity = 0

    if not product or mov_type not in ("livraison", "reprise") or quantity <= 0:
        flash("Vérifie le produit, la quantité (>0) et le type (livraison/reprise).", "danger")
        return redirect(url_for("client_detail", client_id=client.id))

    db.session.add(Movement(client_id=client.id, product=product, quantity=quantity, type=mov_type))
    db.session.commit()
    flash("Mouvement enregistré avec succès.", "success")
    return redirect(url_for("client_detail", client_id=client.id))

@app.route("/clients/<int:client_id>/delete", methods=["POST"])
def delete_client(client_id):
    client = Client.query.get_or_404(client_id)
    name = client.name
    db.session.delete(client)  # supprime aussi ses mouvements (cascade)
    db.session.commit()
    flash(f"Client « {name} » supprimé.", "warning")
    return redirect(url_for("index"))

@app.route("/movements/<int:movement_id>/delete", methods=["POST"])
def delete_movement(movement_id):
    movement = Movement.query.get_or_404(movement_id)
    cid = movement.client_id
    db.session.delete(movement)
    db.session.commit()
    flash("Mouvement supprimé.", "warning")
    return redirect(url_for("client_detail", client_id=cid))

# ----------- Local run -----------
if __name__ == "__main__":
    with app.app_context():
        ensure_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
