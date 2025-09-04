# app.py — version complète, compatible Render/Gunicorn, Flask 3 + SQLAlchemy 3
# Objectifs :
# - Fournir une instance globale `app` pour gunicorn (app:app)
# - Initialiser SQLAlchemy et Migrate proprement
# - Laisser intactes tes autres routes (dont Stock) si tu les ajoutes en bas
# - Offrir une route /catalog robuste par introspection SQL
# - Ne PAS planter si une table/colonne varie : on affiche ce qui existe

from __future__ import annotations
import os
import math
import logging
from typing import List, Dict, Any, Optional

from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from sqlalchemy import text, inspect
from werkzeug.middleware.proxy_fix import ProxyFix

# -----------------------------------------------------------------------------
# Configuration de base
# -----------------------------------------------------------------------------
def _build_db_uri() -> str:
    # Priorité à DATABASE_URL (Render, Heroku, etc.)
    uri = os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URI") or "sqlite:///app.db"
    # Correction Heroku style
    if uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)
    return uri

app = Flask(__name__)
# Sécurité minimale : clé aléatoire si non fournie
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", os.urandom(24).hex())
app.config["SQLALCHEMY_DATABASE_URI"] = _build_db_uri()
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# En prod derrière un proxy (Render), corrige REMOTE_ADDR / scheme
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

# DB & migrations
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Logs sobres en prod
if os.getenv("FLASK_ENV") != "development":
    logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Routes génériques utiles
# -----------------------------------------------------------------------------
@app.route("/health")
def health() -> tuple[str, int]:
    return "OK", 200

@app.route("/")
def index():
    # Page d’accueil minimaliste — garde la compat avec tes anciennes routes/templates
    try:
        return render_template("index.html")
    except Exception:
        return (
            "<h1>Appli bière</h1>"
            "<p>Routes utiles :</p>"
            "<ul>"
            "<li><a href='/catalog'>/catalog</a></li>"
            "<li><a href='/stock'>/stock</a> (si définie)</li>"
            "</ul>",
            200,
        )

# -----------------------------------------------------------------------------
# Route CATALOG robuste (introspection SQL, sans dépendre d’un modèle)
# -----------------------------------------------------------------------------
@app.route("/catalog")
def catalog():
    """
    Lit une table 'catalogue/produits/products/...' si elle existe et affiche
    les colonnes présentes (sans planter si le schéma varie).
    Tri: ?sort=col&order=asc|desc
    Pagination: ?page=1&per_page=50
    """
    try:
        engine = db.session.get_bind()
        insp = inspect(engine)

        # Tables candidates par ordre de priorité
        table_priority: List[str] = [
            "products",
            "product",
            "catalog",
            "catalogue",
            "produits",
            "items",
            "articles",
        ]
        existing = set(insp.get_table_names())
        table_name: Optional[str] = next((t for t in table_priority if t in existing), None)

        if not table_name:
            logger.error("[CATALOG] Aucune table trouvée parmi %s. Existantes: %s",
                         table_priority, sorted(existing))
            return render_template(
                "catalog.html",
                error="Aucune table catalogue détectée en base.",
                columns=[],
                rows=[],
                page=1,
                pages=1,
                total=0,
                sort=None,
                order="asc",
                table_name=None,
                table_hint=sorted(existing),
            ), 200

        # Colonnes
        cols: List[str] = [c["name"] for c in insp.get_columns(table_name)]
        if not cols:
            return render_template(
                "catalog.html",
                error=f"La table '{table_name}' n'a aucune colonne.",
                columns=[],
                rows=[],
                page=1,
                pages=1,
                total=0,
                sort=None,
                order="asc",
                table_name=table_name,
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

        # Count total
        total = db.session.execute(text(f"SELECT COUNT(*) AS cnt FROM {table_name}")).scalar() or 0
        pages = max(math.ceil(total / per_page), 1)
        if page > pages:
            page = pages

        # Lecture page
        offset = (page - 1) * per_page
        sql = text(f"SELECT * FROM {table_name}{sort_clause} LIMIT :limit OFFSET :offset")
        result = db.session.execute(sql, {"limit": per_page, "offset": offset})
        rows: List[Dict[str, Any]] = [dict(r._mapping) for r in result]

        return render_template(
            "catalog.html",
            error=None,
            columns=cols,
            rows=rows,
            page=page,
            pages=pages,
            total=total,
            sort=sort,
            order=order,
            table_name=table_name,
            table_hint=None,
        ), 200

    except Exception as e:
        logger.exception("[CATALOG] Exception sur /catalog")
        # On renvoie 200 avec un message clair pour éviter l'écran 500 "Oups"
        return render_template(
            "catalog.html",
            error=f"Une erreur est survenue lors du chargement du catalogue : {e}",
            columns=[],
            rows=[],
            page=1,
            pages=1,
            total=0,
            sort=None,
            order="asc",
            table_name=None,
            table_hint=None,
        ), 200

# -----------------------------------------------------------------------------
# ⭐ IMPORTANT ⭐
# Tes autres routes existantes (dont /stock) doivent être déclarées APRÈS l'instance `app`.
# Si elles étaient auparavant plus haut dans le fichier, déplace-les simplement ici.
# Exemple (laisse ce bloc si tu as déjà une route /stock ailleurs) :
# -----------------------------------------------------------------------------
# @app.route("/stock")
# def stock():
#     # Ta logique actuelle qui fonctionne — ne pas modifier.
#     return render_template("stock.html", ...)

# -----------------------------------------------------------------------------
# Lancement local (optionnel). En prod, Render lance: `gunicorn app:app`
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG") == "1")
