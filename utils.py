from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict
from types import SimpleNamespace

from sqlalchemy import func
from models import db, Client, Product, Variant, Movement, Inventory, ReorderRule

# --- Constantes ---
DEFAULT_KEG_DEPOSIT = 30.0         # consigne par fût
DEFAULT_ECOCUP_DEPOSIT = 1.0       # consigne par gobelet
MOV_TYPES = {"OUT", "IN", "DEFECT", "FULL"}  # FULL = retour plein


# --- Structures utilitaires ---
@dataclass
class Equipment:
    tireuse: int = 0
    co2: int = 0
    comptoir: int = 0
    tonnelle: int = 0
    ecocup: int = 0   # prêt de gobelets via notes (optionnel, inchangé)


def now_utc():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def parse_equipment(notes: Optional[str]) -> Equipment:
    if not notes:
        return Equipment()
    eq = Equipment()
    try:
        parts = [p.strip() for p in notes.split(";") if p.strip()]
        for p in parts:
            if "=" in p:
                k, v = p.split("=", 1)
                k = k.strip().lower()
                try:
                    val = int(v.strip())
                except Exception:
                    val = 0
                if k == "tireuse":
                    eq.tireuse = val
                elif k == "co2":
                    eq.co2 = val
                elif k == "comptoir":
                    eq.comptoir = val
                elif k == "tonnelle":
                    eq.tonnelle = val
                elif k == "ecocup":
                    eq.ecocup = val
    except Exception:
        pass
    return eq


def combine_equipment(dst: Equipment, src: Equipment, sign: int):
    dst.tireuse += sign * (src.tireuse or 0)
    dst.co2 += sign * (src.co2 or 0)
    dst.comptoir += sign * (src.comptoir or 0)
    dst.tonnelle += sign * (src.tonnelle or 0)
    dst.ecocup += sign * (src.ecocup or 0)


# --- Prix / Consigne effectifs ---
def effective_price(m: Movement, v: Variant) -> Optional[float]:
    # Prix prioritaire saisi sur le mouvement, sinon celui de la variante si présent
    return m.unit_price_ttc if m.unit_price_ttc is not None else getattr(v, "price_ttc", None)


def is_ecocup_product(product_name: Optional[str]) -> bool:
    if not product_name:
        return False
    name = product_name.strip().lower()
    # tolère "ecocup", "éco cup", "gobelet consigné", etc.
    return ("ecocup" in name) or ("éco" in name and "cup" in name) or ("gobelet" in name)


def effective_deposit(m: Movement, product_name: Optional[str]) -> float:
    """
    Retourne la consigne effective pour la ligne:
      - si la consigne est saisie sur le mouvement => on l'utilise
      - sinon: 1,00 € si produit Ecocup ; 30,00 € sinon (fûts)
    """
    if m.deposit_per_keg is not None:
        try:
            return float(m.deposit_per_keg)
        except Exception:
            pass
    if is_ecocup_product(product_name):
        return float(DEFAULT_ECOCUP_DEPOSIT)
    return float(DEFAULT_KEG_DEPOSIT)


# --- Requêtes composées ---
def client_movements_full(client_id: int):
    q = (
        db.session.query(Movement, Variant, Product)
        .join(Variant, Movement.variant_id == Variant.id)
        .join(Product, Variant.product_id == Product.id)
        .filter(Movement.client_id == client_id)
        .order_by(Movement.created_at.desc(), Movement.id.desc())
    )
    return q.all()


def summarize_client_detail(c: Client) -> Dict:
    rows = []
    beer_eur = 0.0
    deposit_eur = 0.0
    equipment = Equipment()
    liters_out_cum = 0.0

    for m, v, p in client_movements_full(c.id):
        price = effective_price(m, v) or 0.0
        dep = effective_deposit(m, getattr(p, "name", None))
        eq = parse_equipment(m.notes)

        if m.type == "OUT":
            beer_eur += (m.qty or 0) * price
            deposit_eur += (m.qty or 0) * dep
            liters_out_cum += (m.qty or 0) * (getattr(v, "size_l", 0) or 0)
            combine_equipment(equipment, eq, +1)
        elif m.type in {"IN", "DEFECT", "FULL"}:
            if m.type == "IN":
                combine_equipment(equipment, eq, -1)
        else:
            pass

        rows.append(dict(
            id=m.id,
            date=m.created_at,
            type=m.type,
            product=p.name,
            size_l=getattr(v, "size_l", None),
            qty=m.qty,
            unit_price_ttc=price,
            deposit_per_keg=dep,
            notes=m.notes,
        ))

    sums = dict(
        db.session.query(Movement.type, func.coalesce(func.sum(Movement.qty), 0))
        .filter(Movement.client_id == c.id)
        .group_by(Movement.type)
        .all()
    )
    total_out = int(sums.get("OUT", 0))
    total_in = int(sums.get("IN", 0))
    total_def = int(sums.get("DEFECT", 0))
    total_full = int(sums.get("FULL", 0))
    kegs = total_out - (total_in + total_def + total_full)

    return dict(
        rows=rows,
        kegs=kegs,
        beer_eur=round(beer_eur, 2),
        deposit_eur=round(deposit_eur, 2),
        equipment=equipment,
        liters_out_cum=round(liters_out_cum, 2),
    )
