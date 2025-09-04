# app.py  — Option B (WSGI = gunicorn app:app)
import os
from datetime import datetime, date, time

from flask import (
    Flask, render_template, request, redirect, url_for, flash, session, Response
)
from sqlalchemy import func, and_

from models import db, Client, Product, Variant, Movement, ReorderRule
from seed import seed_if_empty
import utils as U


# ---------- Data guard : crée / normalise le produit “Ecocup” générique ----------
def _ensure_ecocup_simple():
    """
    Si aucun produit 'Ecocup' générique n'existe (hors 'lavage/perdu/perte/wash/clean'),
    on le crée + 1 variante avec size_l=1 (NOT NULL). On normalise aussi toutes
    variantes existantes à size_l=1 si elles étaient NULL.
    """
    name_lc = func.lower(Product.name)
    is_cup = (name_lc.like("%ecocup%") | name_lc.like("%eco cup%") | name_lc.like("%gobelet%"))
    is_maintenance = (
        name_lc.like("%lavage%")
        | name_lc.like("%perdu%")
        | name_lc.like("%perte%")
        | name_lc.like("%wash%")
        | name_lc.like("%clean%")
    )

    # Produit ecocup "simple"
    p = Product.query.filter(is_cup, ~is_maintenance).first()
    if not p:
        p = Product(name="Ecocup")
        db.session.add(p)
        db.session.flush()  # obtenir p.id

    # Backfill: toutes ses variantes size_l=NULL -> 1, price_ttc=0.0 si NULL
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

    # S'il n'existe aucune variante valide, en créer une
    v_any = Variant.query.filter_by(product_id=p.id).first()
    if not v_any:
        v = Variant(product_id=p.id, size_l=1, price_ttc=0.0)
        db.session.add(v)

    db.session.commit()


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "dev")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///data.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)

    # ----------------- bootstrap DB -----------------
    with app.app_context():
        db.create_all()
        seed_if_empty()
        _ensure_ecocup_simple()  # ⚙️ garantit la présence d'“Ecocup” générique

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

    # ----------------- Helper : fûts “ouverts” par variante chez un client -----------------
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

    # ----------------- Pages -----------------
    @app.route("/")
    def index():
        clients = Client.query.order_by(Client.name.asc()).all()
        cards = [U.summarize_client_for_index(c) for c in clients]
        totals = U.summarize_totals(cards)
        alerts = U.compute_reorder_alerts()
        return render_template("index.html", cards=cards, totals=totals, alerts=alerts)

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

        return render_template(
            "client_detail.html",
            client=c,
            view=view,
            movements=movements,
            beer_billed_cum=view["beer_eur"],
            deposit_in_play=view["deposit_eur"],
            equipment_totals=view["equipment"],
            liters_out_cum=view.get("liters_out_cum", 0.0),
            litres_out_cum=view.get("liters_out_cum", 0.0),
        )

    # ---------- Filtre robuste pour masquer “ecocup lavage/perdu” ----------
    def _hide_ecocup_maintenance(query):
        """
        Masque les produits dont le nom contient (ecocup|eco cup|gobelet)
        ET (lavage|perdu|perte|wash|clean).
        """
        name_lc = func.lower(Product.name)
        is_cup = (
            name_lc.like("%ecocup%")
            | name_lc.like("%eco cup%")
            | name_lc.like("%gobelet%")
        )
        is_maintenance = (
            name_lc.like("%lavage%")
            | name_lc.like("%perdu%")
            | name_lc.like("%perte%")
            | name_lc.like("%wash%")
            | name_lc.like("%clean%")
        )
        return query.filter(~and_(is_cup, is_maintenance))

    @app.route("/catalog")
    def catalog():
        """
        Donne au template les deux formats possibles :
        - rows : liste de (Variant, Product)
        - variants : liste de Variant
        """
        pairs = (
            db.session.query(Variant, Product)
            .join(Product, Variant.product_id == Product.id)
            .order_by(Product.name, Variant.size_l)
        )
        pairs = _hide_ecocup_maintenance(pairs)
        rows = pairs.all()
        variants = [v for (v, _p) in rows]
        return render_template("catalog.html", rows=rows, variants=variants)

    # ------------- Saisie (wizard) -------------
    @app.route("/movement/new", methods=["GET"])
    def movement_new():
        """Redirection pratique; accepte un client_id pour pré-remplir."""
        client_id = request.args.get("client_id", type=int)
        if client_id:
            return redirect(url_for("movement_wizard", step=1, client_id=client_id))
        return redirect(url_for("movement_wizard", step=1))

    @app.route("/movement/wizard", methods=["GET", "POST"])
    def movement_wizard():
        # État du wizard en session
        if "wiz" not in session:
            session["wiz"] = {}
        wiz = session["wiz"]

        # Pré-remplissage client depuis l’URL ? (ex: depuis une fiche client)
        pre_client_id = request.args.get("client_id", type=int)
        if pre_client_id:
            wiz["client_id"] = pre_client_id
            session.modified = True

        # Forcer la remise à zéro du client si demandé
        if request.args.get("clear_client"):
            wiz.pop("client_id", None)
            session.modified = True

        # Étape courante (par défaut 1)
        try:
            step = int(request.args.get("step", 1))
        except Exception:
            step = 1

        # ---------- ÉTAPE 1 : type + date (et client si pas déjà fixé) ----------
        if step == 1:
            if request.method == "POST":
                if "client_id" not in wiz:
                    wiz["client_id"] = request.form.get("client_id", type=int)
                wiz["type"] = request.form.get("type")
                wiz["date"] = request.form.get("date")  # AAAA-MM-JJ (optionnel)
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
            # GET
            clients = Client.query.order_by(Client.name.asc()).all()
            prefill_client = Client.query.get(wiz.get("client_id")) if wiz.get("client_id") else None
            return render_template("movement_wizard.html", step=1, clients=clients, prefill_client=prefill_client, rows=[], wiz=wiz)

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

            # GET : liste de variantes (masque maintenance)
            clients = Client.query.order_by(Client.name.asc()).all()
            base_q = (
                db.session.query(Variant, Product)
                .join(Product, Variant.product_id == Product.id)
                .order_by(Product.name, Variant.size_l)
            )
            base_q = _hide_ecocup_maintenance(base_q)

            # Si c'est un retour (IN), limiter aux variantes réellement en jeu + "Matériel seul"
            if wiz.get("type") == "IN" and wiz.get("client_id"):
                open_map = _open_kegs_by_variant(wiz["client_id"])
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

            rows = base_q.all()
            prefill_client = Client.query.get(wiz.get("client_id")) if wiz.get("client_id") else None
            return render_template("movement_wizard.html", step=2, clients=clients, rows=rows, wiz=wiz, prefill_client=prefill_client)

        # ---------- ÉTAPE 3 : saut direct vers 4 ----------
        if step == 3:
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

                # Matériel prêté/repris → notes
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

                client_id = int(wiz["client_id"])
                mtype = wiz["type"]

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
                                label = f"{v.product.name} — {v.size_l} L" if v.size_l else f"{v.product.name}"
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
                    text = "Certains retours dépassent l’enjeu autorisé : " + ", ".join(
                        f"{lab} (max {q})" for lab, q in violations
                    )
                    flash(text, "warning")
                    return redirect(url_for("movement_wizard", step=2))

                db.session.commit()
                flash("Saisie enregistrée.", "success")
                session.pop("wiz", None)
                return redirect(url_for("client_detail", client_id=client_id))

            # GET -> afficher le formulaire de l’étape 4 (on charge les variantes sélectionnées)
            selected = []
            for vid in wiz.get("variant_ids", []):
                v = Variant.query.get(vid)
                if v:
                    selected.append((v, v.product))
            prefill_client = Client.query.get(wiz.get("client_id")) if wiz.get("client_id") else None
            return render_template("movement_wizard.html", step=4, wiz=wiz, selected=selected, prefill_client=prefill_client)

    # ---- Suppression mouvement ----
    @app.route("/movement/<int:movement_id>/confirm-delete", methods=["GET"])
    def movement_confirm_delete(movement_id):
        m = Movement.query.get_or_404(movement_id)
        return render_template("movement_confirm_delete.html", m=m)

    @app.route("/movement/<int:movement_id>/delete", methods=["POST"])
    def movement_delete(movement_id):
        m = Movement.query.get_or_404(movement_id)
        client_id = m.client_id

        # Rétablir l’inventaire
        if m.qty and m.variant_id:
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

        # Sortie principale (tuples)
        rows = U.get_stock_items()
        # Format alternatif (dicts) si le template s'attend à des attributs
        items = [
            {"variant": v, "product": p, "inv_qty": inv_qty, "min_qty": min_qty}
            for (v, p, inv_qty, min_qty) in rows
        ]
        alerts = U.compute_reorder_alerts()
        return render_template("stock.html", rows=rows, items=items, alerts=alerts)

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


# ⚠️ Option B: garder gunicorn app:app → on expose une variable module-level 'app'
app = create_app()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
