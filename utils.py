# utils.py
from __future__ import annotations

from typing import Dict, Tuple, List
from datetime import datetime, timezone

from sqlalchemy import func
from models import db, Client, Product, Variant, Movement, Inventory

# ---------- Petites aides ----------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _is_equipment_only(name: str | None) -> bool:
    if not name:
        return False
    n = name.lower()
    return (("matériel" in n or "materiel" in n) and "seul" in n)

def _is_cup(name: str | None) -> bool:
    if not name:
        return False
    n = name.lower()
    is_cup = ("ecocup" in n) or ("eco cup" in n) or ("gobelet" in n)
    is_maintenance = any(w in n for w in ["lavage", "wash", "perdu", "perte", "clean"])
    return is_cup and not is_maintenance

def _sign_for_type(mtype: str) -> int:
    # OUT = on met chez le client (+1), IN/DEFECT/FULL = on récupère (-1)
    if mtype == "OUT":
        return 1
    if mtype in ("IN", "DEFECT", "FULL"):
        return -1
    return 0

# ---------- Consignes (séparées gobelets / fûts) ----------

def compute_deposits_split(client_id: int) -> Tuple[float, int, float, int]:
    """
    Retourne (deposit_cup_eur, cup_qty_in_play, deposit_keg_eur, keg_qty_in_play).
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
        s = _sign_for_type(mtype)
        if s == 0:
            continue

        if _is_equipment_only(pname):
            # matériel seul : ne compte pas dans les consignes
            continue

        is_cup = _is_cup(pname)

        # fallback consigne
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

# ---------- Matériel prêté (parser les notes) ----------

def _parse_equipment_notes(notes: str | None) -> Dict[str, int]:
    """
    Parse 'tireuse=1;co2=2;comptoir=1;tonnelle=1;...' en dict d'int.
    """
    res = {"tireuse": 0, "co2": 0, "comptoir": 0, "tonnelle": 0}
    if not notes:
        return res
    parts = [p.strip() for p in notes.split(";") if p.strip()]
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            k = k.strip().lower()
            try:
                v_int = int(v.strip())
            except Exception:
                continue
            if k in res:
                res[k] = v_int
    return res

def compute_equipment_in_play(client_id: int) -> Dict[str, int]:
    """
    Somme signée des équipements en prêt via les notes des mouvements.
    """
    rows = (
        db.session.query(Movement.type, Movement.notes)
        .filter(Movement.client_id == client_id)
        .all()
    )
    acc = {"tireuse": 0, "co2": 0, "comptoir": 0, "tonnelle": 0}
    for mtype, notes in rows:
        s = _sign_for_type(mtype)
        if s == 0:
            continue
        parsed = _parse_equipment_notes(notes)
        for k in acc:
            acc[k] += s * int(parsed.get(k, 0))
    # évite les -0
    for k in acc:
        if abs(acc[k]) < 1e-9:
            acc[k] = 0
    return acc

# ---------- Fûts en jeu (nombre) ----------

def open_kegs_qty_for_client(client_id: int) -> int:
    """
    Renvoie le nombre de fûts en jeu chez un client (tout sauf ecocup et matériel seul).
    """
    rows = (
        db.session.query(Movement.qty, Movement.type, Product.name)
        .join(Variant, Movement.variant_id == Variant.id)
        .join(Product, Variant.product_id == Product.id)
        .filter(Movement.client_id == client_id)
        .all()
    )
    total = 0
    for qty, mtype, pname in rows:
        if not qty:
            continue
        if _is_equipment_only(pname) or _is_cup(pname):
            continue
        s = _sign_for_type(mtype)
        total += s * int(qty)
    return int(total)

# ---------- Récap accueil par client ----------

def summarize_client_for_index(client: Client) -> Dict:
    """
    Fabrique la carte pour l'accueil.
    """
    cup_eur, _cup_qty, keg_eur, _keg_qty = compute_deposits_split(client.id)
    equipment = compute_equipment_in_play(client.id)
    kegs_qty = open_kegs_qty_for_client(client.id)

    # "Bière facturée" simple : somme OUT (prix TTC de la variante si champ vide)
    billed = (
        db.session.query(
            func.coalesce(Movement.unit_price_ttc, Variant.price_ttc, 0.0) * Movement.qty
        )
        .join(Variant, Movement.variant_id == Variant.id)
        .join(Product, Variant.product_id == Product.id)
        .filter(
            Movement.client_id == client.id,
            Movement.type == "OUT",
            ~Product.name.ilike("%ecocup%"),
            ~Product.name.ilike("%eco cup%"),
            ~Product.name.ilike("%gobelet%"),
        )
        .all()
    )
    billed_beer_eur = 0.0
    for (val,) in billed:
        try:
            billed_beer_eur += float(val or 0.0)
        except Exception:
            pass

    return {
        "id": client.id,                 # pour compatibilité
        "client_id": client.id,          # pour accès via dict.get dans le template
        "name": client.name,
        "equipment": equipment,
        "kegs_qty": kegs_qty,
        "billed_beer_eur": round(billed_beer_eur, 2),
        "deposits_cup_eur": round(cup_eur, 2),
        "deposits_keg_eur": round(keg_eur, 2),
    }

def summarize_totals(cards: List[Dict]) -> Dict[str, float]:
    total_beer = sum(float(c.get("billed_beer_eur") or 0) for c in cards)
    total_cup  = sum(float(c.get("deposits_cup_eur") or 0) for c in cards)
    total_keg  = sum(float(c.get("deposits_keg_eur") or 0) for c in cards)
    return {
        "billed_beer_eur": round(total_beer, 2),
        "deposits_cup_eur": round(total_cup, 2),
        "deposits_keg_eur": round(total_keg, 2),
    }

# ---------- Stock / catalogue ----------

def get_stock_items():
    """
    Retourne [(Variant, Product, inv_qty, min_qty)].
    Ici on ne dépend PAS d'un champ Product.min_qty (absent chez toi) -> min_qty = 0.
    """
    rows = (
        db.session.query(
            Variant,
            Product,
            func.coalesce(Inventory.qty, 0).label("inv_qty"),
        )
        .join(Product, Variant.product_id == Product.id)
        .outerjoin(Inventory, Inventory.variant_id == Variant.id)
        .order_by(Product.name.asc(), Variant.size_l.asc())
        .all()
    )
    # Adapter au format attendu (avec min_qty en 0)
    return [(v, p, inv_qty, 0) for (v, p, inv_qty) in rows]

def compute_reorder_alerts() -> List[Dict]:
    """
    Sans min_qty côté DB, on ne déclenche d'alerte que si inv_qty < 0 (anomalie).
    """
    alerts = []
    for v, p, inv_qty, _min_qty in get_stock_items():
        if (inv_qty or 0) < 0:
            alerts.append({
                "product": p,
                "variant": v,
                "message": f"Stock négatif détecté: {inv_qty}",
            })
    return alerts
