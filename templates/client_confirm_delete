@app.route("/client/<int:client_id>/confirm-delete", methods=["GET"])
def client_confirm_delete(client_id):
    c = Client.query.get_or_404(client_id)

    # --- balances "en jeu" par variante ---
    def _open_qty_by_variant(client_id: int):
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

    mv_count = Movement.query.filter_by(client_id=client_id).count()
    balance_map = _open_qty_by_variant(client_id)

    open_details = []
    open_total = 0
    for vid, q in balance_map.items():
        if q > 0:
            v = Variant.query.get(vid)
            if not v:
                continue
            p = v.product
            open_total += q
            open_details.append({
                "product_name": p.name if p else "Produit",
                "size_l": v.size_l,
                "qty": q
            })

    view = U.summarize_client_detail(c)
    deposit_in_play = float(view.get("deposit_eur", 0.0) or 0.0)
    equipment_dict = view.get("equipment", {}) or {}
    try:
        equipment_in_play = sum(int(v or 0) for v in equipment_dict.values())
    except Exception:
        equipment_in_play = 0

    eps_eur = 0.01
    blocked = (open_total > 0) or (abs(deposit_in_play) > eps_eur) or (equipment_in_play > 0)

    # --- tente le template fichier ; sinon fallback inline ---
    try:
        return render_template(
            "client_confirm_delete.html",
            client=c,
            mv_count=mv_count,
            open_total=open_total,
            open_details=open_details,
            deposit_in_play=deposit_in_play,
            equipment_in_play=equipment_in_play,
            blocked=blocked,
        )
    except TemplateNotFound:
        html = """
        {% extends "base.html" %}
        {% block title %}Supprimer le client{% endblock %}
        {% block content %}
        <div class="card border-danger">
          <div class="card-header bg-danger text-white">⚠️ Suppression définitive du client</div>
          <div class="card-body">
            <h5 class="card-title mb-3">{{ client.name }}</h5>
            <p class="mb-2">Cette action est <strong>irréversible</strong>. Elle va :</p>
            <ul>
              <li>Supprimer <strong>tous les mouvements</strong> liés à ce client ({{ mv_count }} enregistrements).</li>
              <li>Rétablir l’inventaire bar correspondant aux mouvements <em>Livraison (OUT)</em> et <em>Appro (FULL)</em>.</li>
              <li>Supprimer la fiche client.</li>
            </ul>

            {% if open_total and open_total > 0 %}
              <div class="alert alert-warning">
                Il reste <strong>{{ open_total }}</strong> article(s) en jeu chez ce client :
                <ul class="mb-0">
                  {% for it in open_details %}
                    <li>{{ it.product_name }}{% if it.size_l %} — {{ it.size_l }} L{% endif %} : {{ it.qty }}</li>
                  {% endfor %}
                </ul>
              </div>
            {% endif %}

            {% if deposit_in_play and (deposit_in_play | float | abs) > 0.01 %}
              <div class="alert alert-info">
                Consignes/depôts estimés en jeu : <strong>{{ deposit_in_play | eur }}</strong>.
              </div>
            {% endif %}

            {% if equipment_in_play and equipment_in_play > 0 %}
              <div class="alert alert-secondary">
                Matériel en jeu : <strong>{{ equipment_in_play }}</strong>.
              </div>
            {% endif %}

            {% if blocked %}
              <div class="alert alert-danger">
                Suppression <strong>bloquée</strong> : le client n’est pas à zéro partout.
                Merci d’enregistrer retours et régularisations (consignes/matériel) avant de supprimer.
              </div>
              <a class="btn btn-outline-secondary" href="{{ url_for('client_detail', client_id=client.id) }}">Retour à la fiche</a>
            {% else %}
              <form method="post" action="{{ url_for('client_delete', client_id=client.id) }}" class="d-flex gap-2 mt-3">
                <a class="btn btn-outline-secondary" href="{{ url_for('client_detail', client_id=client.id) }}">Annuler</a>
                <button class="btn btn-danger" type="submit"
                        onclick="return confirm('Confirmer la suppression définitive ? Action irréversible.');">
                  Supprimer définitivement
                </button>
              </form>
            {% endif %}
          </div>
        </div>
        {% endblock %}
        """
        return render_template_string(
            html,
            client=c,
            mv_count=mv_count,
            open_total=open_total,
            open_details=open_details,
            deposit_in_play=deposit_in_play,
            equipment_in_play=equipment_in_play,
            blocked=blocked,
        )
