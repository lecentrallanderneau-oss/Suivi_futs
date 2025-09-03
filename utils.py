# utils.py
from __future__ import annotations
from datetime import datetime
from typing import Dict, List, Tuple

from sqlalchemy import func

from models import db, Client, Product, Variant, Movement, ReorderRule, Inventory


# -------------------- Outils généraux --------------------

def now_utc() -> datetime:
    """Retourne un datetime naïf en UTC (cohérent avec le reste de l'app)."""
    return datetime.utcnow()


def _to_int(x, default=0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def _to_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _is_ecocup_name(name: str) -> bool:
    n = (name or "").lower()
    return ("ecocup" in n) or ("gobelet" in n)


def _is_equipment_only_name(name: str) -> bool:
    n = (name or "").lower()
    # On considère "Matériel seul" comme l'article générique d'équipement
    return ("matériel" in n or "materiel" in n) and ("seul" in n)


def default_deposit_for_product(product) -> float:
    """
    Consigne par défaut :
    - Ecocup / gobelets -> 1.00 €
    - Autres (fûts, etc.) -> 30.00 €
    """
    name = (getattr(product, "name", "") or "").lower()
    if "ecocup" in name or "gobelet" in name:
        return 1.0
    return 30.0


def get_or_create_inventory(variant_id: int) -> Inventory:
    """Récupère (ou crée) une ligne d'inventaire pour la variante donnée."""
    inv = Inventory.query.filter_by(variant_id=variant_id).first()
    if not inv:
        inv = Inventory(variant_id=variant_id, qty=0)
        db.session.add(inv)
        # ATTENTION : commit géré par l'appelant, pour regrouper les opérations
    return inv


# -------------------- Agrégations client --------------------

def _movements_joined_for_client(client_id: int):
    """
    Retourne tous les mouvements du client avec leurs variantes/produits,
    triés du plus récent au plus ancien.
    """
    rows = (
        db.session.query(Movement, Variant, Product)
        .join(Variant, Movement.variant_id == Variant.id)
        .join(Product, Variant.product_id == Product.id)
        .filter(Movement.client_id == client_id)
        .order_by(Movement.created_at.desc(), Movement.id.desc())
        .all()
    )
    return rows


def _parse_equipment_notes(notes: str) -> Dict[str, int]:
    """
    Convertit une chaîne de notes 'tireuse=1;co2=2;comptoir=1;tonnelle=0'
    en dict de compteurs.
    """
    out: Dict[str, int] = {"tireuse": 0, "co2": 0, "comptoir": 0, "tonnelle": 0}
    if not notes:
        return out
    parts = [p.strip() for p in notes.split(";") if p.strip()]
    for part in parts:
        if "=" in part:
            k, v = part.split("=", 1)
            k = k.strip().lower()
            if k in out:
                out[k] += _to_int(v, 0)
    return out


def summarize_client_detail(client: Client) -> Dict:
    """
    Calcule les indicateurs utilisés sur la fiche client :
    - kegs : unités en jeu (hors ecocup & matériel seul)
    - deposit_eur : consignes en jeu
    - beer_eur : montant TTC facturé (cumul) pour la bière (hors ecocup / matériel)
    - liters_out_cum : litres livrés (cumul)
    - equipment : totaux d'équipement en jeu (via notes OUT/IN)
    """
    rows = _movements_joined_for_client(client.id)

    kegs_open = 0
    deposit_eur = 0.0
    beer_eur = 0.0
    liters_out_cum = 0.0
    equipment = {"tireuse": 0, "co2": 0, "comptoir": 0, "tonnelle": 0}

    for m, v, p in rows:
        qty = _to_int(m.qty, 0)
        pname = (p.name if p else "") or ""
        is_cup = _is_ecocup_name(pname)
        is_equipment_only = _is_equipment_only_name(pname)
        unit_price = m.unit_price_ttc if (m.unit_price_ttc is not None) else (v.price_ttc if v and v.price_ttc is not None else None)
        deposit = m.deposit_per_keg if (m.deposit_per_keg is not None) else default_deposit_for_product(p)

        # Débits/Crédits consigne
        if m.type == "OUT":
            deposit_eur += _to_float(deposit) * qty
        elif m.type in ("IN", "DEFECT", "FULL"):
            deposit_eur -= _to_float(deposit) * qty

        # Bière facturée et litres sortis (on ignore ecocup et "matériel seul")
        if not is_cup and not is_equipment_only:
            if m.type == "OUT":
                if v and v.size_l:
                    liters_out_cum += _to_float(v.size_l) * qty
                if unit_price is not None:
                    beer_eur += _to_float(unit_price) * qty

            # Unités en jeu (kegs)
            if m.type == "OUT":
                kegs_open += qty
            elif m.type in ("IN", "DEFECT", "FULL"):
                kegs_open -= qty

        # Équipement en jeu sur base des notes (OUT ajoute, IN retire)
        if m.notes:
            parsed = _parse_equipment_notes(m.notes)
            sign = 1 if m.type == "OUT" else (-1 if m.type in ("IN", "DEFECT", "FULL") else 0)
            if sign:
                for k in equipment:
                    equipment[k] += sign * _to_int(parsed.get(k, 0), 0)

    return {
        "kegs": max(kegs_open, 0),
        "deposit_eur": round(deposit_eur, 2),
        "beer_eur": round(beer_eur, 2),
        "liters_out_cum": round(liters_out_cum, 2),
        "equipment": equipment,
    }


def summarize_client_for_index(client: Client) -> Dict:
    """Résumé compact pour la page d'accueil."""
    detail = summarize_client_detail(client)
    return {
        "id": client.id,
        "name": client.name,
        "kegs": detail["kegs"],
        "deposit_eur": detail["deposit_eur"],
        "beer_eur": detail["beer_eur"],
        "liters_out_cum": detail["liters_out_cum"],
        "equipment": detail["equipment"],
    }


def summarize_totals(cards: List[Dict]) -> Dict:
    """Totaux agrégés pour l'accueil."""
    total_clients = len(cards)
    total_kegs = sum(_to_int(c.get("kegs", 0), 0) for c in cards)
    total_deposit = sum(_to_float(c.get("deposit_eur", 0.0), 0.0) for c in cards)
    total_beer = sum(_to_float(c.get("beer_eur", 0.0), 0.0) for c in cards)
    total_liters = sum(_to_float(c.get("liters_out_cum", 0.0), 0.0) for c in cards)
    return {
        "clients": total_clients,
        "kegs": total_kegs,
        "deposit_eur": round(total_deposit, 2),
        "beer_eur": round(total_beer, 2),
        "liters_out_cum": round(total_liters, 2),
    }


# -------------------- Stock & réassort --------------------

def get_stock_items() -> List[Tuple[Variant, Product, int, int]]:
    """
    Retourne les lignes pour l'écran Stock.
    Format : liste de tuples (Variant, Product, inv_qty, min_qty)
    - inv_qty : quantité d'inventaire actuelle (bar)
    - min_qty : seuil mini (règle de réassort)
    """
    # Toutes les variantes + produits
    rows = (
        db.session.query(Variant, Product)
        .join(Product, Variant.product_id == Product.id)
        .order_by(Product.name.asc(), Variant.size_l.asc())
        .all()
    )

    out: List[Tuple[Variant, Product, int, int]] = []
    for v, p in rows:
        inv = Inventory.query.filter_by(variant_id=v.id).first()
        rr = ReorderRule.query.filter_by(variant_id=v.id).first()
        inv_qty = _to_int(inv.qty if inv and inv.qty is not None else 0, 0)
        min_qty = _to_int(rr.min_qty if rr and rr.min_qty is not None else 0, 0)
        out.append((v, p, inv_qty, min_qty))
    return out


def compute_reorder_alerts() -> List[Dict]:
    """
    Construit la liste des alertes de réassort :
    [{ 'variant_id': ..., 'product': 'Nom', 'size_l': 30.0, 'inv_qty': 1, 'min_qty': 3, 'missing': 2 }, ...]
    """
    alerts: List[Dict] = []
    # On part des règles connues
    rules = ReorderRule.query.all()
    for rr in rules:
        v = Variant.query.get(rr.variant_id)
        if not v:
            continue
        p = Product.query.get(v.product_id)
        inv = Inventory.query.filter_by(variant_id=v.id).first()
        inv_qty = _to_int(inv.qty if inv and inv.qty is not None else 0, 0)
        min_qty = _to_int(rr.min_qty if rr and rr.min_qty is not None else 0, 0)
        if min_qty > inv_qty:
            alerts.append({
                "variant_id": v.id,
                "product": (p.name if p else f"Var#{v.id}"),
                "size_l": _to_float(v.size_l, None),
                "inv_qty": inv_qty,
                "min_qty": min_qty,
                "missing": (min_qty - inv_qty),
            })
    # Tri : manque le plus élevé d'abord, puis par nom
    alerts.sort(key=lambda a: (-a["missing"], a["product"]))
    return alerts
