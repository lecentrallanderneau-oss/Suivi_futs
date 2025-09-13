# models.py
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


# ⚠️ Client : modèle minimal pour ne PAS casser ta structure existante.
# S'il existe déjà plus de colonnes dans ta base, ce n'est pas grave :
# SQLAlchemy ignorera les colonnes non mappées tant qu'on ne les utilise pas.
class Client(db.Model):
    __tablename__ = "clients"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    address = db.Column(db.String(255))          # optionnel
    phone = db.Column(db.String(50))             # optionnel
    email = db.Column(db.String(255))            # optionnel

    def __repr__(self) -> str:
        return f"<Client {self.id} {self.name!r}>"


class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    volume_l = db.Column(db.Integer, nullable=False, index=True, default=0)  # ex : 20, 22, 30
    price_cents = db.Column(db.Integer, nullable=False, default=0)           # prix TTC en centimes
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    __table_args__ = (
        db.UniqueConstraint("name", "volume_l", name="uq_product_name_volume"),
    )

    def __repr__(self) -> str:
        return f"<Product {self.name} {self.volume_l}L>"


class Movement(db.Model):
    """
    Mouvement de fûts chez un client :
      - type = 'delivery' (livraison) ou 'pickup' (reprise)
      - quantity = nombre de fûts
      - unit_deposit_cents = consigne par fût (si tu en mets une)
      - unit_price_cents = prix unitaire (optionnel si facture ailleurs)
    """
    __tablename__ = "movements"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)

    type = db.Column(db.String(20), nullable=False)  # 'delivery' | 'pickup'
    quantity = db.Column(db.Integer, nullable=False, default=0)

    unit_deposit_cents = db.Column(db.Integer, nullable=False, default=0)
    unit_price_cents = db.Column(db.Integer, nullable=False, default=0)

    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    client = db.relationship("Client", backref=db.backref("movements", lazy="dynamic"))
    product = db.relationship("Product")

    def __repr__(self) -> str:
        return f"<Movement {self.type} client={self.client_id} product={self.product_id} qty={self.quantity}>"
