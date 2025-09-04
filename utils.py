# utils.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Tuple, List, Iterable, Optional

from sqlalchemy import func

from models import db, Client, Product, Variant, Movement, Inventory


# ---------- Helpers généraux ----------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def format_eur(x: Optional[float]) -> str:
    try:
        return f"{float(x):.2f} €"
    except Exception:
        return "0.00 €"


def _lc(s: Optional[str]) -> str:
    return (s or "").lower().strip()


# ---------- Détection produit “écocup” / “matériel seul” ----------

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
    if is_cup_product_name(product.name):
        return 1.0
    return 30.0


# ---------- Inventaire (bar) ----------

def get_or_create_inventory(variant_id: int) -> Inventory:
    inv = Inventory.query.filter_by(variant_id=variant_id).first()
    if not inv:
        inv = Inventory(variant_id=variant_id, qty=0)
        db.session.add(inv)
        db.session.flush()
    return inv


# ---------- Matériel prêté (tireuse/co2/comptoir/tonnelle) ----------

_EQ_KEYS = ("tireuse", "co2", "comptoir", "tonnelle")


def parse_equipment_notes(notes: Optional[str]) -> Dict[str, int]:
    """
    Extrait un dict { 'tireuse': n, 'co2': n, 'comptoir': n, 'tonnelle': n }
    depuis la chaîne de notes. Tolérant aux autres textes.
    """
    res = {k: 0 for k in _EQ_KEYS}
    if not notes:
        return res
    parts = [p.strip() for p in notes.split(";") if p and "=" in p]
    for p in parts:
        try:
            k, v = p.split("=", 1)
            k = _lc(k)
            if k in res:
                res[k] += int(str(v).strip())
        except Exception:
            # Ignore tout ce qui n'est pas “clé=valeur int”
            continue
    return res


def equipment_in_play_for_client(client_id: int) -> Dict[str, int]:
    """
    Calcule le matériel encore “en jeu” pour un client en cumulant tous les mouvements :
    - OUT  => +
    - IN / DEFECT / FULL => −
    Les quantités sont lues dans Movement.notes via parse_equipment_notes().
    Le matériel “Matériel seul” saisi via le wizard est encodé dans ces notes.
    """
    totals = {k: 0 for k in _EQ_KEYS}

    rows: List[Tuple[str, Optional[str]]] = (
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

    # pas de négatifs affichés (sécurité si historique atypique)
    for k in list(totals):
        if totals[k] < 0:
            totals[k] = 0

    return totals


# Alias compat (si certaines vues appellent encore l’ancienne fonction)
def compute_equipment_in_play(client_id: int) -> Dict[str, int]:
    return equipment_in_play_for_client(client_id)


# ---------- Consignes séparées (gobelets vs fûts) ----------

def compute_deposits_split(client_id: int) -> Tuple[float, int, float, int]:
    """
    Retourne (deposit_cup_eur, cup_qty_in_play, deposit_keg_eur, keg_qty_in_play)
    en séparant consigne Ecocup vs consigne Fûts.
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

        dep_val = None
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


# ---------- Résumé client pour l’accueil ----------

def summarize_client_for_index(client: Client) -> Dict:
    """
    Retourne un dict prêt pour index.html :
      {
        id, name,
        billed_beer_eur,  # total € bière (OUT uniquement)
        deposits_cup_eur, deposits_keg_eur,
        equipment: {tireuse, co2, comptoir, tonnelle}
      }
    """
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


# ---------- Stock & alertes ----------

def get_stock_items():
    """
    Liste le stock bar par variante.
    Retourne une liste de tuples (Variant, Product, inv_qty, min_qty)
    """
    rows = (
        db.session.query(
            Variant,
            Product,
            func.coalesce(Inventory.qty, 0).label("inv_qty"),
            func.coalesce(Product.min_qty, 0).label("min_qty"),
        )
        .join(Product, Variant.product_id == Product.id)
        .outerjoin(Inventory, Inventory.variant_id == Variant.id)
        .order_by(Product.name.asc(), Variant.size_l.asc())
        .all()
    )
    return rows


def compute_reorder_alerts() -> List[Dict]:
    """
    Alimente les alertes de réappro :
      - variantes dont l’inventaire est < min_qty (si min_qty > 0)
    """
    rows = get_stock_items()
    alerts: List[Dict] = []
    for v, p, inv_qty, min_qty in rows:
        try:
            mi = int(min_qty or 0)
            q = int(inv_qty or 0)
        except Exception:
            mi = 0
            q = 0
        if mi > 0 and q < mi:
            alerts.append({
                "product": p,
                "variant": v,
                "current": q,
                "min": mi,
            })
    return alerts
