# app.py
from __future__ import annotations
import os
from datetime import datetime
from decimal import Decimal

from flask import Flask, render_template, render_template_string, request, redirect, url_for, abort
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text

# ------------------------------------------------------------------------------
# Config Flask + DB
# ------------------------------------------------------------------------------
def _make_db_uri() -> str:
    uri = os.getenv("DATABASE_URL", "").strip()
    if not uri:
        return "sqlite:///local.db"
    if uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql+psycopg://", 1)
    elif uri.startswith("postgresql://"):
        uri = uri.replace("postgresql://", "postgresql+psycopg://", 1)
    return uri

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = _make_db_uri()
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)  # une seule instance liée à app

# ------------------------------------------------------------------------------
# Modèles
# ------------------------------------------------------------------------------
class Client(db.Model):
    __tablename__ = "clients"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False, unique=True)
    note = db.Column(db.Text, nullable=True, default="")

class KegMove(db.Model):
    __tablename__ = "keg_moves"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    qty_out = db.Column(db.Integer, nullable=False, default=0)     # fûts livrés
    qty_in = db.Column(db.Integer, nullable=False, default=0)      # fûts repris
    qty_defect = db.Column(db.Integer, nullable=False, default=0)  # fûts défectueux repris
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

class EquipMove(db.Model):
    __tablename__ = "equip_moves"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    equip_name = db.Column(db.String(128), nullable=False)
    qty_out = db.Column(db.Integer, nullable=False, default=0)     # prêt
    qty_in = db.Column(db.Integer, nullable=False, default=0)      # retour
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

# ------------------------------------------------------------------------------
# Auto-migration minimale (idempotente)
# ------------------------------------------------------------------------------
def ensure_schema():
    with app.app_context():
        # crée les tables si elles n'existent pas
        db.create_all()

        # si ce n'est pas Postgres (ex: SQLite local), on s'arrête là
        try:
            dialect_name = db.engine.dialect.name  # <== toujours défini ici
        except Exception:
            dialect_name = ""

        if dialect_name != "postgresql":
            return

        ddl = [
            "ALTER TABLE clients ADD COLUMN IF NOT EXISTS note TEXT",
            "ALTER TABLE keg_moves ADD COLUMN IF NOT EXISTS qty_out INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE keg_moves ADD COLUMN IF NOT EXISTS qty_in INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE keg_moves ADD COLUMN IF NOT EXISTS qty_defect INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE keg_moves ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE equip_moves ADD COLUMN IF NOT EXISTS equip_name VARCHAR(128)",
            "ALTER TABLE equip_moves ADD COLUMN IF NOT EXISTS qty_out INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE equip_moves ADD COLUMN IF NOT EXISTS qty_in INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE equip_moves ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        ]
        # exécution via une transaction explicite
        with db.engine.begin() as conn:
            for stmt in ddl:
                conn.execute(text(stmt))

# Appel à l’import (ok pour Gunicorn)
ensure_schema()

# ------------------------------------------------------------------------------
# Filtre Jinja €
# ------------------------------------------------------------------------------
@app.template_filter("eur")
def eur(value):
    try:
        v = Decimal(value)
    except Exception:
        v = Decimal(0)
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")
    return f"{s} €"

# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------
CONSIGNE_PAR_FUT = Decimal("30")

def client_keg_summary(client_id: int) -> dict:
    row = db.session.execute(
        text(
            """
            SELECT
              COALESCE(SUM(qty_out), 0)     AS out_sum,
              COALESCE(SUM(qty_in), 0)      AS in_sum,
              COALESCE(SUM(qty_defect), 0)  AS defect_sum
            FROM keg_moves
            WHERE client_id = :cid
            """
        ),
        {"cid": client_id},
    ).one()
    m = row._mapping
    qty_out = int(m["out_sum"] or 0)
    qty_in = int(m["in_sum"] or 0)
    qty_def = int(m["defect_sum"] or 0)
    in_play = qty_out - qty_in - qty_def
    consignes = CONSIGNE_PAR_FUT * Decimal(max(in_play, 0))
    return {"out": qty_out, "in": qty_in, "defect": qty_def, "in_play": in_play, "consignes": consignes}

def client_equip_summary(client_id: int) -> dict[str, int]:
    rows = db.session.execute(
        text(
            """
            SELECT equip_name,
                   COALESCE(SUM(qty_out),0) - COALESCE(SUM(qty_in),0) AS on_loan
            FROM equip_moves
            WHERE client_id = :cid
            GROUP BY equip_name
            HAVING COALESCE(SUM(qty_out),0) - COALESCE(SUM(qty_in),0) <> 0
            ORDER BY equip_name
            """
        ),
        {"cid": client_id},
    ).all()
    return {r._mapping["equip_name"]: int(r._mapping["on_loan"] or 0) for r in rows}

# ------------------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------------------
@app.route("/")
def index():
    clients = Client.query.order_by(Client.name.asc()).all()
    cards = []
    total_kegs = 0
    total_consignes = Decimal("0")
    for c in clients:
        k = client_keg_summary(c.id)
        e = client_equip_summary(c.id)
        total_kegs += max(k["in_play"], 0)
        total_consignes += k["consignes"]
        cards.append(
            {
                "id": c.id,
                "name": c.name,
                "note": c.note or "",
                "kegs_in_play": k["in_play"],
                "consignes": k["consignes"],
                "equip": e,
            }
        )
    try:
        return render_template("index.html", cards=cards, total_kegs=total_kegs, total_consignes=total_consignes)
    except Exception:
        # fallback inline (si les templates ne sont pas déployés)
        return render_template_string(
            """
            <!doctype html><html><head><meta charset="utf-8"><title>Suivi fûts</title>
            <style>body{font-family:system-ui;margin:2rem} .card{border:1px solid #ddd;padding:1rem;margin-bottom:1rem;border-radius:.5rem}
            .head{display:flex;justify-content:space-between;align-items:center}
            .chip{background:#eef;padding:.2rem .5rem;border-radius:.4rem}
            </style></head><body>
            <h1>Suivi fûts</h1>
            <p>Total fûts en circulation: <b>{{ total_kegs }}</b> — Consignes en jeu: <b>{{ total_consignes|eur }}</b></p>
            <p><a href="{{ url_for('new_client') }}">➕ Nouveau client</a></p>
            {% for c in cards %}
            <div class="card">
              <div class="head">
                <h3><a href="{{ url_for('client_detail', client_id=c.id) }}">{{ c.name }}</a></h3>
                <span class="chip">{{ c.kegs_in_play }} fûts — {{ c.consignes|eur }}</span>
              </div>
              {% if c.equip %}
                <div>Matériel: {% for k,v in c.equip.items() %}<span class="chip">{{k}}: {{v}}</span> {% endfor %}</div>
              {% endif %}
              {% if c.note %}<div style="color:#666"><em>{{ c.note }}</em></div>{% endif %}
            </div>
            {% endfor %}
            </body></html>
            """,
            cards=cards,
            total_kegs=total_kegs,
            total_consignes=total_consignes,
        )

@app.route("/clients/new", methods=["GET", "POST"])
def new_client():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        note = (request.form.get("note") or "").strip()
        if not name:
            abort(400, "Nom requis")
        existing = Client.query.filter_by(name=name).first()
        if existing:
            existing.note = note
            db.session.commit()
            return redirect(url_for("client_detail", client_id=existing.id))
        c = Client(name=name, note=note)
        db.session.add(c)
        db.session.commit()
        return redirect(url_for("client_detail", client_id=c.id))
    try:
        return render_template("client_new.html")
    except Exception:
        return render_template_string(
            """
            <h1>Nouveau client</h1>
            <form method="post">
              <label>Nom<br><input name="name" required></label><br><br>
              <label>Note<br><textarea name="note" rows="3"></textarea></label><br><br>
              <button type="submit">Enregistrer</button>
            </form>
            """
        )

@app.route("/client/<int:client_id>")
def client_detail(client_id: int):
    c = db.session.get(Client, client_id) or abort(404)
    k = client_keg_summary(client_id)
    e = client_equip_summary(client_id)
    keg_rows = KegMove.query.filter_by(client_id=client_id).order_by(KegMove.created_at.desc()).limit(100).all()
    equip_rows = EquipMove.query.filter_by(client_id=client_id).order_by(EquipMove.created_at.desc()).limit(100).all()
    try:
        return render_template(
            "client_detail.html",
            c=c, k=k, e=e,
            keg_rows=keg_rows, equip_rows=equip_rows,
            CONSIGNE=float(CONSIGNE_PAR_FUT),
        )
    except Exception:
        return render_template_string(
            """
            <h1>{{ c.name }}</h1>
            <p>Fûts en circulation: <b>{{ k.in_play }}</b> — Consignes: <b>{{ k.consignes|eur }}</b></p>
            <h3>Ajouter mouvement fûts</h3>
            <form method="post" action="{{ url_for('add_keg_move', client_id=c.id) }}">
              <label>Livrés (out) <input type="number" name="qty_out" value="0"></label>
              <label>Repris (in) <input type="number" name="qty_in" value="0"></label>
              <label>Défectueux <input type="number" name="qty_defect" value="0"></label>
              <button type="submit">Ajouter</button>
            </form>
            <h3>Ajouter mouvement matériel</h3>
            <form method="post" action="{{ url_for('add_equip_move', client_id=c.id) }}">
              <label>Matériel <input name="equip_name" placeholder="Tireuse / CO2 / Barnum"></label>
              <label>Prêt (out) <input type="number" name="qty_out" value="0"></label>
              <label>Retour (in) <input type="number" name="qty_in" value="0"></label>
              <button type="submit">Ajouter</button>
            </form>
            <h3>Historique fûts</h3>
            <ul>
              {% for r in keg_rows %}
                <li>{{ r.created_at.strftime("%d/%m/%Y") }} — out: {{ r.qty_out }}, in: {{ r.qty_in }}, defect: {{ r.qty_defect }}</li>
              {% endfor %}
            </ul>
            <h3>Historique matériel</h3>
            <ul>
              {% for r in equip_rows %}
                <li>{{ r.created_at.strftime("%d/%m/%Y") }} — {{ r.equip_name }}: +{{ r.qty_out }} / -{{ r.qty_in }}</li>
              {% endfor %}
            </ul>
            """,
            c=c, k=k, e=e,
            keg_rows=keg_rows, equip_rows=equip_rows,
            CONSIGNE=float(CONSIGNE_PAR_FUT),
        )

@app.route("/client/<int:client_id>/keg/new", methods=["POST"])
def add_keg_move(client_id: int):
    c = db.session.get(Client, client_id) or abort(404)
    def _to_int(name):
        try: return int(request.form.get(name, "0") or 0)
        except: return 0
    m = KegMove(
        client_id=c.id,
        qty_out=_to_int("qty_out"),
        qty_in=_to_int("qty_in"),
        qty_defect=_to_int("qty_defect"),
        created_at=datetime.utcnow(),
    )
    db.session.add(m)
    db.session.commit()
    return redirect(url_for("client_detail", client_id=c.id))

@app.route("/client/<int:client_id>/equip/new", methods=["POST"])
def add_equip_move(client_id: int):
    c = db.session.get(Client, client_id) or abort(404)
    name = (request.form.get("equip_name") or "").strip() or "Matériel"
    def _to_int(n):
        try: return int(request.form.get(n, "0") or 0)
        except: return 0
    m = EquipMove(
        client_id=c.id,
        equip_name=name,
        qty_out=_to_int("qty_out"),
        qty_in=_to_int("qty_in"),
        created_at=datetime.utcnow(),
    )
    db.session.add(m)
    db.session.commit()
    return redirect(url_for("client_detail", client_id=c.id))

# ------------------------------------------------------------------------------
# Entrée
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
