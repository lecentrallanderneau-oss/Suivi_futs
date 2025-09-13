import os
from datetime import date
from decimal import Decimal

from dotenv import load_dotenv
from flask import (
    Flask, render_template_string, request, redirect, url_for, flash
)

# ⚠️ On importe la DB et les modèles depuis models.py
from models import db, Client, KegMove, EquipMove
from sqlalchemy import func

# -----------------------------------------------------------------------------
# App & configuration
# -----------------------------------------------------------------------------
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///local.db")
# Compat Render/Heroku
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# ⚙️ on « branche » la DB du models.py sur cette app
db.init_app(app)

# Création auto des tables si absentes
with app.app_context():
    db.create_all()

# -----------------------------------------------------------------------------
# Filtres / helpers
# -----------------------------------------------------------------------------
@app.template_filter("eur")
def eur_filter(value):
    """Affiche des centimes en euros (12345 -> 123,45 €)."""
    try:
        cents = int(Decimal(str(value)))
        return f"{Decimal(cents) / Decimal(100):.2f} €".replace(".", ",")
    except Exception:
        return f"{value} €"

def consigne_cents_for(qty_out: int, qty_in: int) -> int:
    """30 € par fût livré, -30 € par fût repris."""
    return (qty_out - qty_in) * 3000


def client_keg_summary(client_id: int) -> dict:
    """Synthèse fûts pour un client."""
    out_total, in_total, defect_total = (
        db.session.query(
            func.coalesce(func.sum(KegMove.qty_out), 0),
            func.coalesce(func.sum(KegMove.qty_in), 0),
            func.coalesce(func.sum(KegMove.qty_defect), 0),
        )
        .filter(KegMove.client_id == client_id)
        .one()
    )

    present = (out_total or 0) - (in_total or 0) - (defect_total or 0)
    consigne = consigne_cents_for(out_total or 0, in_total or 0)
    return {"present_kegs": present, "consigne_cents": consigne}


def client_equip_summary(client_id: int) -> list[tuple[str, int]]:
    """Liste (label, quantité en prêt > 0)."""
    rows = (
        db.session.query(
            EquipMove.label,
            (
                func.coalesce(func.sum(EquipMove.qty_out), 0)
                - func.coalesce(func.sum(EquipMove.qty_in), 0)
            ).label("bal")
        )
        .filter(EquipMove.client_id == client_id)
        .group_by(EquipMove.label)
        .having(
            (
                func.coalesce(func.sum(EquipMove.qty_out), 0)
                - func.coalesce(func.sum(EquipMove.qty_in), 0)
            )
            > 0
        )
        .order_by(EquipMove.label.asc())
        .all()
    )
    return [(r[0], int(r[1])) for r in rows]

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.route("/")
def index():
    """Liste clients + résumé global (starter minimal garanti)."""
    clients = Client.query.order_by(Client.name.asc()).all()

    cards: list[dict] = []
    total_kegs = 0
    total_consigne = 0
    total_equip = 0

    for c in clients:
        keg = client_keg_summary(c.id)
        equip = client_equip_summary(c.id)

        total_kegs += max(keg["present_kegs"], 0)
        total_consigne += keg["consigne_cents"]
        total_equip += sum(q for _, q in equip)

        cards.append(
            {
                "id": c.id,
                "name": c.name,
                "kegs": keg["present_kegs"],
                "consigne_cents": keg["consigne_cents"],
                "equip": equip,
            }
        )

    totals = {
        "kegs": total_kegs,
        "consigne_cents": total_consigne,
        "equip": total_equip,
    }

    # Template inline (tu pourras passer à des fichiers templates plus tard)
    tpl = """
    <!doctype html>
    <html lang="fr">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <title>Suivi fûts - Tableau de bord</title>
      <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
    </head>
    <body class="bg-light">
    <div class="container py-4">
      <h1 class="mb-4">Suivi fûts — Tableau de bord</h1>

      <div class="row g-3 mb-4">
        <div class="col-md-4">
          <div class="card border-0 shadow-sm"><div class="card-body">
            <div class="text-muted">Fûts chez clients</div>
            <div class="fs-3">{{ totals.kegs or 0 }}</div>
          </div></div>
        </div>
        <div class="col-md-4">
          <div class="card border-0 shadow-sm"><div class="card-body">
            <div class="text-muted">Consigne en cours</div>
            <div class="fs-3">{{ (totals.consigne_cents or 0)|eur }}</div>
          </div></div>
        </div>
        <div class="col-md-4">
          <div class="card border-0 shadow-sm"><div class="card-body">
            <div class="text-muted">Matériel en prêt</div>
            <div class="fs-3">{{ totals.equip or 0 }}</div>
          </div></div>
        </div>
      </div>

      <div class="d-flex justify-content-between align-items-center mb-2">
        <h2 class="h4 mb-0">Clients</h2>
        <a class="btn btn-sm btn-primary" href="{{ url_for('client_new') }}">➕ Nouveau client</a>
      </div>

      {% if cards %}
      <div class="list-group">
        {% for c in cards %}
        <a href="{{ url_for('client_detail', client_id=c.id) }}" class="list-group-item list-group-item-action">
          <div class="d-flex w-100 justify-content-between">
            <h5 class="mb-1">{{ c.name }}</h5>
            <small class="text-muted">Consigne: {{ (c.consigne_cents or 0)|eur }}</small>
          </div>
          <p class="mb-1">Fûts: {{ c.kegs or 0 }} • Matériel:
            {% if c.equip %}
              {% for label, q in c.equip %}
                <span class="badge text-bg-secondary me-1">{{ label }} × {{ q }}</span>
              {% endfor %}
            {% else %}Aucun{% endif %}
          </p>
        </a>
        {% endfor %}
      </div>
      {% else %}
        <div class="alert alert-info">Aucun client pour l’instant. Commence par en créer un.</div>
      {% endif %}
    </div>
    </body>
    </html>
    """
    return render_template_string(tpl, cards=cards, totals=totals)


@app.route("/clients/new", methods=["GET", "POST"])
def client_new():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        note = (request.form.get("note") or "").strip() or None
        if not name:
            flash("Nom obligatoire.", "warning")
            return redirect(url_for("client_new"))
        if db.session.query(Client.id).filter(func.lower(Client.name) == name.lower()).first():
            flash("Ce client existe déjà.", "warning")
            return redirect(url_for("client_new"))
        c = Client(name=name, note=note)
        db.session.add(c)
        db.session.commit()
        flash("Client créé.", "success")
        return redirect(url_for("client_detail", client_id=c.id))

    tpl = """
    <!doctype html><html lang="fr"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Nouveau client</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
    </head><body class="bg-light"><div class="container py-4">
      <h1 class="mb-4">Nouveau client</h1>
      <form method="post" class="card p-3 shadow-sm">
        <div class="mb-3">
          <label class="form-label">Nom du client</label>
          <input class="form-control" name="name" required>
        </div>
        <div class="mb-3">
          <label class="form-label">Note (optionnel)</label>
          <textarea class="form-control" name="note" rows="3"></textarea>
        </div>
        <div class="d-flex gap-2">
          <button class="btn btn-primary" type="submit">Créer</button>
          <a class="btn btn-secondary" href="{{ url_for('index') }}">Annuler</a>
        </div>
      </form>
    </div></body></html>
    """
    return render_template_string(tpl)


@app.route("/client/<int:client_id>")
def client_detail(client_id: int):
    c = Client.query.get_or_404(client_id)

    keg = client_keg_summary(client_id)
    equip_rows = client_equip_summary(client_id)

    moves_keg = (
        KegMove.query.filter_by(client_id=client_id)
        .order_by(KegMove.mov_date.desc(), KegMove.id.desc())
        .limit(50)
        .all()
    )
    moves_equip = (
        EquipMove.query.filter_by(client_id=client_id)
        .order_by(EquipMove.mov_date.desc(), EquipMove.id.desc())
        .limit(50)
        .all()
    )

    tpl = """
    <!doctype html><html lang="fr"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Fiche client</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
    </head><body class="bg-light"><div class="container py-4">
      <div class="d-flex justify-content-between align-items-center mb-3">
        <h1 class="h3 mb-0">Client — {{ c.name }}</h1>
        <a class="btn btn-secondary" href="{{ url_for('index') }}">← Retour</a>
      </div>

      <div class="row g-3 mb-4">
        <div class="col-md-4"><div class="card shadow-sm"><div class="card-body">
          <div class="text-muted">Fûts présents</div>
          <div class="fs-3">{{ keg.present_kegs or 0 }}</div>
        </div></div></div>
        <div class="col-md-4"><div class="card shadow-sm"><div class="card-body">
          <div class="text-muted">Consigne en cours</div>
          <div class="fs-3">{{ (keg.consigne_cents or 0)|eur }}</div>
        </div></div></div>
        <div class="col-md-4"><div class="card shadow-sm"><div class="card-body">
          <div class="text-muted">Matériel en prêt</div>
          <div class="fs-3">
            {% set total_e = 0 %}
            {% for _, q in equip_rows %}{% set total_e = total_e + q %}{% endfor %}
            {{ total_e }}
          </div>
        </div></div></div>
      </div>

      <h2 class="h5">Saisir un mouvement</h2>
      <form method="post" action="{{ url_for('movement_new', client_id=c.id) }}" class="card p-3 shadow-sm mb-4">
        <div class="row g-2">
          <div class="col-12 col-md-3">
            <label class="form-label">Date</label>
            <input type="date" class="form-control" name="mov_date" value="{{ (now or '') }}">
          </div>
          <div class="col-4 col-md-3">
            <label class="form-label">Livraison fûts</label>
            <input type="number" class="form-control" name="qty_out" value="0" min="0">
          </div>
          <div class="col-4 col-md-3">
            <label class="form-label">Reprise fûts</label>
            <input type="number" class="form-control" name="qty_in" value="0" min="0">
          </div>
          <div class="col-4 col-md-3">
            <label class="form-label">Défectueux</label>
            <input type="number" class="form-control" name="qty_defect" value="0" min="0">
          </div>
        </div>

        <hr>

        <div class="row g-2 align-items-end">
          <div class="col-12 col-md-6">
            <label class="form-label">Matériel (libellé)</label>
            <input type="text" class="form-control" name="equip_label" placeholder="ex: Tirage 2 voies">
          </div>
          <div class="col-6 col-md-3">
            <label class="form-label">Prêt</label>
            <input type="number" class="form-control" name="equip_out" value="0" min="0">
          </div>
          <div class="col-6 col-md-3">
            <label class="form-label">Retour</label>
            <input type="number" class="form-control" name="equip_in" value="0" min="0">
          </div>
        </div>

        <div class="mt-3">
          <button class="btn btn-primary" type="submit">Enregistrer</button>
        </div>
      </form>

      <div class="row g-3">
        <div class="col-md-6">
          <h2 class="h5">Historique fûts</h2>
          <div class="list-group">
            {% for m in moves_keg %}
            <div class="list-group-item">
              <div class="d-flex justify-content-between">
                <strong>{{ m.mov_date }}</strong>
                <form method="post" action="{{ url_for('movement_keg_delete', move_id=m.id, client_id=c.id) }}" onsubmit="return confirm('Supprimer ce mouvement fûts ?');">
                  <button class="btn btn-sm btn-outline-danger">Supprimer</button>
                </form>
              </div>
              <div>Livré: {{ m.qty_out }} • Repris: {{ m.qty_in }} • Défectueux: {{ m.qty_defect }}</div>
            </div>
            {% else %}
            <div class="list-group-item text-muted">Aucun mouvement.</div>
            {% endfor %}
          </div>
        </div>

        <div class="col-md-6">
          <h2 class="h5">Historique matériel</h2>
          <div class="list-group">
            {% for e in moves_equip %}
            <div class="list-group-item">
              <div class="d-flex justify-content-between">
                <strong>{{ e.mov_date }} — {{ e.label }}</strong>
                <form method="post" action="{{ url_for('movement_equip_delete', move_id=e.id, client_id=c.id) }}" onsubmit="return confirm('Supprimer ce mouvement matériel ?');">
                  <button class="btn btn-sm btn-outline-danger">Supprimer</button>
                </form>
              </div>
              <div>Prêté: {{ e.qty_out }} • Retourné: {{ e.qty_in }}</div>
            </div>
            {% else %}
            <div class="list-group-item text-muted">Aucun mouvement.</div>
            {% endfor %}
          </div>
        </div>
      </div>
    </div></body></html>
    """
    return render_template_string(
        tpl,
        c=c,
        keg=keg,
        equip_rows=equip_rows,
        moves_keg=moves_keg,
        moves_equip=moves_equip,
        now=date.today().isoformat(),
    )


@app.route("/client/<int:client_id>/movement/new", methods=["POST"])
def movement_new(client_id: int):
    Client.query.get_or_404(client_id)

    mov_date_str = request.form.get("mov_date") or date.today().isoformat()
    try:
        y, m, d = [int(x) for x in mov_date_str.split("-")]
        mov_date = date(y, m, d)
    except Exception:
        mov_date = date.today()

    # Fûts
    qty_out = int(request.form.get("qty_out") or 0)
    qty_in = int(request.form.get("qty_in") or 0)
    qty_defect = int(request.form.get("qty_defect") or 0)
    if qty_out or qty_in or qty_defect:
        db.session.add(
            KegMove(
                client_id=client_id,
                mov_date=mov_date,
                qty_out=qty_out,
                qty_in=qty_in,
                qty_defect=qty_defect,
            )
        )

    # Matériel
    equip_label = (request.form.get("equip_label") or "").strip()
    equip_out = int(request.form.get("equip_out") or 0)
    equip_in = int(request.form.get("equip_in") or 0)
    if equip_label and (equip_out or equip_in):
        db.session.add(
            EquipMove(
                client_id=client_id,
                mov_date=mov_date,
                label=equip_label,
                qty_out=equip_out,
                qty_in=equip_in,
            )
        )

    db.session.commit()
    flash("Mouvement enregistré.", "success")
    return redirect(url_for("client_detail", client_id=client_id))


@app.route("/client/<int:client_id>/movement/keg/delete/<int:move_id>", methods=["POST"])
def movement_keg_delete(client_id: int, move_id: int):
    m = KegMove.query.filter_by(id=move_id, client_id=client_id).first_or_404()
    db.session.delete(m)
    db.session.commit()
    flash("Mouvement fûts supprimé.", "success")
    return redirect(url_for("client_detail", client_id=client_id))


@app.route("/client/<int:client_id>/movement/equip/delete/<int:move_id>", methods=["POST"])
def movement_equip_delete(client_id: int, move_id: int):
    e = EquipMove.query.filter_by(id=move_id, client_id=client_id).first_or_404()
    db.session.delete(e)
    db.session.commit()
    flash("Mouvement matériel supprimé.", "success")
    return redirect(url_for("client_detail", client_id=client_id))
