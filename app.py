# app.py — application principale

from datetime import date, datetime, time
import os

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
)
from sqlalchemy import func

# Modèles & DB
from models import db, Client, Product, Variant, Movement, Inventory

# Utilitaires (doit contenir: summarize_client_for_index, summarize_totals,
# compute_reorder_alerts, get_stock_items, summarize_client_detail,
# now_utc, default_deposit_for_product, get_or_create_inventory)
import utils as U


# -----------------------------------------------------------------------------
# App & Config
# -----------------------------------------------------------------------------
app = Flask(__name__)

# Clé de session
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key")

# Base de données
default_db = "sqlite:///data.db"
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", default_db)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

# Réglages Jinja
app.jinja_env.trim_blocks = True
app.jinja_env.lstrip_blocks = True


# -----------------------------------------------------------------------------
# Hooks
# -----------------------------------------------------------------------------
@app.before_request
def _ensure_session():
    # Wizard multi-étapes
    session.setdefault("wiz", {})


# -----------------------------------------------------------------------------
# Pages
# -----------------------------------------------------------------------------
@app.route("/")
def index():
    # Liste des clients -> cartes “pills” + totaux + alertes réassort
    clients = Client.query.order_by(Client.name.asc()).all()
    cards = [U.summarize_client_for_index(c) for c in clients]
    totals = U.summarize_totals(cards)
    alerts = U.compute_reorder_alerts()
    return render_template("index.html", cards=cards, totals=totals, alerts=alerts)


# --- Clients -----------------------------------------------------------------
@app.route("/clients")
def clients():
    items = Client.query.order_by(Client.name.asc()).all()
    return render_template("clients.html", clients=items)


@app.route("/client/<int:client_id>")
def client_detail(client_id):
    c = Client.query.get_or_404(client_id)
    view = U.summarize_client_detail(c)

    cup_eur = view["deposits"]["cup_eur"]
    cup_qty = view["deposits"]["cup_qty"]
    keg_eur = view["deposits"]["keg_eur"]
    keg_qty = view["deposits"]["keg_qty"]

    return render_template(
        "client_detail.html",
        # objets client (double nom pour compatibilité d’anciens templates)
        c=c,
        client=c,
        view=view,
        # consignes séparées
        cup_eur=cup_eur,
        cup_qty=cup_qty,
        keg_eur=keg_eur,
        keg_qty=keg_qty,
        # bière facturée cumulée
        beer_billed_cum=view["beer_eur"],
        # matériel + fûts en jeu
        equipment=view["equipment"],            # dict: tireuse, co2, comptoir, tonnelle
        open_rows=view["open_by_variant"],      # liste des fûts en jeu par variante
        kegs_qty=view["kegs_qty"],              # total fûts en jeu
        keg_qty_in_play=view["kegs_qty"],       # alias
    )


@app.route("/client/new", methods=["GET", "POST"])
def client_new():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Le nom du client est requis.", "warning")
            return render_template("client_form.html", client=None)

        c = Client(
            name=name,
            contact=(request.form.get("contact") or None),
            address=(request.form.get("address") or None),
            notes=(request.form.get("notes") or None),
        )
        db.session.add(c)
        db.session.commit()
        flash("Client créé.", "success")
        return redirect(url_for("client_detail", client_id=c.id))

    return render_template("client_form.html", client=None)


@app.route("/client/<int:client_id>/edit", methods=["GET", "POST"])
def client_edit(client_id):
    c = Client.query.get_or_404(client_id)
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Le nom du client est requis.", "warning")
            return render_template("client_form.html", client=c)

        c.name = name
        c.contact = (request.form.get("contact") or None)
        c.address = (request.form.get("address") or None)
        c.notes = (request.form.get("notes") or None)
        db.session.commit()
        flash("Fiche client mise à jour.", "success")
        return redirect(url_for("client_detail", client_id=c.id))

    return render_template("client_form.html", client=c)


@app.route("/client/<int:client_id>/confirm-delete")
def client_confirm_delete(client_id):
    c = Client.query.get_or_404(client_id)
    view = U.summarize_client_detail(c)

    # Bloquer si quoi que ce soit est en jeu (fûts, gobelets, matériel)
    blocked = (
        (view["kegs_qty"] or 0) > 0
        or (view["deposits"]["cup_qty"] or 0) > 0
        or any((view["equipment"].get(k) or 0) > 0 for k in ("tireuse", "co2", "comptoir", "tonnelle"))
    )

    return render_template(
        "client_confirm_delete.html",
        c=c,
        blocked=blocked,
        open_kegs=view["kegs_qty"],
        cup_qty=view["deposits"]["cup_qty"],
        eq=view["equipment"],
    )


@app.route("/client/<int:client_id>/delete", methods=["POST"])
def client_delete(client_id):
    c = Client.query.get_or_404(client_id)
    view = U.summarize_client_detail(c)
    if (
        (view["kegs_qty"] or 0) > 0
        or (view["deposits"]["cup_qty"] or 0) > 0
        or any((view["equipment"].get(k) or 0) > 0 for k in ("tireuse", "co2", "comptoir", "tonnelle"))
    ):
        flash("Suppression impossible : des éléments sont encore en jeu (fûts / gobelets / matériel).", "warning")
        return redirect(url_for("client_confirm_delete", client_id=c.id))

    # OK : on supprime le client (les mouvements associés doivent être gérés via FK ON DELETE
    # ou nettoyés séparément selon ton modèle)
    db.session.delete(c)
    db.session.commit()
    flash("Client supprimé.", "success")
    return redirect(url_for("clients"))


# --- Catalogue ----------------------------------------------------------------
@app.route("/catalog", methods=["GET", "POST"])
def catalog():
    if request.method == "POST":
        # Ajouter un produit et/ou une variante
        pname = (request.form.get("product_name") or "").strip()
        vsize = request.form.get("variant_size", type=int)
        vprice = request.form.get("variant_price_ttc", type=float)

        product = None
        if pname:
            product = Product(name=pname)
            db.session.add(product)
            db.session.flush()

        if product and vsize:
            v = Variant(product_id=product.id, size_l=vsize, price_ttc=vprice)
            db.session.add(v)

        db.session.commit()
        flash("Catalogue mis à jour.", "success")
        return redirect(url_for("catalog"))

    # Listing
    products = Product.query.order_by(Product.name.asc()).all()
    variants = (
        db.session.query(Variant, Product)
        .join(Product, Variant.product_id == Product.id)
        .order_by(Product.name.asc(), Variant.size_l.asc())
        .all()
    )
    alerts = U.compute_reorder_alerts()

    return render_template("catalog.html", products=products, variants=variants, alerts=alerts)


# --- Stock --------------------------------------------------------------------
@app.route("/stock")
def stock():
    # get_stock_items() -> liste de dicts:
    # {variant, product, inv_qty, min_qty}
    rows = U.get_stock_items()
    return render_template("stock.html", rows=rows)


# --- Mouvements : Wizard multi-étapes -----------------------------------------
@app.route("/movement/new")
def movement_new():
    # Permet d’appeler /movement/new?client_id=xx pour préremplir le wizard
    client_id = request.args.get("client_id", type=int)
    if client_id:
        # on positionne le client en session wizard et on saute à l’étape 1
        wiz = session.setdefault("wiz", {})
        wiz["client_id"] = client_id
        session.modified = True
        return redirect(url_for("movement_wizard", step=1, client_id=client_id))
    return redirect(url_for("movement_wizard", step=1))


@app.route("/movement/wizard", methods=["GET", "POST"])
def movement_wizard():
    # État du wizard en session
    wiz = session.setdefault("wiz", {})

    # Étape courante (1 par défaut)
    try:
        step = int(request.args.get("step", 1))
    except Exception:
        step = 1

    # ---------- ÉTAPE 1 : choix client / type / date ----------
    if step == 1:
        # Si on vient d’une fiche client -> client_id dans la query
        cid = request.args.get("client_id", type=int)
        if cid and not wiz.get("client_id"):
            wiz["client_id"] = cid
            session.modified = True

        if request.method == "POST":
            # Si un client est déjà figé (depuis la fiche client), on ne l’écrase pas
            if not wiz.get("client_id"):
                wiz["client_id"] = request.form.get("client_id", type=int)
            wiz["type"] = request.form.get("type")
            wiz["date"] = request.form.get("date")  # AAAA-MM-JJ (optionnel)
            session.modified = True

            # Contrôles légers
            if not wiz.get("client_id") or not wiz.get("type"):
                flash("Sélectionne un client et un type de mouvement.", "warning")
                clients = Client.query.order_by(Client.name.asc()).all()
                return render_template("movement_wizard.html", step=1, clients=clients, rows=[], wiz=wiz)

            return redirect(url_for("movement_wizard", step=2))

        # GET
        clients = Client.query.order_by(Client.name.asc()).all()
        return render_template("movement_wizard.html", step=1, clients=clients, rows=[], wiz=wiz)

    # ---------- ÉTAPE 2 : choix des produits/variantes ----------
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

        # GET : liste de variantes (avec filtrage pour retours IN)
        clients = Client.query.order_by(Client.name.asc()).all()

        base_q = (
            db.session.query(Variant, Product)
            .join(Product, Variant.product_id == Product.id)
            .order_by(Product.name.asc(), Variant.size_l.asc())
        )

        if wiz.get("type") == "IN" and wiz.get("client_id"):
            # n’autoriser que les variantes réellement en jeu chez ce client + “Matériel seul”
            def _open_kegs_by_variant(client_id: int):
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

            open_map = _open_kegs_by_variant(wiz["client_id"])
            allowed_ids = {vid for vid, openq in open_map.items() if openq > 0}

            equip_ids = set(
                vid
                for (vid,) in (
                    db.session.query(Variant.id)
                    .join(Product, Variant.product_id == Product.id)
                    .filter(
                        (Product.name.ilike("%matériel%seul%"))
                        | (Product.name.ilike("%materiel%seul%"))
                        | (Product.name.ilike("%Matériel seul%"))
                        | (Product.name.ilike("%Materiel seul%"))
                    )
                    .all()
                )
            )

            final_ids = list(allowed_ids | equip_ids)
            if final_ids:
                base_q = base_q.filter(Variant.id.in_(final_ids))

        rows = base_q.all()
        return render_template("movement_wizard.html", step=2, clients=clients, rows=rows, wiz=wiz)

    # ---------- ÉTAPE 3 : écran intermédiaire (facultatif) ----------
    if step == 3:
        if request.method == "POST":
            return redirect(url_for("movement_wizard", step=4))
        return redirect(url_for("movement_wizard", step=4))

    # ---------- ÉTAPE 4 : saisie quantités/prix/consignes + enregistrement ----------
    if step == 4:
        if request.method == "POST":
            if (wiz.get("client_id") is None) or (wiz.get("type") is None):
                flash("Informations incomplètes.", "warning")
                return redirect(url_for("movement_wizard", step=1))

            # Date finale
            if wiz.get("date"):
                try:
                    y, m_, d2 = [int(x) for x in wiz["date"].split("-")]
                    created_at = datetime.combine(date(y, m_, d2), time(hour=12))
                except Exception:
                    created_at = U.now_utc()
            else:
                created_at = U.now_utc()

            variant_ids = request.form.getlist("variant_id")
            qtys = request.form.getlist("qty")
            unit_prices = request.form.getlist("unit_price_ttc")
            deposits = request.form.getlist("deposit_per_keg")
            notes = request.form.get("notes") or None

            # Matériel prêté/repris → notes auto-structurées
            t = request.form.get("eq_tireuse", type=int)
            c2 = request.form.get("eq_co2", type=int)
            cpt = request.form.get("eq_comptoir", type=int)
            ton = request.form.get("eq_tonnelle", type=int)
            equip_parts = []
            if t:
                equip_parts.append(f"tireuse={t}")
            if c2:
                equip_parts.append(f"co2={c2}")
            if cpt:
                equip_parts.append(f"comptoir={cpt}")
            if ton:
                equip_parts.append(f"tonnelle={ton}")
            notes = (";".join(equip_parts) + (";" + notes if notes else "")) or None

            client_id = int(wiz["client_id"])
            mtype = wiz["type"]

            # Contrôle des retours vs enjeu
            def _open_kegs_by_variant(client_id: int):
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

            violations = []
            open_map = _open_kegs_by_variant(client_id) if mtype == "IN" else {}

            for i, vid in enumerate(variant_ids):
                try:
                    vid_int = int(vid)
                except Exception:
                    continue

                try:
                    qty_int = int(qtys[i])
                except Exception:
                    qty_int = 0

                up = None
                if i < len(unit_prices) and unit_prices[i] not in ("", None):
                    try:
                        up = float(unit_prices[i])
                    except Exception:
                        up = None

                dep = None
                if i < len(deposits) and deposits[i] not in ("", None):
                    try:
                        dep = float(deposits[i])
                    except Exception:
                        dep = None

                v = Variant.query.get(vid_int)
                if not v:
                    continue

                pname = (v.product.name if v and v.product else "") or ""
                is_equipment_only = ("matériel" in pname.lower() or "materiel" in pname.lower()) and ("seul" in pname.lower())

                if is_equipment_only:
                    # Le matériel seul ne porte pas de quantité de fûts ni de prix
                    qty_int = 0
                    up = 0.0
                    dep = 0.0
                else:
                    if up is None and (v.price_ttc is not None):
                        up = v.price_ttc
                    if dep is None:
                        # Ecocup = 1 €, sinon 30 €
                        dep = U.default_deposit_for_product(v.product)

                if mtype == "IN":
                    open_q = int(open_map.get(vid_int, 0))
                    if open_q <= 0 or qty_int > open_q:
                        label = f"{v.product.name} — {v.size_l} L"
                        violations.append((label, open_q))
                        continue

                mv = Movement(
                    client_id=client_id,
                    variant_id=vid_int,
                    type=mtype,
                    qty=qty_int,
                    unit_price_ttc=up,
                    deposit_per_keg=dep,
                    notes=notes,
                    created_at=created_at,
                )
                db.session.add(mv)

                # MAJ inventaire bar (OUT / FULL)
                inv = U.get_or_create_inventory(vid_int)
                if mtype == "OUT":
                    inv.qty = (inv.qty or 0) - qty_int
                elif mtype == "FULL":
                    inv.qty = (inv.qty or 0) + qty_int

            if violations:
                text = "Certains retours dépassent l’enjeu autorisé : " + ", ".join(f"{lab} (max {q})" for lab, q in violations)
                flash(text, "warning")
                return redirect(url_for("movement_wizard", step=2))

            db.session.commit()
            flash("Saisie enregistrée.", "success")
            session.pop("wiz", None)
            return redirect(url_for("client_detail", client_id=client_id))

        # GET -> afficher le formulaire de l’étape 4
        return render_template("movement_wizard.html", step=4, wiz=wiz)

    # Fallback sécurité
    return redirect(url_for("movement_wizard", step=1))


# -----------------------------------------------------------------------------
# Erreurs
# -----------------------------------------------------------------------------
@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="Page introuvable"), 404


@app.errorhandler(500)
def server_error(e):
    # Si tu veux logguer e, tu peux rajouter print(e) ici
    return render_template("error.html", code=500, message="Une erreur est survenue."), 500


# -----------------------------------------------------------------------------
# Lancement local
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
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

    # Consignes Ecocup vs Fûts
    dep_cup_eur, cup_qty, dep_keg_eur, keg_qty = U.compute_deposits_split(client_id)

    return render_template(
        "client_detail.html",
        client=c,
        c=c,  # <-- important pour les templates qui utilisent {{ c.* }}
        view=view,
        movements=movements,
        beer_billed_cum=view["beer_eur"],
        deposit_in_play=view.get("deposit_eur", 0.0),
        equipment_totals=view.get("equipment", {}),
        liters_out_cum=view.get("liters_out_cum", 0.0),
        litres_out_cum=view.get("liters_out_cum", 0.0),
        deposit_cup_eur=dep_cup_eur,
        deposit_keg_eur=dep_keg_eur,
        cup_qty_in_play=cup_qty,
        keg_qty_in_play=keg_qty,
    )
@app.route("/client/<int:client_id>/confirm-delete", methods=["GET"])
def client_confirm_delete(client_id):
    c = Client.query.get_or_404(client_id)
    mv_count = Movement.query.filter_by(client_id=client_id).count()

    # Balance encore “en jeu”
    balance_map = _open_qty_by_variant(client_id)
    open_details, open_total = [], 0
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
            c=c,  # <-- important
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
            <p class="mb-2">Cette action est <strong>irréversible</strong>.</p>

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
              <div class="alert alert-info">
                Consignes en jeu : <strong>{{ deposit_in_play | eur }}</strong>.
              </div>
            {% endif %}

            {% if equipment_in_play and equipment_in_play > 0 %}
              <div class="alert alert-secondary">
                Matériel en jeu : <strong>{{ equipment_in_play }}</strong>.
              </div>
            {% endif %}

            {% if blocked %}
              <div class="alert alert-danger">
                Suppression <strong>bloquée</strong> : le client n’est pas à zéro partout.
              </div>
              <a class="btn btn-outline-secondary" href="{{ url_for('client_detail', client_id=client.id) }}">Retour à la fiche</a>
            {% else %}
              <form method="post" action="{{ url_for('client_delete', client_id=client.id) }}" class="d-flex gap-2 mt-3">
                <a class="btn btn-outline-secondary" href="{{ url_for('client_detail', client_id=client.id) }}">Annuler</a>
                <button class="btn btn-danger" type="submit"
                        onclick="return confirm('Confirmer la suppression définitive ? Action irréversible.');">
                  Supprimer définitivement
                </button>
              </form>
            {% endif %}
          </div>
        </div>
        {% endblock %}
        """
        return render_template_string(
            html,
            client=c,
            c=c,  # <-- important
            mv_count=mv_count,
            open_total=open_total,
            open_details=open_details,
            deposit_in_play=deposit_in_play,
            equipment_in_play=equipment_in_play,
            blocked=blocked,
        )

