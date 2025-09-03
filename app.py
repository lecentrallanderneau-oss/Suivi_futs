import os
from datetime import datetime, date, time
from flask import Flask, render_template, request, redirect, url_for, flash, session, Response
from sqlalchemy import func

from models import db, Client, Product, Variant, Movement, ReorderRule
from seed import seed_if_empty
import utils as U


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "dev")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///data.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)

    with app.app_context():
        db.create_all()
        seed_if_empty()

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

    @app.route("/catalog")
    def catalog():
        variants = (
            db.sess
