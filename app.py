import os
from decimal import Decimal
from datetime import date, datetime
from flask import Flask, render_template, request, redirect, url_for, flash
from dotenv import load_dotenv
from models import db, Client, KegMove, EquipMove

load_dotenv()

def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")

    # DB config: use Render's DATABASE_URL if present, else SQLite file
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        database_url = "sqlite:///app.db"
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    # Create tables if they don't exist (simple mode, no migrations)
    with app.app_context():
        db.create_all()

    # Jinja filter for euros (simple, optional)
    def eur(v):
        try:
            cents = int(v)
        except Exception:
            return "0,00 €"
        euros = Decimal(cents) / Decimal(100)
        return f"{euros:,.2f} €".replace(",", "X").replace(".", ",").replace("X", " ")
    app.jinja_env.filters["eur"] = eur

    # --------- ROUTES ----------

    @app.get("/")
    def index():
        # Cards = clients with quick totals
        clients = Client.query.order_by(Client.name.asc()).all()

        # Global totals
        total_kegs = sum(c.current_kegs() for c in clients)

        # Equipment totals (aggregated by name across all clients)
        equip_totals = {}
        for c in clients:
            for name, qty in c.current_equip_summary().items():
                equip_totals[name] = equip_totals.get(name, 0) + qty

        totals = {
            "kegs": total_kegs,
            "equip": equip_totals,
            "deposit_cents": total_kegs * 3000,  # 30 € per keg
        }

        return render_template("index.html", cards=clients, totals=totals, alerts=[])

    # --- Clients ---
    @app.get("/clients")
    def clients():
        items = Client.query.order_by(Client.name.asc()).all()
        return render_template("clients.html", clients=items)

    @app.post("/clients/new")
    def client_new_post():
        name = (request.form.get("name") or "").strip()
        city = (request.form.get("city") or "").strip() or None
        if not name:
            flash("Nom du client requis.", "danger")
            return redirect(url_for("clients"))
        if Client.query.filter_by(name=name).first():
            flash("Ce client existe déjà.", "warning")
            return redirect(url_for("clients"))
        c = Client(name=name, city=city)
        db.session.add(c)
        db.session.commit()
        flash("Client créé.", "success")
        return redirect(url_for("clients"))

    @app.post("/clients/<int:client_id>/delete")
    def client_delete(client_id: int):
        c = Client.query.get_or_404(client_id)
        db.session.delete(c)
        db.session.commit()
        flash("Client supprimé.", "success")
        return redirect(url_for("clients"))

    @app.get("/client/<int:client_id>")
    def client_detail(client_id: int):
        c = Client.query.get_or_404(client_id)
        # History
        keg_moves = KegMove.query.filter_by(client_id=client_id).order_by(KegMove.created_at.desc()).all()
        equip_moves = EquipMove.query.filter_by(client_id=client_id).order_by(EquipMove.created_at.desc()).all()

        # Running totals
        current_kegs = c.current_kegs()
        equip_summary = c.current_equip_summary()

        return render_template(
            "client_detail.html",
            c=c,
            keg_moves=keg_moves,
            equip_moves=equip_moves,
            current_kegs=current_kegs,
            equip_summary=equip_summary,
        )

    # --- Movement form (beer + equipment in one shot) ---
    @app.get("/movement/new")
    def movement_new():
        client_id = request.args.get("client_id", type=int)
        c = Client.query.get(client_id) if client_id else None
        return render_template("movement_new.html", c=c)

    @app.post("/movement/new")
    def movement_new_post():
        client_id = request.form.get("client_id", type=int)
        c = Client.query.get_or_404(client_id)

        # Beer / kegs
        date_str = request.form.get("date") or ""
        when = date.fromisoformat(date_str) if date_str else date.today()
        product_name = (request.form.get("product_name") or "").strip() or None
        delivered_full = request.form.get("delivered_full", type=int) or 0
        picked_empty = request.form.get("picked_empty", type=int) or 0
        picked_def = request.form.get("picked_defective", type=int) or 0

        km = KegMove(
            client_id=c.id,
            date=when,
            product_name=product_name,
            delivered_full=delivered_full,
            picked_empty=picked_empty,
            picked_defective=picked_def,
        )
        km.recalc()

        db.session.add(km)

        # Equipment (optional, free text)
        equip_name = (request.form.get("equipment_name") or "").strip()
        qty_out = request.form.get("equip_out", type=int) or 0
        qty_in = request.form.get("equip_in", type=int) or 0
        if equip_name and (qty_out or qty_in):
            em = EquipMove(
                client_id=c.id,
                date=when,
                equipment_name=equip_name,
                qty_out=qty_out,
                qty_in=qty_in,
            )
            em.recalc()
            db.session.add(em)

        db.session.commit()
        flash("Mouvement enregistré.", "success")
        return redirect(url_for("client_detail", client_id=c.id))

    # --- Delete lines ---
    @app.post("/keg/delete/<int:move_id>")
    def keg_delete(move_id: int):
        m = KegMove.query.get_or_404(move_id)
        cid = m.client_id
        db.session.delete(m)
        db.session.commit()
        flash("Ligne fûts supprimée.", "success")
        return redirect(url_for("client_detail", client_id=cid))

    @app.post("/equip/delete/<int:move_id>")
    def equip_delete(move_id: int):
        m = EquipMove.query.get_or_404(move_id)
        cid = m.client_id
        db.session.delete(m)
        db.session.commit()
        flash("Ligne matériel supprimée.", "success")
        return redirect(url_for("client_detail", client_id=cid))

    # --- Simple catalog placeholder ---
    @app.get("/catalog")
    def catalog():
        return render_template("catalog.html")

    return app

app = create_app()
