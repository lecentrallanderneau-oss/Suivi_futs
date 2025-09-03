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
        return Fa
