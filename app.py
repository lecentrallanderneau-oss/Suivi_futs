import os
from flask import Flask, render_template
from flask_sqlalchemy import SQLAlchemy

# -------------------------------------------------
# Initialisation de l’application Flask
# -------------------------------------------------
app = Flask(__name__)

# -------------------------------------------------
# Configuration base de données
# -------------------------------------------------
db_url = os.environ.get("DATABASE_URL", "sqlite:///local.db")

# Forcer l’utilisation du driver psycopg (v3) même si Render fournit postgres://
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
elif db_url.startswith("postgresql://") and "+psycopg" not in db_url:
    db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# -------------------------------------------------
# Initialisation SQLAlchemy
# -------------------------------------------------
db = SQLAlchemy(app)


# -------------------------------------------------
# Exemple de modèle (tu pourras en rajouter d’autres)
# -------------------------------------------------
class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)


# -------------------------------------------------
# Routes
# -------------------------------------------------
@app.route("/")
def index():
    clients = Client.query.all()
    return render_template("index.html", clients=clients)


@app.route("/clients/<int:client_id>")
def client_detail(client_id):
    client = Client.query.get_or_404(client_id)
    return render_template("client_detail.html", client=client)


# -------------------------------------------------
# Lancement local
# -------------------------------------------------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()  # Créé les tables si elles n’existent pas
    app.run(host="0.0.0.0", port=5000, debug=True)
