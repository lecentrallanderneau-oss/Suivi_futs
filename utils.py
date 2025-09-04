# utils.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func

from models import db, Client, Product, Variant, Movement, Inventory


# -------- Helpers --------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _lc(s: Optional[str]) -> str:
    return (s or "").lower().strip()


def format_eur(x: Optional[float]) -> str:
    try:
        return f"{float(x):.2f} €"
    except Exception:
        return "0.00 €"


# -------- Détection produit (écocup / matériel seul) --------

def is_cup_product_name(name: str) -> bool:
    n = _lc(name)
    if not n:
        return False
    is_cup = ("ecocup" in n) or ("eco cup" in n) or ("gobelet" in n)
    is_maintenance = any(w in n for w in ["lavage", "wash", "perdu", "perte", "clean"])
    return is_cup and not is_maintenance


def is_equipment_only_name(name: str) -> bool:
    n = _lc(name)
    return (("matériel" in n or "materiel" in n) and "seul" in n)


def default_deposit_for_product(product: Product) -> float:
    return 1.0 if is_cup_product_name(product.name) else 30.0


# -------- Inventaire (bar) --------

def get_or_create_inventory(variant_id: int) -> Inventory:
    inv = Inventory.query.filter_by(variant_id=variant_id).first()
    if not inv:
        inv = Inventory(variant_id=variant_id, qty=0)
        db.session.add(inv)
        db.session.flush()
    return inv


# -------- Matériel prêté (via notes) --------

_EQ_KEYS = ("tireuse", "co2", "comptoir", "tonnelle")


def parse_equipment_notes(notes: Optional[str]) -> Dict[str, int]:
    res = {k: 0 for k in _EQ_KEYS}
    if not notes:
        return res
    for part in notes.split(";"):
        if "=" not in part:
            continue
        try:
            k, v = part.split("=", 1)
            k = _lc(k)
            if k in res:
                res[k] += int(str(v).strip())
        except Exception:
            continue
    return res


def equipment_in_play_for_client(client_id: int) -> Dict[str, int]:
    totals = {k: 0 for k in _EQ_KEYS}
    rows = (
        db.session.query(Movement.type, Movement.notes)
        .filter(Movement.client_id == client_id)
        .all()
    )
    for mtype, notes in rows:
        if mtype == "OUT":
            s = 1
        elif mtype in ("IN", "DEFECT", "FULL"):
            s = -1
        else:
            s = 0
        if s == 0:
            continue
        parsed = parse_equipment_notes(notes)
        for k in _EQ_KEYS:
            totals[k] += s * int(parsed.get(k, 0))
    # pas de négatifs
    for k in totals:
        if totals[k] < 0:
            totals[k] = 0
    return totals


# compat
def compute_equipment_in_play(client_id: int) -> Dict[str, int]:
    return equipment_in_play_for_client(client_id)


# -------- Consignes (séparées gobelets / fûts) --------

def compute_deposits_split(client_id: int) -> Tuple[float, int, float, int]:
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

    cup_eur = 0.0
    cup_qty = 0
    keg_eur = 0.0
    keg_qty = 0

    for qty, mtype, dep, _size_l, pname in rows:
        if not qty:
            continue
        if mtype == "OUT":
            s = 1
        elif mtype in ("IN", "DEFECT", "FULL"):
            s = -1
        else:
            continue

        name_lc = _lc(pname)
        if is_equipment_only_name(name_lc):
            continue

        is_cup = is_cup_product_name(name_lc)

        dep_val: Optional[float] = None
        if dep is not None:
            try:
                dep_val = float(dep)
            except Exception:
                dep_val = None
        if dep_val is None:
            dep_val = 1.0 if is_cup else 30.0

        if is_cup:
            cup_qty += s * int(qty)
            cup_eur += s * dep_val * int(qty)
        else:
            keg_qty += s * int(qty)
            keg_eur += s * dep_val * int(qty)

    return (cup_eur, cup_qty, keg_eur, keg_qty)


# -------- Résumés pour vues --------

def summarize_client_for_index(client: Client) -> Dict:
    billed = (
        db.session.query(func.coalesce(func.sum(Movement.qty * Movement.unit_price_ttc), 0.0))
        .filter(Movement.client_id == client.id, Movement.type == "OUT")
        .scalar()
        or 0.0
    )
    dep_cup_eur, _cup_qty, dep_keg_eur, _keg_qty = compute_deposits_split(client.id)
    eq = equipment_in_play_for_client(client.id)
    return {
        "id": client.id,
        "name": client.name,
        "billed_beer_eur": float(billed),
        "deposits_cup_eur": float(dep_cup_eur),
        "deposits_keg_eur": float(dep_keg_eur),
        "equipment": eq,
    }


def summarize_totals(cards: List[Dict]) -> Dict[str, float]:
    out = {"billed_beer_eur": 0.0, "deposits_cup_eur": 0.0, "deposits_keg_eur": 0.0}
    for c in cards:
        out["billed_beer_eur"] += float(c.get("billed_beer_eur", 0) or 0)
        out["deposits_cup_eur"] += float(c.get("deposits_cup_eur", 0) or 0)
        out["deposits_keg_eur"] += float(c.get("deposits_keg_eur", 0) or 0)
    return out


def summarize_client_detail(client: Client) -> Dict:
    liters = (
        db.session.query(func.coalesce(func.sum(Movement.qty * Variant.size_l), 0))
        .join(Variant, Movement.variant_id == Variant.id)
        .filter(Movement.client_id == client.id, Movement.type == "OUT")
        .scalar()
        or 0
    )
    billed = (
        db.session.query(func.coalesce(func.sum(Movement.qty * Movement.unit_price_ttc), 0.0))
        .filter(Movement.client_id == client.id, Movement.type == "OUT")
        .scalar()
        or 0.0
    )
    dep_cup_eur, dep_cup_qty, dep_keg_eur, dep_keg_qty = compute_deposits_split(client.id)
    eq = equipment_in_play_for_client(client.id)

    # historique
    q = (
        db.session.query(Movement, Variant, Product)
        .join(Variant, Movement.variant_id == Variant.id)
        .join(Product, Variant.product_id == Product.id)
        .filter(Movement.client_id == client.id)
        .order_by(Movement.created_at.desc(), Movement.id.desc())
    )
    history = []
    for m, v, p in q.all():
        history.append({
            "id": m.id,
            "date": m.created_at,
            "type": m.type,
            "product": p.name,
            "size_l": v.size_l,
            "qty": m.qty,
            "unit_price_ttc": m.unit_price_ttc,
            "deposit_per_keg": m.deposit_per_keg,
            "notes": m.notes,
        })

    return {
        "id": client.id,
        "name": client.name,
        "liters_delivered": float(liters or 0),
        "billed_beer_eur": float(billed or 0),
        "deposits": {
            "cup_eur": float(dep_cup_eur or 0),
            "cup_qty": int(dep_cup_qty or 0),
            "keg_eur": float(dep_keg_eur or 0),
            "keg_qty": int(dep_keg_qty or 0),
            "total_eur": float((dep_cup_eur or 0) + (dep_keg_eur or 0)),
        },
        "equipment": eq,
        "history": history,
    }


# -------- Stock & alertes --------
# (ne dépend pas de Product.min_qty — s’il n’existe pas on met 0)

def get_stock_items():
    """
    Retourne une liste [(Variant, Product, inv_qty, min_qty)].
    min_qty = getattr(Product, 'min_qty', 0)
    """
    rows = (
        db.session.query(Variant, Product, Inventory.qty)
        .join(Product, Variant.product_id == Product.id)
        .outerjoin(Inventory, Inventory.variant_id == Variant.id)
        .order_by(Product.name.asc(), Variant.size_l.asc())
        .all()
    )
    out = []
    for v, p, inv_qty in rows:
        out.append((v, p, int(inv_qty or 0), int(getattr(p, "min_qty", 0) or 0)))
    return out


def compute_reorder_alerts() -> List[Dict]:
    alerts: List[Dict] = []
    for v, p, inv_qty, min_qty in get_stock_items():
        try:
            q = int(inv_qty or 0)
            mi = int(min_qty or 0)
        except Exception:
            q, mi = 0, 0
        if mi > 0 and q < mi:
            alerts.append({"product": p, "variant": v, "current": q, "min": mi})
    return alerts
