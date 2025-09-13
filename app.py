import os
from flask import Flask, render_template
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

# ---------- Base de données ----------
db_url = os.environ.get("DATABASE_URL", "sqlite:///local.db")

# Render fournit parfois 'postgres://', on force le driver psycopg (v3)
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
elif db_url.startswith("postgresql://") and "+psycopg" not in db_url:
    db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# ---------- Modèles ----------
class Client(db.Model):
    __tablename__ = "clients"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)

# ---------- Routes ----------
@app.route("/")
def index():
    clients = Client.query.order_by(Client.name.asc()).all()
    return render_template("index.html", clients=clients)

@app.route("/clients/<int:client_id>")
def client_detail(client_id):
    client = Client.query.get_or_404(client_id)
    return render_template("client_detail.html", client=client)

# ---------- Lancement local ----------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        # petit seed pour tester en local si vide
        if Client.query.count() == 0:
            db.session.add_all([Client(name="Client A"), Client(name="Client B")])
            db.session.commit()
    app.run(host="0.0.0.0", port=5000, debug=True)
