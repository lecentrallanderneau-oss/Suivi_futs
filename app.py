from __future__ import annotations
import os
import logging
import math
from typing import List, Dict, Any, Optional

from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from sqlalchemy import text, inspect
from werkzeug.middleware.proxy_fix import ProxyFix

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
def _build_db_uri() -> str:
    uri = os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URI") or "sqlite:///app.db"
    if uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)
    return uri

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", os.urandom(24).hex())
app.config["SQLALCHEMY_DATABASE_URI"] = _build_db_uri()
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Render/Proxy
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Logs
if os.getenv("FLASK_ENV") != "development":
    logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Routes de base
# -----------------------------------------------------------------------------
@app.route("/health")
def health():
    return "OK", 200

@app.route("/")
def index():
    # Redirige vers le stock (accueil par défaut)
    return redirect(url_for("stock"))

# -----------------------------------------------------------------------------
# --- ROUTE CATALOG ROBUSTE ---
# -----------------------------------------------------------------------------
@app.route("/catalog")
def catalog():
    try:
        engine = db.session.get_bind()
        insp = inspect(engine)

        # 1) Trouver une table "catalogue"
        table_priority = ['products', 'product', 'catalog', 'catalogue', 'produits', 'items', 'articles']
        existing_tables = set(insp.get_table_names())
        table_name = next((t for t in table_priority if t in existing_tables), None)

        if not table_name:
            logging.error(f"[CATALOG] Aucune table trouvée parmi {table_priority}. Tables existantes: {sorted(existing_tables)}")
            return render_template('catalog.html',
                                   error="Aucune table catalogue détectée en base.",
                                   columns=[],
                                   rows=[],
                                   page=1,
                                   pages=1,
                                   total=0,
                                   table_hint=sorted(existing_tables)), 200

        # 2) Pagination et tri simples
        page = max(int(request.args.get('page', 1) or 1), 1)
        per_page = max(min(int(request.args.get('per_page', 50) or 50), 200), 1)
        sort = request.args.get('sort')
        order = request.args.get('order', 'asc').lower()
        order = 'desc' if order == 'desc' else 'asc'

        # 3) Colonnes de la table
        cols = [c['name'] for c in insp.get_columns(table_name)]
        if not cols:
            return render_template('catalog.html',
                                   error=f"La table '{table_name}' n'a aucune colonne.",
                                   columns=[],
                                   rows=[],
                                   page=1,
                                   pages=1,
                                   total=0,
                                   table_hint=sorted(existing_tables)), 200

        # 4) Construire les requêtes COUNT + SELECT
        count_sql = text(f"SELECT COUNT(*) AS cnt FROM {table_name}")
        total = db.session.execute(count_sql).scalar() or 0
        pages = max(math.ceil(total / per_page), 1)
        page = min(page, pages)

        # tri uniquement si la colonne existe
        sort_clause = ""
        if sort in cols:
            sort_clause = f" ORDER BY {sort} {order}"

        offset = (page - 1) * per_page
        select_sql = text(f"SELECT * FROM {table_name}{sort_clause} LIMIT :limit OFFSET :offset")
        result = db.session.execute(select_sql, {"limit": per_page, "offset": offset})

        # 5) Convertir en dicts pour le template
        rows = [dict(r._mapping) for r in result]  # SQLAlchemy 2.x

        return render_template('catalog.html',
                               error=None,
                               columns=cols,
                               rows=rows,
                               page=page,
                               pages=pages,
                               total=total,
                               sort=sort,
                               order=order,
                               table_name=table_name,
                               table_hint=None), 200

    except Exception as e:
        logging.exception("[CATALOG] Exception sur /catalog")
        # On retourne le template avec un message clair, pas de 500 brut
        return render_template('catalog.html',
                               error=f"Une erreur est survenue lors du chargement du catalogue : {e}",
                               columns=[],
                               rows=[],
                               page=1,
                               pages=1,
                               total=0,
                               table_hint=None), 200
# --- FIN ROUTE CATALOG ---

# -----------------------------------------------------------------------------
# STOCK — on ne modifie pas ta logique, tu gardes ton implémentation
# -----------------------------------------------------------------------------
@app.route("/stock")
def stock():
    try:
        return render_template("stock.html")
    except Exception:
        return "<h1>Stock</h1><p>Template stock.html manquant.</p>", 200

# -----------------------------------------------------------------------------
# Handlers erreurs
# -----------------------------------------------------------------------------
@app.errorhandler(404)
def not_found(e):
    try:
        return render_template("404.html"), 404
    except Exception:
        return "404 Not Found", 404

@app.errorhandler(500)
def internal_error(e):
    logger.exception("Erreur 500")
    try:
        return render_template("500.html"), 500
    except Exception:
        return "500 Internal Server Error", 500

# -----------------------------------------------------------------------------
# Lancement local
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG") == "1")
