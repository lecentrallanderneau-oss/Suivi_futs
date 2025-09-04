# --- ROUTE CATALOG ROBUSTE (remplace ton @app.route('/catalog')) ---
from flask import render_template, request, current_app
from sqlalchemy import text, inspect
import logging
import math

@app.route('/catalog')
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
