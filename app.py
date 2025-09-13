import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text, func, select

# -------------------- Config DB --------------------
def normalize_db_url(url: str | None) -> str:
    """Accepte DATABASE_URL (Render/Heroku) et assure le bon driver psycopg v3."""
    if not url or url.strip() == "":
        # Fallback local SQLite si pas de DATABASE_URL
        base_dir = os.path.dirname(os.path.abspath(__file__))
        return "sqlite:///" + os.path.join(base_dir, "suivi_futs.db")
    url = url.strip()
    # Render/Heroku envoient parfois "postgres://"
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    # Ou "postgresql://"
    if url.startswith("postgresql://") and not url.startswith("postgresql+psycopg://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url

DATABASE_URL = normalize_db_url(os.getenv("DATABASE_URL"))

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.secret_key = os.getenv("SECRET_KEY", "dev")
db = SQLAlchemy(app)

# -------------------- Modèles --------------------
class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    # IMPORTANT : colonne optionnelle. On gère sa création si absente.
    contact = db.Column(db.String(120), nullable=True)

    movements = db.relationship(
        "Movement",
        backref="client",
        lazy=True,
        cascade="all, delete-orphan"
    )

class Movement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    product = db.Column(db.String(120), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    type = db.Column(db.String(20), nullable=False)  # "livraison" | "reprise"
    date = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

# -------------------- Utilitaires --------------------
@app.context_processor
def inject_now():
    # Evite l'erreur 'now' is undefined dans les templates
    return {"now": datetime.utcnow()}

def add_missing_contact_column_if_needed() -> None:
    """Ajoute la colonne 'contact' si elle n'existe pas (Postgres / SQLite)."""
    with db.engine.begin() as conn:
        backend = db.engine.url.get_backend_name()
        if backend == "postgresql":
            # Vérifie via information_schema
            exists = conn.execute(text("""
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'client' AND column_name = 'contact'
                LIMIT 1
            """)).first()
            if not exists:
                conn.execute(text("ALTER TABLE client ADD COLUMN contact VARCHAR(120)"))
        else:
            # SQLite
            cols = conn.execute(text("PRAGMA table_info(client)")).fetchall()
            names = {row[1] for row in cols}  # (cid, name, type, notnull, dflt_value, pk)
            if "contact" not in names:
                conn.execute(text("ALTER TABLE client ADD COLUMN contact VARCHAR(120)"))

def safe_client_count() -> int:
    """Évite un SELECT incluant des colonnes manquantes (e.g. contact)."""
    return db.session.execute(select(func.count()).select_from(Client)).scalar() or 0

def ensure_db():
    # Crée les tables si absentes
    db.create_all()
    # Ajoute la colonne contact si manquante (empêche l'erreur « column client.contact does not exist »)
    add_missing_contact_column_if_needed()

    # Seed minimal si base vide (via COUNT(*) uniquement)
    if safe_client_count() == 0:
        demo = Client(name="Client Démo", contact="demo@example.com")
        db.session.add(demo)
        db.session.flush()
        db.session.add_all([
            Movement(client_id=demo.id, product="Fût COREFF Blonde 20L", quantity=5, type="livraison"),
            Movement(client_id=demo.id, product="Fût COREFF Blonde 20L", quantity=2, type="reprise"),
        ])
        db.session.commit()

with app.app_context():
    ensure_db()

def stock_client(client: Client) -> int:
    """Stock net = livraisons - reprises."""
    total = 0
    for m in client.movements:
        total += m.quantity if m.type == "livraison" else -m.quantity
    return total

# -------------------- Routes existantes & ajouts --------------------
@app.route("/")
def index():
    # Affichage simple des clients (le template existant doit déjà marcher)
    clients = Client.query.order_by(Client.name).all()
    # Pré-calc des stocks si tu l'utilises dans l'index
    stocks = {c.id: stock_client(c) for c in clients}
    return render_template("index.html", clients=clients, stocks=stocks)

@app.route("/clients/<int:client_id>")
def client_detail(client_id: int):
    client = Client.query.get_or_404(client_id)
    movements = (
        Movement.query
        .filter_by(client_id=client.id)
        .order_by(Movement.date.desc(), Movement.id.desc())
        .all()
    )
    return render_template(
        "client_detail.html",
        client=client,
        movements=movements,
        client_stock=stock_client(client)
    )

# ------ AJOUT : création d'un client (si tu as déjà un formulaire ailleurs, garde le tien) ------
@app.route("/clients/new", methods=["GET", "POST"])
def new_client():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        contact = (request.form.get("contact") or "").strip()
        if not name:
            flash("Le nom du client est obligatoire.", "danger")
            return redirect(url_for("new_client"))
        db.session.add(Client(name=name, contact=contact or None))
        db.session.commit()
        flash("Client ajouté.", "success")
        return redirect(url_for("index"))
    # Template très simple ou remplace par le tien
    return render_template("new_client.html")

# ------ AJOUT : enregistrement d'un mouvement (livraison / reprise) ------
@app.route("/clients/<int:client_id>/add_movement", methods=["POST"])
def add_movement(client_id: int):
    client = Client.query.get_or_404(client_id)
    product = (request.form.get("product") or "").strip()
    mov_type = (request.form.get("type") or "").strip().lower()
    try:
        quantity = int(request.form.get("quantity") or 0)
    except ValueError:
        quantity = 0

    if not product or mov_type not in ("livraison", "reprise") or quantity <= 0:
        flash("Vérifie le produit, la quantité (>0) et le type (livraison/reprise).", "danger")
        return redirect(url_for("client_detail", client_id=client.id))

    db.session.add(Movement(
        client_id=client.id,
        product=product,
        quantity=quantity,
        type=mov_type
    ))
    db.session.commit()
    flash("Mouvement enregistré.", "success")
    return redirect(url_for("client_detail", client_id=client.id))

# ------ AJOUT : suppression d'un client ------
@app.route("/clients/<int:client_id>/delete", methods=["POST"])
def delete_client(client_id: int):
    client = Client.query.get_or_404(client_id)
    name = client.name
    db.session.delete(client)  # cascade = supprime ses mouvements
    db.session.commit()
    flash(f"Client « {name} » supprimé.", "warning")
    return redirect(url_for("index"))

# ------ AJOUT : suppression d’un mouvement ------
@app.route("/movements/<int:movement_id>/delete", methods=["POST"])
def delete_movement(movement_id: int):
    mvt = Movement.query.get_or_404(movement_id)
    cid = mvt.client_id
    db.session.delete(mvt)
    db.session.commit()
    flash("Mouvement supprimé.", "warning")
    return redirect(url_for("client_detail", client_id=cid))

# -------------------- Run local --------------------
if __name__ == "__main__":
    with app.app_context():
        ensure_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
