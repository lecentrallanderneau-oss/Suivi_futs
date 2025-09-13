import os
from datetime import datetime
from decimal import Decimal
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text

# ----------------------------------------------------------------------------
# App & DB
# ----------------------------------------------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev")
db_url = os.environ.get("DATABASE_URL") or "sqlite:///local.db"
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# ----------------------------------------------------------------------------
# MODELS (minimal for starter; will be ignored if real models.py is present)
# ----------------------------------------------------------------------------
# We try to import your project models if they exist.
try:
    from models import db as ext_db  # type: ignore
    db = ext_db  # type: ignore[assignment]
    from models import Client, KegMove, EquipMove  # type: ignore
except Exception:
    class Client(db.Model):  # type: ignore
        __tablename__ = "clients"
        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(120), nullable=False, unique=True)
        note = db.Column(db.Text, nullable=True)
        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    class KegMove(db.Model):  # type: ignore
        __tablename__ = "keg_moves"
        id = db.Column(db.Integer, primary_key=True)
        client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
        qty_out = db.Column(db.Integer, default=0, nullable=False)     # fûts livrés
        qty_in = db.Column(db.Integer, default=0, nullable=False)      # fûts repris
        qty_defect = db.Column(db.Integer, default=0, nullable=False)  # fûts défectueux
        note = db.Column(db.Text)

    class EquipMove(db.Model):  # type: ignore
        __tablename__ = "equip_moves"
        id = db.Column(db.Integer, primary_key=True)
        client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
        equip_name = db.Column(db.String(120), nullable=False)
        qty_out = db.Column(db.Integer, default=0, nullable=False)
        qty_in = db.Column(db.Integer, default=0, nullable=False)
        note = db.Column(db.Text)

# ----------------------------------------------------------------------------
# Jinja filters
# ----------------------------------------------------------------------------
@app.template_filter("eur")
def eur(v):
    try:
        q = Decimal(v or 0)
    except Exception:
        q = Decimal(0)
    return f"{q:,.2f} €".replace(",", " ").replace(".", ",")

# ----------------------------------------------------------------------------
# Auto-migration: ensure missing tables/columns exist
# ----------------------------------------------------------------------------
def ensure_schema():
    with app.app_context():
        inspector = inspect(db.engine)
        # clients table
        if not inspector.has_table("clients"):
            db.session.execute(text("""
                CREATE TABLE clients (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(120) NOT NULL UNIQUE,
                    note TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
            """))
        else:
            cols = {c["name"] for c in inspector.get_columns("clients")}
            if "note" not in cols:
                db.session.execute(text("ALTER TABLE clients ADD COLUMN note TEXT;"))

        # keg_moves table
        if not inspector.has_table("keg_moves"):
            db.session.execute(text("""
                CREATE TABLE keg_moves (
                    id SERIAL PRIMARY KEY,
                    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    qty_out INTEGER NOT NULL DEFAULT 0,
                    qty_in INTEGER NOT NULL DEFAULT 0,
                    qty_defect INTEGER NOT NULL DEFAULT 0,
                    note TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_keg_moves_client ON keg_moves(client_id);
            """))
        else:
            cols = {c["name"] for c in inspector.get_columns("keg_moves")}
            for needed in ("qty_out", "qty_in", "qty_defect"):
                if needed not in cols:
                    db.session.execute(text(f"ALTER TABLE keg_moves ADD COLUMN {needed} INTEGER NOT NULL DEFAULT 0;"))
            if "created_at" not in cols:
                db.session.execute(text("ALTER TABLE keg_moves ADD COLUMN created_at TIMESTAMP NOT NULL DEFAULT NOW();"))

        # equip_moves table
        if not inspector.has_table("equip_moves"):
            db.session.execute(text("""
                CREATE TABLE equip_moves (
                    id SERIAL PRIMARY KEY,
                    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    equip_name VARCHAR(120) NOT NULL,
                    qty_out INTEGER NOT NULL DEFAULT 0,
                    qty_in INTEGER NOT NULL DEFAULT 0,
                    note TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_equip_moves_client ON equip_moves(client_id);
            """))
        else:
            cols = {c["name"] for c in inspector.get_columns("equip_moves")}
            for needed in ("equip_name", "qty_out", "qty_in"):
                if needed not in cols:
                    if needed == "equip_name":
                        db.session.execute(text("ALTER TABLE equip_moves ADD COLUMN equip_name VARCHAR(120) NOT NULL DEFAULT 'Matériel';"))
                    else:
                        db.session.execute(text(f"ALTER TABLE equip_moves ADD COLUMN {needed} INTEGER NOT NULL DEFAULT 0;"))
            if "created_at" not in cols:
                db.session.execute(text("ALTER TABLE equip_moves ADD COLUMN created_at TIMESTAMP NOT NULL DEFAULT NOW();"))
        db.session.commit()

# Run once on startup
ensure_schema()

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
CONS_PER_KEG = Decimal("30")

def client_keg_summary(client_id: int):
    r = (
        db.session.query(
            db.func.coalesce(db.func.sum(KegMove.qty_out), 0),
            db.func.coalesce(db.func.sum(KegMove.qty_in), 0),
            db.func.coalesce(db.func.sum(KegMove.qty_defect), 0),
        )
        .filter(KegMove.client_id == client_id)
        .one()
    )
    out_q, in_q, defect_q = map(lambda x: int(x or 0), r)
    in_play = out_q - in_q - defect_q
    consigne = Decimal(in_play) * CONS_PER_KEG
    return {"out": out_q, "in": in_q, "defect": defect_q, "in_play": in_play, "consigne": consigne}

def client_equip_summary(client_id: int):
    r = (
        db.session.query(
            db.func.coalesce(db.func.sum(EquipMove.qty_out), 0),
            db.func.coalesce(db.func.sum(EquipMove.qty_in), 0),
        )
        .filter(EquipMove.client_id == client_id)
        .one()
    )
    out_q, in_q = map(lambda x: int(x or 0), r)
    return {"out": out_q, "in": in_q, "in_play": out_q - in_q}

# ----------------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------------
@app.route("/")
def index():
    clients = Client.query.order_by(Client.name.asc()).all()
    rows = []
    tot_keg = tot_cons = 0
    for c in clients:
        keg = client_keg_summary(c.id)
        eq = client_equip_summary(c.id)
        tot_keg += keg["in_play"]
        tot_cons += keg["consigne"]
        rows.append({
            "id": c.id,
            "name": c.name,
            "note": getattr(c, "note", None),
            "kegs": keg["in_play"],
            "equip": eq["in_play"],
            "consigne": keg["consigne"],
        })
    return render_template("index.html", clients=rows, total_kegs=tot_keg, total_cons=tot_cons)

@app.route("/clients/new", methods=["GET", "POST"])
def clients_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        note = request.form.get("note") or None
        if not name:
            flash("Nom obligatoire.", "danger")
        else:
            c = Client(name=name, note=note)
            db.session.add(c)
            db.session.commit()
            flash("Client créé.", "success")
            return redirect(url_for("index"))
    return render_template("client_new.html")

@app.route("/client/<int:client_id>")
def client_detail(client_id):
    c = Client.query.get_or_404(client_id)
    keg_hist = (
        KegMove.query.filter_by(client_id=client_id)
        .order_by(KegMove.created_at.desc())
        .limit(50)
        .all()
    )
    equip_hist = (
        EquipMove.query.filter_by(client_id=client_id)
        .order_by(EquipMove.created_at.desc())
        .limit(50)
        .all()
    )
    keg = client_keg_summary(client_id)
    eq = client_equip_summary(client_id)
    return render_template("client_detail.html", c=c, keg=keg, equip=eq, keg_hist=keg_hist, equip_hist=equip_hist)

@app.route("/client/<int:client_id>/keg/new", methods=["POST"])
def keg_new(client_id):
    qty_out = int(request.form.get("qty_out") or 0)
    qty_in = int(request.form.get("qty_in") or 0)
    qty_defect = int(request.form.get("qty_defect") or 0)
    note = request.form.get("note")
    m = KegMove(client_id=client_id, qty_out=qty_out, qty_in=qty_in, qty_defect=qty_defect, note=note)
    db.session.add(m)
    db.session.commit()
    flash("Mouvement fûts enregistré.", "success")
    return redirect(url_for("client_detail", client_id=client_id))

@app.route("/client/<int:client_id>/equip/new", methods=["POST"])
def equip_new(client_id):
    equip_name = (request.form.get("equip_name") or "Matériel").strip() or "Matériel"
    qty_out = int(request.form.get("qty_out") or 0)
    qty_in = int(request.form.get("qty_in") or 0)
    note = request.form.get("note")
    m = EquipMove(client_id=client_id, equip_name=equip_name, qty_out=qty_out, qty_in=qty_in, note=note)
    db.session.add(m)
    db.session.commit()
    flash("Mouvement matériel enregistré.", "success")
    return redirect(url_for("client_detail", client_id=client_id))

# ----------------------------------------------------------------------------
# Minimal templates (fallback if your repo hasn't them)
# ----------------------------------------------------------------------------
# These allow the app to render even if templates are missing in the repo.
from jinja2 import TemplateNotFound

def _render_or_inline(tpl_name, inline_html):
    try:
        return render_template(tpl_name)
    except TemplateNotFound:
        return inline_html

@app.route("/__inline__/index.html")
def __inline_index():
    return _render_or_inline("index.html", """
<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Suivi fûts</title></head>
<body>
  <h1>Clients</h1>
  <p>Total fûts en jeu: {{ total_kegs }} — Consignes: {{ total_cons|eur }}</p>
  <ul>
    {% for r in clients %}
      <li><a href="{{ url_for('client_detail', client_id=r.id) }}">{{ r.name }}</a> — fûts: {{ r.kegs }}, matériel: {{ r.equip }}, consignes: {{ r.consigne|eur }}</li>
    {% endfor %}
  </ul>
  <p><a href="{{ url_for('clients_new') }}">+ Nouveau client</a></p>
</body></html>
""")

# Serve real template name too so the route works with real files.
@app.route("/__inline__/client_new.html")
def __inline_client_new():
    return _render_or_inline("client_new.html", """
<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Nouveau client</title></head>
<body>
  <h1>Nouveau client</h1>
  <form method="post">
    <label>Nom <input name="name" required></label><br>
    <label>Note <input name="note"></label><br>
    <button type="submit">Créer</button>
  </form>
  <p><a href="{{ url_for('index') }}">Retour</a></p>
</body></html>
""")

@app.route("/__inline__/client_detail.html")
def __inline_client_detail():
    return _render_or_inline("client_detail.html", """
<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Client</title></head>
<body>
  <h1>{{ c.name }}</h1>
  <p>Fûts en jeu: {{ keg.in_play }} — Consignes: {{ keg.consigne|eur }}</p>
  <h2>Saisir mouvement fûts</h2>
  <form method="post" action="{{ url_for('keg_new', client_id=c.id) }}">
    Sortis <input type="number" name="qty_out" min="0" value="0">
    Rentrés <input type="number" name="qty_in" min="0" value="0">
    Défectueux <input type="number" name="qty_defect" min="0" value="0">
    <input name="note" placeholder="Note">
    <button type="submit">Enregistrer</button>
  </form>

  <h2>Saisir mouvement matériel</h2>
  <form method="post" action="{{ url_for('equip_new', client_id=c.id) }}">
    Matériel <input name="equip_name" placeholder="Tireuse, CO2, etc.">
    Sortis <input type="number" name="qty_out" min="0" value="0">
    Rentrés <input type="number" name="qty_in" min="0" value="0">
    <input name="note" placeholder="Note">
    <button type="submit">Enregistrer</button>
  </form>

  <h2>Historique fûts</h2>
  <ul>{% for m in keg_hist %}
      <li>{{ m.created_at.date() }} — +{{ m.qty_out }} / -{{ m.qty_in }} / défectueux {{ m.qty_defect }} — {{ m.note or '' }}</li>
  {% endfor %}</ul>

  <h2>Historique matériel</h2>
  <ul>{% for m in equip_hist %}
      <li>{{ m.created_at.date() }} — {{ m.equip_name }} : +{{ m.qty_out }} / -{{ m.qty_in }} — {{ m.note or '' }}</li>
  {% endfor %}</ul>

  <p><a href="{{ url_for('index') }}">Retour</a></p>
</body></html>
""")

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
