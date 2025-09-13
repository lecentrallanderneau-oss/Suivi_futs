from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Client(db.Model):
    __tablename__ = "clients"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    city = db.Column(db.String(120), nullable=True)

    keg_moves = db.relationship("KegMove", backref="client", cascade="all, delete-orphan")
    equip_moves = db.relationship("EquipMove", backref="client", cascade="all, delete-orphan")

    def current_kegs(self) -> int:
        return sum((m.delta_kegs or 0) for m in self.keg_moves)

    def current_equip_summary(self) -> dict[str, int]:
        agg: dict[str, int] = {}
        for m in self.equip_moves:
            key = (m.equipment_name or "").strip() or "Matériel"
            agg[key] = agg.get(key, 0) + (m.delta_qty or 0)
        return {k: v for k, v in agg.items() if v != 0}

class KegMove(db.Model):
    __tablename__ = "keg_moves"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # FK
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)

    # Saisie
    date = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    product_name = db.Column(db.String(120), nullable=True)  # libre pour aller vite
    delivered_full = db.Column(db.Integer, nullable=False, default=0)
    picked_empty = db.Column(db.Integer, nullable=False, default=0)
    picked_defective = db.Column(db.Integer, nullable=False, default=0)

    # Calculs
    delta_kegs = db.Column(db.Integer, nullable=False, default=0)  # livré - repris
    deposit_delta_cents = db.Column(db.Integer, nullable=False, default=0)  # 30€ * delta

    def recalc(self, deposit_eur_per_keg: int = 30):
        self.delta_kegs = int(self.delivered_full) - (int(self.picked_empty) + int(self.picked_defective))
        self.deposit_delta_cents = self.delta_kegs * int(deposit_eur_per_keg) * 100

class EquipMove(db.Model):
    __tablename__ = "equip_moves"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)

    date = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    equipment_name = db.Column(db.String(120), nullable=False)  # libre
    qty_out = db.Column(db.Integer, nullable=False, default=0)
    qty_in = db.Column(db.Integer, nullable=False, default=0)
    delta_qty = db.Column(db.Integer, nullable=False, default=0)  # out - in

    def recalc(self):
        self.delta_qty = int(self.qty_out) - int(self.qty_in)
