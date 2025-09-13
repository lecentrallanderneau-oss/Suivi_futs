from flask_sqlalchemy import SQLAlchemy
from datetime import date
from sqlalchemy import func

db = SQLAlchemy()

class Client(db.Model):
    __tablename__ = "clients"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    note = db.Column(db.Text, nullable=True)

    @property
    def city(self):
        return None  # pas de colonne en BDD → évite les erreurs si le template l’utilise


class KegMove(db.Model):
    __tablename__ = "keg_moves"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    mov_date = db.Column(db.Date, nullable=False, default=date.today)
    qty_out = db.Column(db.Integer, nullable=False, default=0)
    qty_in = db.Column(db.Integer, nullable=False, default=0)
    qty_defect = db.Column(db.Integer, nullable=False, default=0)
    client = db.relationship("Client", backref=db.backref("keg_moves", lazy="dynamic"))


class EquipMove(db.Model):
    __tablename__ = "equip_moves"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    mov_date = db.Column(db.Date, nullable=False, default=date.today)
    label = db.Column(db.String(160), nullable=False)
    qty_out = db.Column(db.Integer, nullable=False, default=0)
    qty_in = db.Column(db.Integer, nullable=False, default=0)
    client = db.relationship("Client", backref=db.backref("equip_moves", lazy="dynamic"))
