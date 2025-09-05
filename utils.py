# utils.py
from __future__ import annotations

from datetime import datetime
from typing import Dict, Tuple, List
from types import SimpleNamespace
from sqlalchemy import func
from models import db, Client, Product, Variant, Movement, Inventory, ReorderRule


# --------- basics ----------
def now_utc():
    return datetime.utcnow()


def get_or_create_inventory(variant_id: int) -> Inventory:
    inv = Inventory.query.filter_by(variant_id=variant_id).first()
    if not inv:
        inv = Inventory(variant_id=variant_id, qty=0)
        db.session.add(inv)
        db.session.flush()
    return inv


# --------- règles de consigne ----------
def _is_cup_product(p: Product) -> bool:
    if not p or not p.name:
        return False
    n = p.name.lower()
    # exclure maintenance
    if any(w in n for w in ["lavage", "wash", "clean", "perdu", "perte"]):
        return False
    return ("ecocup" in n) or ("eco cup" in n) or ("gobelet" in n)


def default_deposit_for_product(p: Product) -> float:
    """Valeur par défaut : 0 € maintenance, 1 € gobelet/ecocup, 30 € fûts"""
    if not p or not p.name:
        return 30.0
    n = p.name.lower()
    if any(w in n for w in ["lavage", "wash", "clean", "perdu", "perte"]):
        return 0.0
    return 1.0 if _is_cup_product(p) else 30.0


# --------- calculs consignes scindées ----------
def compute_deposits_split(client_id: int) -> Tuple[float, int, float, int]:
    """
    Retourne (deposit_cup_eur, cup_qty_in_play, deposit_keg_eur, keg_qty_in_play)
    en séparant consigne Ecocup vs consigne Fûts.
    Le signe est géré comme d'habitude : OUT +, IN/DEFECT/FULL -.
    On utilise la consigne de la ligne (deposit_per_keg) si présente, sinon:
      - 1.0 € pour Ecocup
      - 30.0 € pour Fûts
    """
    rows = (
        db.session.query(
            Movement.qty,
            Movement.type,
            Movement.deposit_per_keg,
            Variant.size_l,
            Product.name,
        )
        .join(Variant, Movement.variant_id == Variant.id)
        .join(Product, Variant.product_id == Product.id)
        .filter(Movement.client_id == client_id)
        .all()
    )

    def _is_cup_name(name: str) -> bool:
        if not name:
            return False
        n = name.lower()
        is_cup = ("ecocup" in n) or ("eco cup" in n) or ("gobelet" in n)
        is_maintenance = any(w in n for w in ["lavage", "wash", "perdu", "perte", "clean"])
        return is_cup and not is_maintenance

    def _is_equipment_only(name: str) -> bool:
        if not name:
            return False
        n = name.lower()
        return (("matériel" in n or "materiel" in n) and "seul" in n)

    cup_eur = 0.0
    cup_qty = 0
    keg_eur = 0.0
    keg_qty = 0

    for qty, mtype, dep, size_l, pname in rows:
        if not qty:
            continue

        # signe : OUT = +, IN/DEFECT/FULL = -
        if mtype == "OUT":
            s = 1
        elif mtype in ("IN", "DEFECT", "FULL"):
            s = -1
        else:
            s = 0
        if s == 0:
            continue

        name_lc = (pname or "").lower()

        if _is_equipment_only(name_lc):
            # le matériel seul n’entre pas dans la consigne
            continue

        is_cup = _is_cup_name(name_lc)

        # valeur de consigne fallback si non saisie sur la ligne
        if dep is None:
            dep_val = 1.0 if is_cup else 30.0
        else:
            try:
                dep_val = float(dep)
            except Exception:
                dep_val = 1.0 if is_cup else 30.0

        # cumul
        if is_cup:
            cup_qty += s * int(qty)
            cup_eur += s * float(dep_val) * int(qty)
        else:
            # assimilé "fûts" (bière et assimilés)
            keg_qty += s * int(qty)
            keg_eur += s * float(dep_val) * int(qty)

    return (cup_eur, cup_qty, keg_eur, keg_qty)


# --------- parse "équipement" depuis notes ---------
def _parse_equipment_notes(notes: str) -> Dict[str, int]:
    """
    Parse une chaîne "tireuse=1;co2=2;comptoir=0;tonnelle=1"
    -> dict { tireuse, co2, comptoir, tonnelle } (entiers, défaut 0)
    """
    keys = ["tireuse", "co2", "comptoir", "tonnelle"]
    res = {k: 0 for k in keys}
    if not notes:
        return res
    parts = [p.strip() for p in str(notes).split(";") if p.strip()]
    for part in parts:
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip().lower()
        try:
            val = int(v.strip())
        except Exception:
            continue
        if k in res:
            res[k] = val
    return res


def _latest_equipment_snapshot(client_id: int) -> Dict[str, int]:
    """
    Dernière "photo" d'équipement depuis les notes du dernier mouvement du client.
    (on suppose que l'utilisateur met à jour les notes à chaque opération)
    """
    m = (
        Movement.query
        .filter(Movement.client_id == client_id)
        .order_by(Movement.created_at.desc(), Movement.id.desc())
        .first()
    )
    return _parse_equipment_notes(m.notes if m else "")


# --------- agrégations pour l'accueil & la fiche client ----------
def _beer_and_liters_out(client_id: int) -> Tuple[float, float]:
    """
    Sommes "bière €" (OUT uniquement, unit_price_ttc * qty) et "litres sortis cumulé"
    (size_l * qty sur OUT uniquement), hors matériel/gobelets maintenance.
    """
    rows = (
        db.session.query(
            Movement.qty,
            Movement.type,
            Movement.unit_price_ttc,
            Variant.size_l,
            Product.name,
        )
        .join(Variant, Movement.variant_id == Variant.id)
        .join(Product, Variant.product_id == Product.id)
        .filter(Movement.client_id == client_id)
        .all()
    )
    beer_eur = 0.0
    liters = 0.0

    def is_equipment_only(name: str) -> bool:
        if not name:
            return False
        n = name.lower()
        return (("matériel" in n or "materiel" in n) and "seul" in n)

    def is_maintenance(name: str) -> bool:
        if not name:
            return False
        n = name.lower()
        return any(w in n for w in ["lavage", "wash", "clean", "perdu", "perte"])

    for qty, mtype, up, size_l, pname in rows:
        if mtype != "OUT":
            continue
        if not qty:
            continue
        if is_equipment_only(pname) or is_maintenance(pname):
            continue
        if size_l:
            liters += float(size_l) * int(qty)
        if up:
            beer_eur += float(up) * int(qty)
    return (beer_eur, liters)


def summarize_client_for_index(c: Client) -> Dict:
    dep_cup_eur, cup_qty, dep_keg_eur, keg_qty = compute_deposits_split(c.id)
    beer_eur, liters = _beer_and_liters_out(c.id)
    eq = _latest_equipment_snapshot(c.id)
    return {
        "id": c.id,
        "name": c.name,
        "equipment": eq,                # attendu par _macros.html -> equipment_badges
        "beer_eur": beer_eur,
        "liters_out_cum": liters,
        "deposit_eur": (dep_cup_eur + dep_keg_eur),
        "deposit_cup_eur": dep_cup_eur,
        "deposit_keg_eur": dep_keg_eur,
        "cup_qty_in_play": cup_qty,
        "keg_qty_in_play": keg_qty,
    }


def summarize_totals(cards: List[Dict]) -> Dict:
    tot_beer = sum(float(c.get("beer_eur", 0) or 0) for c in cards)
    tot_liters = sum(float(c.get("liters_out_cum", 0) or 0) for c in cards)
    tot_dep_cup = sum(float(c.get("deposit_cup_eur", 0) or 0) for c in cards)
    tot_dep_keg = sum(float(c.get("deposit_keg_eur", 0) or 0) for c in cards)
    tot_cup_qty = sum(int(c.get("cup_qty_in_play", 0) or 0) for c in cards)
    tot_keg_qty = sum(int(c.get("keg_qty_in_play", 0) or 0) for c in cards)
    return {
        "beer_eur": tot_beer,
        "liters_out_cum": tot_liters,
        "deposit_cup_eur": tot_dep_cup,
        "deposit_keg_eur": tot_dep_keg,
        "deposit_eur": tot_dep_cup + tot_dep_keg,
        "cup_qty_in_play": tot_cup_qty,
        "keg_qty_in_play": tot_keg_qty,
    }


def summarize_client_detail(c: Client) -> Dict:
    dep_cup_eur, cup_qty, dep_keg_eur, keg_qty = compute_deposits_split(c.id)
    beer_eur, liters = _beer_and_liters_out(c.id)
    eq = _latest_equipment_snapshot(c.id)
    return {
        "beer_eur": beer_eur,
        "liters_out_cum": liters,
        "deposit_eur": dep_cup_eur + dep_keg_eur,
        "deposit_cup_eur": dep_cup_eur,
        "deposit_keg_eur": dep_keg_eur,
        "cup_qty_in_play": cup_qty,
        "keg_qty_in_play": keg_qty,
        "equipment": eq,
    }


# --------- alertes réassort & stock ----------
def get_stock_items():
    """
    Retourne [(Variant, Product, inv_qty, min_qty)].
    min_qty vient de ReorderRule (pas de Product.min_qty).
    """
    q = (
        db.session.query(
            Variant,
            Product,
            func.coalesce(Inventory.qty, 0).label("inv_qty"),
            func.coalesce(ReorderRule.min_qty, 0).label("min_qty"),
        )
        .join(Product, Variant.product_id == Product.id)
        .outerjoin(Inventory, Inventory.variant_id == Variant.id)
        .outerjoin(ReorderRule, ReorderRule.variant_id == Variant.id)
        .order_by(Product.name.asc(), Variant.size_l.asc())
    )
    return [(v, p, inv_qty, min_qty) for (v, p, inv_qty, min_qty) in q.all()]


def compute_reorder_alerts() -> List[SimpleNamespace]:
    """
    Renvoie une LISTE d’objets avec les champs attendus par le macro d’alertes :
      - product (obj Product)
      - variant (obj Variant)
      - inv (int), min (int), need (int)
    """
    rows = get_stock_items()
    alerts: List[SimpleNamespace] = []
    for v, p, inv_qty, min_qty in rows:
        try:
            inv_i = int(inv_qty or 0)
            min_i = int(min_qty or 0)
        except Exception:
            inv_i, min_i = 0, 0
        if inv_i < min_i:
            alerts.append(SimpleNamespace(
                product=p,
                variant=v,
                inv=inv_i,
                min=min_i,
                need=max(0, min_i - inv_i),
            ))
    return alerts