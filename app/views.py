from flask import Blueprint, render_template, request, redirect, url_for, flash
from . import db
from .models import Client

bp = Blueprint("main", __name__)

@bp.route("/")
def index():
    # Simple home page with client count
    total = db.session.scalar(db.select(db.func.count(Client.id)))
    clients = db.session.execute(db.select(Client).order_by(Client.name.asc())).scalars().all()
    return render_template("index.html", total_clients=total or 0, clients=clients)

@bp.route("/clients", methods=["GET", "POST"])
def clients():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        city = request.form.get("city", "").strip() or None
        if not name:
            flash("Le nom du client est obligatoire.", "danger")
        else:
            c = Client(name=name, city=city)
            db.session.add(c)
            db.session.commit()
            flash("Client créé.", "success")
            return redirect(url_for("main.clients"))
    items = db.session.execute(db.select(Client).order_by(Client.name.asc())).scalars().all()
    return render_template("clients.html", items=items)

@bp.post("/clients/<int:client_id>/delete")
def client_delete(client_id: int):
    c = db.session.get(Client, client_id)
    if not c:
        flash("Client introuvable.", "warning")
        return redirect(url_for("main.clients"))
    db.session.delete(c)
    db.session.commit()
    flash("Client supprimé.", "success")
    return redirect(url_for("main.clients"))
