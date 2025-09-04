# app.py — Flask 3 + SQLAlchemy 3, compatible Render/Gunicorn (web: gunicorn app:app)
from __future__ import annotations
import os
import math
import logging
from typing import List, Dict, Any, Optional

from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from sqlalchemy import text, inspect
from werkzeug.middleware.proxy_fix import ProxyFix

# -----------------------------------------------------------------------------
# Config de base
# -----------------------------------------------------------------------------
def _build_db_uri() -> str:
    uri = os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URI") or "sqlite:///app.db"
    # Fix Heroku-style
    if uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)
    return uri

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", os.urandom(24).hex())
app.config["SQLALCHEMY_DATABASE_URI"] = _build_db_uri()
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Render est derrière un proxy → corrige scheme/IP
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

db = SQLAlchemy(app)
migrate = Migrate(app, db)

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
    # On t’emmène sur la page qui marche déjà chez toi
    return redirect(url_for("stock"))

# -----------------------------------------------------------------------------
# /catalog — version robuste par introspection SQL (aucune dépendance modèle)
# -----------------------------------------------------------------------------
@app.route("/catalog")
def catalog():
    """
    Tri: ?sort=col&order=asc|desc
    Pagination: ?page=1&per_page=50
    """
    try:
        engine = db.session.get_bind()
        insp = inspect(engine)

        # Tables candidates par ordre de priorité
        table_priority: List[str] = [
            "products", "product", "catalog", "catalogue",
            "produits", "items", "articles",
        ]
        existing = set(insp.get_table_names())
        table_name: Optional[str] = next((t for t in table_priority if t in existing), None)

        if not table_name:
            logging.error(f"[CATALOG] Aucune table parmi {table_priority}. Existantes: {sorted(existing)}")
            return render_template(
                "catalog.html",
                error="Aucune table catalogue détectée en base.",
                columns=[], rows=[], page=1, pages=1, total=0,
                sort=None, order="asc", table_name=None,
                table_hint=sorted(existing),
            ), 200

        cols: List[str] = [c["name"] for c in insp.get_columns(table_name)]
        if not cols:
            return render_template(
                "catalog.html",
                error=f"La table '{table_name}' n'a aucune colonne.",
                columns=[], rows=[], page=1, pages=1, total=0,
                sort=None, order="asc", table_name=table_name,
                table_hint=sorted(existing),
            ), 200

        # Pagination
        page = max(int(request.args.get("page", 1) or 1), 1)
        per_page = max(min(int(request.args.get("per_page", 50) or 50), 200), 1)

        # Tri
        sort = request.args.get("sort")
        order = request.args.get("order", "asc").lower()
        order = "desc" if order == "desc" else "asc"
        sort_clause = f" ORDER BY {sort} {order}" if sort in cols else ""

        # Count
        total = db.session.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar() or 0
        pages = max(math.ceil(total / per_page), 1)
        if page > pages:
            page = pages

        # Page
        offset = (page - 1) * per_page
        sql = text(f"SELECT * FROM {table_name}{sort_clause} LIMIT :limit OFFSET :offset")
        result = db.session.execute(sql, {"limit": per_page, "offset": offset})
        rows: List[Dict[str, Any]] = [dict(r._mapping) for r in result]  # SQLAlchemy 2.x

        return render_template(
            "catalog.html",
            error=None, columns=cols, rows=rows,
            page=page, pages=pages, total=total,
            sort=sort, order=order, table_name=table_name,
            table_hint=None,
        ), 200

    except Exception as e:
        logging.exception("[CATALOG] Exception sur /catalog")
        return render_template(
            "catalog.html",
            error=f"Une erreur est survenue lors du chargement du catalogue : {e}",
            columns=[], rows=[], page=1, pages=1, total=0,
            sort=None, order="asc", table_name=None, table_hint=None,
        ), 200

# -----------------------------------------------------------------------------
# /stock — on ne modifie PAS ta logique; fallback si besoin
# -----------------------------------------------------------------------------
@app.route("/stock")
def stock():
    try:
        return render_template("stock.html")
    except Exception:
        return "<h1>Stock</h1><p>Le template <code>templates/stock.html</code> est requis.</p>", 200

# -----------------------------------------------------------------------------
# Handlers d’erreurs (utilisent tes templates)
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
# Exécution locale (Render utilise `web: gunicorn app:app`)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG") == "1")
