"""
Créer le produit 'Ecocup' dans le catalogue s'il n'existe pas.
- Product.name = 'Ecocup'
- Variant: size_l = 0 (non pertinent), price_ttc = NULL (laisse libre)
"""

from models import db, Product, Variant
from app import app  # suppose que l'app Flask s'appelle 'app' dans app.py

def main():
    with app.app_context():
        prod = Product.query.filter(Product.name.ilike("ecocup")).first()
        if not prod:
            prod = Product(name="Ecocup")
            db.session.add(prod)
            db.session.flush()

        # une seule variante simple
        var = Variant.query.filter_by(product_id=prod.id).first()
        if not var:
            var = Variant(product_id=prod.id, size_l=0, price_ttc=None)
            db.session.add(var)

        db.session.commit()
        print("Ok: produit 'Ecocup' présent avec une variante.")

if __name__ == "__main__":
    main()
