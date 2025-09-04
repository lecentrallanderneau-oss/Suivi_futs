{% extends "base.html" %}
{% block title %}Supprimer le client{% endblock %}

{% block content %}
<div class="card border-danger">
  <div class="card-header bg-danger text-white">
    ⚠️ Suppression définitive du client
  </div>
  <div class="card-body">
    <h5 class="card-title mb-3">{{ client.name }}</h5>
    <p class="mb-2">Cette action est <strong>irréversible</strong>. Elle va :</p>
    <ul>
      <li>Supprimer <strong>tous les mouvements</strong> liés à ce client ({{ mv_count }} enregistrements).</li>
      <li>Rétablir automatiquement l’inventaire bar lié aux mouvements <em>Livraison (OUT)</em> et <em>Appro (FULL)</em>.</li>
      <li>Supprimer la fiche client.</li>
    </ul>

    {% if open_total and open_total > 0 %}
      <div class="alert alert-warning">
        Attention : ce client a encore <strong>{{ open_total }}</strong> fût(s)/référence(s) en jeu (non retournés).
      </div>
    {% endif %}

    {% if deposit_in_play and deposit_in_play != 0 %}
      <div class="alert alert-info">
        Consignes estimées en jeu : <strong>{{ deposit_in_play | eur }}</strong>.
      </div>
    {% endif %}

    <form method="post" action="{{ url_for('client_delete', client_id=client.id) }}" class="d-flex gap-2 mt-3">
      <a class="btn btn-outline-secondary" href="{{ url_for('client_detail', client_id=client.id) }}">Annuler</a>
      <button class="btn btn-danger" type="submit">Supprimer définitivement</button>
    </form>
  </div>
</div>
{% endblock %}
