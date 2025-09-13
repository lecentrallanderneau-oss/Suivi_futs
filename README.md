# Suivi Fûts — Starter simple (Flask + SQLAlchemy)

Starter minimal sans migrations : **`db.create_all()`** crée les tables automatiquement au démarrage.
Parfait pour Render et un déploiement rapide.

## Déploiement local (optionnel)
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export FLASK_APP=app:app
flask run
```

Par défaut, ça utilise SQLite (`app.db`).  
Pour Postgres, définissez `DATABASE_URL` (Render le fournit automatiquement).

## Déploiement Render
- Créez un nouveau service **Web** (Build Command: `pip install -r requirements.txt`)
- Start Command: **`gunicorn app:app`**
- Ajoutez la variable d'environnement **DATABASE_URL** si ce n'est pas auto (Postgres Render).
- Déployez.

## Fonctionnalités
- Liste des clients, ajout/suppression
- Fiche client avec historiques
- Saisie d'une livraison/reprise (fûts) + matériel dans **une seule page**
- Totaux automatiques (Δ fûts, consignes à 30 €/fût, matériel en prêt)

## Important
C'est un squelette volontairement simple : pas de modèles produits/matériel ni migrations.
On ajoutera ces éléments ensuite, de façon stable.
