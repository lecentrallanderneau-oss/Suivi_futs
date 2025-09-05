from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
# app.py — WSGI target: gunicorn app:app
import os
from datetime import datetime, date, time
from types import SimpleNamespace

from flask import (
    Flask, render_template, render_template_string, request, redirect,
    url_for, flash, session, Response
)
from jinja2 import TemplateNotFound
from sqlalchemy import func, and_

from models import db, Client, Product, Variant, Movement, ReorderRule
from seed import seed_if_empty
import utils as U


# ---------- Helpers locaux ----------
def safe_now():
    try:
        return U.now_utc()
    except Exception:
        return datetime.utcnow()

def _is_cup_name(name: str) -> bool:
    if not name:
        return False
    n = name.lower()
    if any(w in n for w in ["lavage", "wash", "clean", "perdu", "perte"]):
        return False
    return ("ecocup" in n) or ("eco cup" in n) or ("gobelet" in n)

def safe_default_deposit(product: Product) -> float:
    """
    Consigne par défaut si non fournie:
      - 0 € : lignes 'maintenance' (lavage/perdu/clean/wash)
      - 1 € : ecocup/gobelet
      - 30 € : fûts
    Essaie utils.default_deposit_for_product si présent, sinon ce fallback.
    """
    try:
        if hasattr(U, "default_deposit_for_product"):
            return float(U.default_deposit_for_product(product))
    except Exception:
        pass
    pname = (product.name if product else "") or ""
    n = pname.lower()
    if any(w in n for w in ["lavage", "wash", "clean", "perdu", "perte"]):
        return 0.0
    return 1.0 if _is_cup_name(pname) else 30.0


# ---------- Data guard : crée / normalise le produit “Ecocup” ----------
def _ensure_ecocup_simple():
    """
    Si aucun produit 'Ecocup' générique n'existe (hors 'lavage/perdu/perte/wash/clean'),
    on le crée + 1 variante avec size_l=1. On normalise aussi les variantes existantes :
    size_l=NULL -> 1 ; price_ttc=NULL -> 0.0.
    """
    name_lc = func.lower(Product.name)
    is_cup = (name_lc.like("%ecocup%") | name_lc.like("%eco cup%") | name_lc.like("%gobelet%"))
    is_maintenance = (
        name_lc.like("%lavage%") | name_lc.like("%perdu%") | name_lc.like("%perte%")
        | name_lc.like("%wash%") | name_lc.like("%clean%")
    )

    p = Product.query.filter(is_cup, ~is_maintenance).first()
    if not p:
        p = Product(name="Ecocup")
        db.session.add(p)
        db.session.flush()

    vs = Variant.query.filter_by(product_id=p.id).all()
    changed = False
    for v in vs:
        if v.size_l is None:
            v.size_l = 1
            if v.price_ttc is None:
                v.price_ttc = 0.0
            changed = True
    if changed:
        db.session.flush()

    if not Variant.query.filter_by(product_id=p.id).first():
        db.session.add(Variant(product_id=p.id, size_l=1, price_ttc=0.0))

    db.session.commit()


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "dev")

    # Render fournit DATABASE_URL ; SQLAlchemy accepte aussi ce format.
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///data.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    # ----------------- bootstrap DB -----------------
    with app.app_context():
        db.create_all()
        seed_if_empty()
        _ensure_ecocup_simple()

    # ----------------- Healthcheck -----------------
    @app.route("/healthz", methods=["GET", "HEAD"])
    def healthz():
        return Response("ok", status=200, mimetype="text/plain")

    # ----------------- Filtres Jinja -----------------
    @app.template_filter("dt")
    def fmt_dt(value):
        if not value:
            return ""
        try:
            return value.strftime("%d/%m/%Y")
        except Exception:
            return str(value)

    @app.template_filter("eur")
    def fmt_eur(v):
        if v is None:
            return "-"
        return f"{v:,.2f} €".replace(",", " ").replace(".", ",")

    @app.template_filter("signed_eur")
    def fmt_signed_eur(v):
        if v is None:
            return "-"
        s = "+" if v >= 0 else "−"
        return f"{s}{abs(v):,.2f} €".replace(",", " ").replace(".", ",")

    # ----------------- Helpers -----------------
    def _open_qty_by_variant(client_id: int):
        """
        Balance par variante pour un client : OUT - (IN + DEFECT + FULL).
        > 0  => encore “en jeu” chez le client.
        """
        out_rows = dict(
            db.session.query(Movement.variant_id, func.coalesce(func.sum(Movement.qty), 0))
            .filter(Movement.client_id == client_id, Movement.type == "OUT")
            .group_by(Movement.variant_id)
            .all()
        )
        back_rows = dict(
            db.session.query(Movement.variant_id, func.coalesce(func.sum(Movement.qty), 0))
            .filter(Movement.client_id == client_id, Movement.type.in_(["IN", "DEFECT", "FULL"]))
            .group_by(Movement.variant_id)
            .all()
        )
        all_vids = set(out_rows) | set(back_rows)
        return {vid: int(out_rows.get(vid, 0)) - int(back_rows.get(vid, 0)) for vid in all_vids}

    def _hide_ecocup_maintenance(query):
        """Masque les produits Ecocup ‘lavage/perdu…’ dans les listings."""
        name_lc = func.lower(Product.name)
        is_cup = (name_lc.like("%ecocup%") | name_lc.like("%eco cup%") | name_lc.like("%gobelet%"))
        is_maintenance = (
            name_lc.like("%lavage%") | name_lc.like("%perdu%") | name_lc.like("%perte%")
            | name_lc.like("%wash%") | name_lc.like("%clean%")
        )
        return query.filter(~and_(is_cup, is_maintenance))

    # ----------------- Pages -----------------
    @app.route("/")
def index():
    """
    Accueil : affiche
      - les mouvements "d'aujourd'hui" en Europe/Paris (livraisons + reprises)
      - les 12 derniers mouvements (toutes dates)
      - la liste des clients
    """
    # Fenêtre locale Europe/Paris (évite le décalage UTC le soir)
    tz = ZoneInfo("Europe/Paris")
    now_paris = datetime.now(tz)
    start_paris = datetime.combine(now_paris.date(), time(0, 0, 0), tzinfo=tz)
    end_paris = start_paris + timedelta(days=1) - timedelta(microseconds=1)

    # Si created_at est aware (tz): on convertit la fenêtre en UTC pour comparer
    start_utc = start_paris.astimezone(timezone.utc)
    end_utc = end_paris.astimezone(timezone.utc)

    # Modèle supposé : Movement(created_at, movement_type in {'delivery','return'})
    # "Aujourd'hui" (Europe/Paris)
    day_movements = (
        Movement.query
        .filter(Movement.created_at >= start_utc, Movement.created_at <= end_utc)
        .order_by(Movement.created_at.desc())
        .limit(20)
        .all()
    )

    # Fil récent toutes dates (mix livraison + reprise)
    recent_movements = (
        Movement.query
        .order_by(Movement.created_at.desc())
        .limit(12)
        .all()
    )

    # Clients pour les tuiles de l’accueil
    clients = Client.query.order_by(Client.name.asc()).all()

    return render_template(
        "index.html",
        clients=clients,
        day_movements=day_movements,
        recent_movements=recent_movements,
    )

    @app.route("/clients")
    def clients():
        clients = Client.query.order_by(Client.name.asc()).all()
        return render_template("clients.html", clients=clients)

    @app.route("/client/new", methods=["GET", "POST"])
    def client_new():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            if not name:
                flash("Nom obligatoire.", "warning")
                return render_template("client_form.html", client=None, is_edit=False)
            c = Client(name=name)
            db.session.add(c)
            db.session.commit()
            flash("Client créé.", "success")
            return redirect(url_for("clients"))
        return render_template("client_form.html", client=None, is_edit=False)

    @app.route("/client/<int:client_id>/edit", methods=["GET", "POST"])
    def client_edit(client_id):
        c = Client.query.get_or_404(client_id)
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            if not name:
                flash("Nom obligatoire.", "warning")
                return render_template("client_form.html", client=c, is_edit=True)
            c.name = name
            db.session.commit()
            flash("Client modifié.", "success")
            return redirect(url_for("clients"))
        return render_template("client_form.html", client=c, is_edit=True)

    @app.route("/client/<int:client_id>")
    def client_detail(client_id):
        c = Client.query.get_or_404(client_id)
        view = U.summarize_client_detail(c)

        movements = (
            db.session.query(Movement, Variant, Product)
            .join(Variant, Movement.variant_id == Variant.id)
            .join(Product, Variant.product_id == Product.id)
            .filter(Movement.client_id == client_id)
            .order_by(Movement.created_at.desc(), Movement.id.desc())
            .all()
        )

        # --- Consignes Ecocup vs Fûts ---
        dep_cup_eur, cup_qty, dep_keg_eur, keg_qty = U.compute_deposits_split(client_id)

        return render_template(
            "client_detail.html",
            client=c,
            view=view,
            movements=movements,
            beer_billed_cum=view.get("beer_eur", 0.0),
            deposit_in_play=view.get("deposit_eur", 0.0),
            equipment_totals=view.get("equipment", {}),
            liters_out_cum=view.get("liters_out_cum", 0.0),
            litres_out_cum=view.get("liters_out_cum", 0.0),

            # Nouvelles variables séparées
            deposit_cup_eur=dep_cup_eur,
            deposit_keg_eur=dep_keg_eur,
            cup_qty_in_play=cup_qty,
            keg_qty_in_play=keg_qty,
        )

    # ---------- Confirmation + suppression client ----------
    @app.route("/client/<int:client_id>/confirm-delete", methods=["GET"])
    def client_confirm_delete(client_id):
        c = Client.query.get_or_404(client_id)
        mv_count = Movement.query.filter_by(client_id=client_id).count()

        balance_map = _open_qty_by_variant(client_id)
        open_details = []
        open_total = 0
        for vid, q in balance_map.items():
            if q > 0:
                v = Variant.query.get(vid)
                if not v:
                    continue
                p = v.product
                open_total += q
                open_details.append(SimpleNamespace(
                    product_name=p.name if p else "Produit",
                    size_l=v.size_l,
                    qty=q
                ))

        view = U.summarize_client_detail(c)
        deposit_in_play = float(view.get("deposit_eur", 0.0) or 0.0)
        equipment_dict = view.get("equipment", {}) or {}
        try:
            equipment_in_play = sum(int(v or 0) for v in equipment_dict.values())
        except Exception:
            equipment_in_play = 0

        eps_eur = 0.01
        blocked = (open_total > 0) or (abs(deposit_in_play) > eps_eur) or (equipment_in_play > 0)

        try:
            return render_template(
                "client_confirm_delete.html",
                client=c,
                mv_count=mv_count,
                open_total=open_total,
                open_details=open_details,
                deposit_in_play=deposit_in_play,
                equipment_in_play=equipment_in_play,
                blocked=blocked,
            )
        except TemplateNotFound:
            html = """
            {% extends "base.html" %}
            {% block title %}Supprimer le client{% endblock %}
            {% block content %}
            <div class="card border-danger">
              <div class="card-header bg-danger text-white">⚠️ Suppression définitive du client</div>
              <div class="card-body">
                <h5 class="card-title mb-3">{{ client.name }}</h5>
                {% if open_total and open_total > 0 %}
                  <div class="alert alert-warning">
                    Il reste <strong>{{ open_total }}</strong> article(s) en jeu :
                    <ul class="mb-0">
                      {% for it in open_details %}
                        <li>{{ it.product_name }}{% if it.size_l %} — {{ it.size_l }} L{% endif %} : {{ it.qty }}</li>
                      {% endfor %}
                    </ul>
                  </div>
                {% endif %}
                {% if deposit_in_play and (deposit_in_play | float | abs) > 0.01 %}
                  <div class="alert alert-info">Consignes en jeu : <strong>{{ deposit_in_play | eur }}</strong>.</div>
                {% endif %}
                {% if equipment_in_play and equipment_in_play > 0 %}
                  <div class="alert alert-secondary">Matériel en jeu : <strong>{{ equipment_in_play }}</strong>.</div>
                {% endif %}
                {% if blocked %}
                  <div class="alert alert-danger">Suppression <strong>bloquée</strong> : le client n’est pas à zéro partout.</div>
                  <a class="btn btn-outline-secondary" href="{{ url_for('client_detail', client_id=client.id) }}">Retour</a>
                {% else %}
                  <form method="post" action="{{ url_for('client_delete', client_id=client.id) }}" class="d-flex gap-2 mt-3">
                    <a class="btn btn-outline-secondary" href="{{ url_for('client_detail', client_id=client.id) }}">Annuler</a>
                    <button class="btn btn-danger" type="submit"
                            onclick="return confirm('Confirmer la suppression définitive ?');">Supprimer définitivement</button>
                  </form>
                {% endif %}
              </div>
            </div>
            {% endblock %}
            """
            return render_template_string(
                html,
                client=c,
                mv_count=mv_count,
                open_total=open_total,
                open_details=open_details,
                deposit_in_play=deposit_in_play,
                equipment_in_play=equipment_in_play,
                blocked=blocked,
            )

    @app.route("/client/<int:client_id>/delete", methods=["POST"])
    def client_delete(client_id):
        c = Client.query.get_or_404(client_id)

        # Bloquage si pas à zéro (ou consignes/matériel en jeu)
        open_map = _open_qty_by_variant(client_id)
        open_total = sum(q for q in open_map.values() if q > 0)

        view = U.summarize_client_detail(c)
        deposit_in_play = float(view.get("deposit_eur", 0.0) or 0.0)
        equipment_dict = view.get("equipment", {}) or {}
        try:
            equipment_in_play = sum(int(v or 0) for v in equipment_dict.values())
        except Exception:
            equipment_in_play = 0

        eps_eur = 0.01
        blocked = (open_total > 0) or (abs(deposit_in_play) > eps_eur) or (equipment_in_play > 0)
        if blocked:
            msg_parts = []
            if open_total > 0:
                msg_parts.append(f"{open_total} article(s) encore en jeu")
            if abs(deposit_in_play) > eps_eur:
                msg_parts.append(f"consignes en jeu {deposit_in_play:.2f} €")
            if equipment_in_play > 0:
                msg_parts.append(f"matériel en jeu ({equipment_in_play})")
            flash("Suppression impossible : " + ", ".join(msg_parts) + ".", "danger")
            return redirect(url_for("client_confirm_delete", client_id=client_id))

        if session.get("wiz", {}).get("client_id") == client_id:
            session["wiz"].pop("client_id", None)
            session.modified = True

        # Rétablit l'inventaire magasin pour OUT/FULL, puis supprime
        movements = Movement.query.filter_by(client_id=client_id).all()
        for m in movements:
            if m.variant_id and m.qty:
                inv = U.get_or_create_inventory(m.variant_id)
                if m.type == "OUT":
                    inv.qty = (inv.qty or 0) + (m.qty or 0)
                elif m.type == "FULL":
                    inv.qty = (inv.qty or 0) - (m.qty or 0)
            db.session.delete(m)

        db.session.delete(c)
        db.session.commit()
        flash("Client supprimé définitivement (zéro partout confirmé).", "success")
        return redirect(url_for("clients"))

    # ----------------- CATALOGUE -----------------
    @app.route("/catalog", methods=["GET", "POST"])
    def catalog():
        if request.method == "POST":
            action = request.form.get("action")

            if action == "update_prices":
                updated = 0
                for k, v in request.form.items():
                    if not k.startswith("price_"):
                        continue
                    try:
                        vid = int(k.split("_", 1)[1])
                    except Exception:
                        continue
                    price = request.form.get(k)
                    if price in ("", None):
                        new_price = None
                    else:
                        try:
                            new_price = float(price.replace(",", "."))
                        except Exception:
                            continue
                    var = Variant.query.get(vid)
                    if not var:
                        continue
                    var.price_ttc = new_price
                    updated += 1
                db.session.commit()
                flash(f"Prix mis à jour ({updated} lignes).", "success")
                return redirect(url_for("catalog"))

            if action == "create":
                name = (request.form.get("name") or "").strip()
                size_l_raw = request.form.get("size_l")
                price_raw = request.form.get("price_ttc")

                if not name:
                    flash("Le nom du produit est obligatoire.", "warning")
                    return redirect(url_for("catalog"))

                is_cup_name = any(s in name.lower() for s in ["ecocup", "eco cup", "gobelet"])
                if is_cup_name:
                    size_l = 1
                else:
                    try:
                        size_l = int(size_l_raw) if size_l_raw not in ("", None) else None
                    except Exception:
                        size_l = None

                if size_l is None:
                    flash("Le volume (L) est obligatoire (sauf Ecocup où il est automatique).", "warning")
                    return redirect(url_for("catalog"))

                try:
                    price_ttc = float(price_raw.replace(",", ".")) if price_raw not in ("", None) else 0.0
                except Exception:
                    price_ttc = 0.0

                p = Product.query.filter(func.lower(Product.name) == name.lower()).first()
                if not p:
                    p = Product(name=name)
                    db.session.add(p)
                    db.session.flush()

                v = Variant.query.filter_by(product_id=p.id, size_l=size_l).first()
                if v:
                    v.price_ttc = price_ttc
                else:
                    v = Variant(product_id=p.id, size_l=size_l, price_ttc=price_ttc)
                    db.session.add(v)
                    db.session.flush()

                U.get_or_create_inventory(v.id)
                db.session.commit()
                flash("Référence créée/mise à jour avec succès.", "success")
                return redirect(url_for("catalog"))

            return redirect(url_for("catalog"))

        q = (
            db.session.query(Variant, Product)
            .join(Product, Variant.product_id == Product.id)
            .order_by(Product.name, Variant.size_l)
        )
        q = _hide_ecocup_maintenance(q)
        rows = q.all()
        items = [SimpleNamespace(variant=v, product=p) for (v, p) in rows]
        alerts = U.compute_reorder_alerts()

        return render_template("catalog.html", rows=rows, items=items, alerts=alerts)

    # ------------- Saisie (wizard) -------------
    @app.route("/movement/new", methods=["GET"])
    def movement_new():
        client_id = request.args.get("client_id", type=int)
        if client_id:
            return redirect(url_for("movement_wizard", step=1, client_id=client_id))
        return redirect(url_for("movement_wizard", step=1))

    @app.route("/movement/wizard", methods=["GET", "POST"])
    def movement_wizard():
        if "wiz" not in session:
            session["wiz"] = {}
        wiz = session["wiz"]

        pre_client_id = request.args.get("client_id", type=int)
        if pre_client_id:
            wiz["client_id"] = pre_client_id
            session.modified = True

        if request.args.get("clear_client"):
            wiz.pop("client_id", None)
            session.modified = True

        try:
            step = int(request.args.get("step", 1))
        except Exception:
            step = 1

        # --- ÉTAPE 1 : type + date (+ client si non fixé) ---
        if step == 1:
            if request.method == "POST":
                if "client_id" not in wiz:
                    wiz["client_id"] = request.form.get("client_id", type=int)
                wiz["type"] = request.form.get("type")
                wiz["date"] = request.form.get("date")
                session.modified = True
                if not wiz.get("client_id") or not wiz.get("type"):
                    flash("Sélectionne un client et un type de mouvement.", "warning")
                    prefill_client = Client.query.get(wiz.get("client_id")) if wiz.get("client_id") else None
                    return render_template(
                        "movement_wizard.html",
                        step=1,
                        clients=Client.query.order_by(Client.name.asc()).all(),
                        prefill_client=prefill_client,
                        rows=[],
                        wiz=wiz,
                    )
                return redirect(url_for("movement_wizard", step=2))

            clients_list = Client.query.order_by(Client.name.asc()).all()
            prefill_client = Client.query.get(wiz.get("client_id")) if wiz.get("client_id") else None
            return render_template("movement_wizard.html", step=1, clients=clients_list, prefill_client=prefill_client, rows=[], wiz=wiz)

        # --- ÉTAPE 2 : choix variantes ---
        if step == 2:
            if request.method == "POST":
                vids = request.form.getlist("variant_id") or request.form.getlist("variant_ids")
                if not vids:
                    flash("Sélectionne au moins un produit.", "warning")
                    return redirect(url_for("movement_wizard", step=2))
                try:
                    wiz["variant_ids"] = [int(v) for v in vids]
                except Exception:
                    wiz["variant_ids"] = []
                session.modified = True
                return redirect(url_for("movement_wizard", step=3))

            clients_list = Client.query.order_by(Client.name.asc()).all()
            base_q = (
                db.session.query(Variant, Product)
                .join(Product, Variant.product_id == Product.id)
                .order_by(Product.name, Variant.size_l)
            )
            base_q = _hide_ecocup_maintenance(base_q)

            if wiz.get("type") == "IN" and wiz.get("client_id"):
                open_map = _open_qty_by_variant(wiz["client_id"])
                allowed_ids = {vid for vid, openq in open_map.items() if openq > 0}
                equip_ids = set(
                    vid for (vid,) in (
                        db.session.query(Variant.id)
                        .join(Product, Variant.product_id == Product.id)
                        .filter(
                            and_(func.lower(Product.name).like("%matériel%"), func.lower(Product.name).like("%seul%"))
                            | and_(func.lower(Product.name).like("%materiel%"), func.lower(Product.name).like("%seul%"))
                        )
                        .all()
                    )
                )
                final_ids = list(allowed_ids | equip_ids)
                if final_ids:
                    base_q = base_q.filter(Variant.id.in_(final_ids))

            rows2 = base_q.all()
            prefill_client = Client.query.get(wiz.get("client_id")) if wiz.get("client_id") else None
            return render_template("movement_wizard.html", step=2, clients=clients_list, rows=rows2, wiz=wiz, prefill_client=prefill_client)

        # --- ÉTAPE 3 : saut vers 4 ---
        if step == 3:
            return redirect(url_for("movement_wizard", step=4))

        # --- ÉTAPE 4 : saisie + enregistrement ---
        if step == 4:
            if request.method == "POST":
                try:
                    if (wiz.get("client_id") is None) or (wiz.get("type") is None):
                        flash("Informations incomplètes.", "warning")
                        return redirect(url_for("movement_wizard", step=1))

                    if wiz.get("date"):
                        try:
                            y, m_, d2 = [int(x) for x in wiz["date"].split("-")]
                            created_at = datetime.combine(date(y, m_, d2), time(hour=12))
                        except Exception:
                            created_at = safe_now()
                    else:
                        created_at = safe_now()

                    variant_ids = request.form.getlist("variant_id") or request.form.getlist("variant_ids")
                    qtys = request.form.getlist("qty") or request.form.getlist("qtys")
                    unit_prices = request.form.getlist("unit_price_ttc") or request.form.getlist("unit_prices")
                    deposits = request.form.getlist("deposit_per_keg") or request.form.getlist("deposits")
                    notes = request.form.get("notes") or None

                    # Encodage équipement dans notes (comme avant)
                    t = request.form.get("eq_tireuse", type=int)
                    c2 = request.form.get("eq_co2", type=int)
                    cpt = request.form.get("eq_comptoir", type=int)
                    ton = request.form.get("eq_tonnelle", type=int)
                    equip_parts = []
                    if t:   equip_parts.append(f"tireuse={t}")
                    if c2:  equip_parts.append(f"co2={c2}")
                    if cpt: equip_parts.append(f"comptoir={cpt}")
                    if ton: equip_parts.append(f"tonnelle={ton}")
                    notes = (";".join(equip_parts) + (";" + notes if notes else "")) or None

                    client_id2 = int(wiz["client_id"])
                    mtype = wiz["type"]

                    violations = []
                    inserted = 0

                    open_map = _open_qty_by_variant(client_id2) if mtype == "IN" else {}

                    line_count = max(len(variant_ids), len(qtys))
                    for i in range(line_count):
                        if i >= len(variant_ids):
                            continue
                        vid_raw = variant_ids[i]
                        if not vid_raw:
                            continue
                        try:
                            vid_int = int(vid_raw)
                        except Exception:
                            continue

                        q_raw = qtys[i] if i < len(qtys) else ""
                        try:
                            qty_int = max(0, int(q_raw)) if (q_raw not in (None, "")) else 0
                        except Exception:
                            qty_int = 0

                        up_raw = unit_prices[i] if i < len(unit_prices) else None
                        if up_raw in ("", None):
                            up = None
                        else:
                            try:
                                up = float(str(up_raw).replace(",", "."))
                            except Exception:
                                up = None

                        dep_raw = deposits[i] if i < len(deposits) else None
                        if dep_raw in ("", None):
                            dep = None
                        else:
                            try:
                                dep = float(str(dep_raw).replace(",", "."))
                            except Exception:
                                dep = None

                        v = Variant.query.get(vid_int)
                        if not v:
                            continue
                        p = v.product
                        pname = (p.name if p else "") or ""

                        # Produit "matériel seul" -> on force à 0/0/0 mais on enregistre la ligne (notes)
                        is_equipment_only = ("matériel" in pname.lower() or "materiel" in pname.lower()) and ("seul" in pname.lower())
                        if is_equipment_only:
                            qty_int = 0
                            up = 0.0
                            dep = 0.0
                        else:
                            if up is None and (v.price_ttc is not None):
                                up = v.price_ttc
                            if dep is None:
                                dep = safe_default_deposit(p)

                            if mtype == "IN":
                                open_q = int(open_map.get(vid_int, 0))
                                if open_q <= 0 or qty_int > open_q:
                                    label = f"{p.name} — {v.size_l} L" if (p and v.size_l) else (p.name if p else f"Var#{vid_int}")
                                    violations.append((label, open_q))
                                    continue

                        mv = Movement(
                            client_id=client_id2,
                            variant_id=vid_int,
                            type=mtype,
                            qty=qty_int,
                            unit_price_ttc=up,
                            deposit_per_keg=dep,
                            notes=notes,
                            created_at=created_at,
                        )
                        db.session.add(mv)
                        inserted += 1

                        inv = U.get_or_create_inventory(vid_int)
                        if mtype == "OUT":
                            inv.qty = (inv.qty or 0) - qty_int
                        elif mtype == "FULL":
                            inv.qty = (inv.qty or 0) + qty_int
                        # (IN ne touche pas au stock magasin)

                    if violations:
                        msg = "Certaines lignes de retour dépassaient l’enjeu et ont été ignorées : " + ", ".join(
                            f"{lab} (max {q})" for lab, q in violations
                        )
                        flash(msg, "warning")

                    if inserted > 0:
                        db.session.commit()
                        flash("Saisie enregistrée.", "success")
                    else:
                        flash("Aucune ligne valide n’a été enregistrée.", "warning")

                    session.pop("wiz", None)
                    return redirect(url_for("client_detail", client_id=client_id2))

                except Exception as e:
                    app.logger.exception("Erreur à l'étape 4 (POST)")
                    flash("Oups, une erreur est survenue à l’enregistrement. Rien n’a été validé.", "danger")
                    # on reste sur l'étape 4 pour corriger
                    return redirect(url_for("movement_wizard", step=4))

            # GET step=4
            selected = []
            rows_for_template = []
            for vid in (wiz.get("variant_ids") or []):
                v = Variant.query.get(vid)
                if v:
                    selected.append((v, v.product))
                    rows_for_template.append((v, v.product))
            if not selected:
                flash("Aucun produit sélectionné. Reviens à l’étape 2.", "info")
                return redirect(url_for("movement_wizard", step=2))

            clients_list = Client.query.order_by(Client.name.asc()).all()
            prefill_client = Client.query.get(wiz.get("client_id")) if wiz.get("client_id") else None
            return render_template("movement_wizard.html",
                                   step=4,
                                   wiz=wiz,
                                   clients=clients_list,
                                   prefill_client=prefill_client,
                                   rows=rows_for_template,
                                   selected=selected)

        # fallback si step inconnu
        return redirect(url_for("movement_wizard", step=1))

    # ---- Suppression mouvement ----
    @app.route("/movement/<int:movement_id>/confirm-delete", methods=["GET"])
    def movement_confirm_delete(movement_id):
        m = Movement.query.get_or_404(movement_id)
        return render_template("movement_confirm_delete.html", m=m)

    @app.route("/movement/<int:movement_id>/delete", methods=["POST"])
    def movement_delete(movement_id):
        m = Movement.query.get_or_404(movement_id)
        client_id = m.client_id

        if m.qty is not None and m.variant_id:
            inv = U.get_or_create_inventory(m.variant_id)
            if m.type == "OUT":
                inv.qty = (inv.qty or 0) + (m.qty or 0)
            elif m.type == "FULL":
                inv.qty = (inv.qty or 0) - (m.qty or 0)

        db.session.delete(m)
        db.session.commit()
        flash("Saisie supprimée.", "success")
        return redirect(url_for("client_detail", client_id=client_id))

    # ---- Stock ----
    @app.route("/stock", methods=["GET", "POST"])
    def stock():
        if request.method == "POST":
            changed = 0
            for k, v in request.form.items():
                if k.startswith("qty_"):
                    vid = int(k.split("_", 1)[1])
                    inv = U.get_or_create_inventory(vid)
                    inv.qty = int(v or 0)
                    changed += 1
                elif k.startswith("min_"):
                    vid = int(k.split("_", 1)[1])
                    rr = ReorderRule.query.filter_by(variant_id=vid).first()
                    if not rr:
                        rr = ReorderRule(variant_id=vid, min_qty=int(v or 0))
                        db.session.add(rr)
                    else:
                        rr.min_qty = int(v or 0)
                    changed += 1
            db.session.commit()
            flash(f"Inventaire enregistré ({changed} mise(s) à jour).", "success")
            return redirect(url_for("stock"))

        rows_raw = U.get_stock_items()  # [(Variant, Product, inv_qty, min_qty), ...]
        rows = [
            SimpleNamespace(variant=v, product=p, inv_qty=inv_qty, min_qty=min_qty)
            for (v, p, inv_qty, min_qty) in rows_raw
        ]
        alerts = U.compute_reorder_alerts()
        return render_template("stock.html", rows=rows, rows_raw=rows_raw, alerts=alerts)

    @app.route("/product/<int:variant_id>")
    def product_variant(variant_id):
        v = Variant.query.get_or_404(variant_id)
        return render_template("product.html", variant=v, product=v.product)

    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("500.html"), 500

    return app


# Exposé WSGI : gunicorn app:app
app = create_app()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
