# utils.py
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Dict, List, Tuple

from sqlalchemy import func, and_

from models import db, Client, Product, Variant, Movement, Inventory, ReorderRule


# -------------------- Helpers texte / catégorisation --------------------

def _lc(s: str | None) -> str:
    return (s or "").strip().lower()


def _is_cup_product(p: Product | None) -> bool:
    """Produit Ecocup générique (gobelet), hors 'lavage/perdu/perte/wash/clean'."""
    if not p:
        return False
    name = _lc(p.name)
    if ("ecocup" in name or "eco cup" in name or "gobelet" in name):
        if any(x in name for x in ["lavage", "perdu", "perte", "wash", "clean"]):
            return False
        return True
    return False


def _is_cup_maintenance(p: Product | None) -> bool:
    """Ecocup maintenance (lavage / perdu), à exclure des listings et des dépôts."""
    if not p:
        return False
    name = _lc(p.name)
    return ("ecocup" in name or "eco cup" in name or "gobelet" in name) and any(
        x in name for x in ["lavage", "perdu", "perte", "wash", "clean"]
    )


def _is_equipment_only(p: Product | None) -> bool:
    """Matériel seul (pas de volume / pas de dépôt)."""
    if not p:
        return False
    n = _lc(p.name)
    return (("matériel" in n or "materiel" in n) and "seul" in n)


def default_deposit_for_product(p: Product | None) -> float:
    """
    Dépôt par unité :
      - Ecocup générique : 1.0 €
      - Matériel seul : 0 €
      - Autres (fûts & assimilés) : 30.0 €
    """
    if _is_equipment_only(p):
        return 0.0
    if _is_cup_product(p):
        return 1.0
    return 30.0


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# -------------------- TA logique: compute_deposits_split (inchangée) --------------------

def compute_deposits_split(client_id: int):
    """
    Retourne (deposit_cup_eur, cup_qty_in_play, deposit_keg_eur, keg_qty_in_play)
    en séparant consigne Ecocup vs consigne Fûts.
    - Ecocup = produits dont le nom contient ecocup/eco cup/gobelet (hors lavage/perdu/perte/wash/clean)
    - Fûts   = tout le reste "liquide" (on exclut 'Matériel seul', etc.)
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

    def _is_cup(name: str) -> bool:
        if not name:
            return False
        n = name.lower()
        is_cup = ("ecocup" in n) or ("eco cup" in n) or ("gobelet" in n)
        is_maintenance = any(w in n for w in ["lavage", "wash", "perdu", "perte", "clean"])
        return is_cup and not is_maintenance

    def _is_equipment_only_name(name: str) -> bool:
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

        if _is_equipment_only_name(name_lc):
            # le matériel seul n’entre pas dans la consigne
            continue

        is_cup = _is_cup(name_lc)

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

    return (round(cup_eur, 2), int(cup_qty), round(keg_eur, 2), int(keg_qty))


# -------------------- Inventaire --------------------

def get_or_create_inventory(variant_id: int) -> Inventory:
    inv = Inventory.query.filter_by(variant_id=variant_id).first()
    if not inv:
        inv = Inventory(variant_id=variant_id, qty=0)
        db.session.add(inv)
        db.session.flush()
    return inv


# -------------------- Calculs d’état client --------------------

def _open_qty_by_variant(client_id: int) -> Dict[int, int]:
    """
    Pour un client, retourne {variant_id: OUT - (IN + DEFECT + FULL)}.
    > 0 => encore en jeu chez le client.
    """
    out_rows = dict(
        db.session.query(Movement.variant_id, func.coalesce(func.sum(Movement.qty), 0))
        .filter(Movement.client_id == client_id, Movement.type == "OUT")
        .group_by(Movement.variant_id)
        .all()
    )
    back_rows = dict(
        db.session.query(Movement.variant_id, func.coalesce(func.sum(Movement.qty), 0))
        .filter(Movement.client_id == client_id, Movement.type.in_(["IN", "DEFECT", "FULL"]))
        .group_by(Movement.variant_id)
        .all()
    )
    all_vids = set(out_rows) | set(back_rows)
    return {vid: int(out_rows.get(vid, 0)) - int(back_rows.get(vid, 0)) for vid in all_vids}


def _beer_totals_for_client(client_id: int) -> Tuple[float, float]:
    """
    Calcule :
      - liters_out_cum : total de litres livrés (OUT) sur des variantes volumétriques
      - beer_eur : somme TTC facturée (OUT) sur ces variantes
    Exclut ecocup maintenance et matériel seul.
    """
    q = (
        db.session.query(
            func.coalesce(func.sum(Movement.qty * func.coalesce(Variant.size_l, 0)), 0),
            func.coalesce(func.sum(Movement.qty * func.coalesce(Movement.unit_price_ttc, 0.0)), 0.0),
        )
        .join(Variant, Movement.variant_id == Variant.id)
        .join(Product, Variant.product_id == Product.id)
        .filter(Movement.client_id == client_id)
        .filter(Movement.type == "OUT")
    )

    name_lc = func.lower(Product.name)
    is_cup = (name_lc.like("%ecocup%") | name_lc.like("%eco cup%") | name_lc.like("%gobelet%"))
    is_maint = (name_lc.like("%lavage%") | name_lc.like("%perdu%") | name_lc.like("%perte%")
                | name_lc.like("%wash%") | name_lc.like("%clean%"))
    is_equip = and_((name_lc.like("%matériel%") | name_lc.like("%materiel%")), name_lc.like("%seul%"))

    q = q.filter(~and_(is_cup, is_maint)).filter(~is_equip)

    liters_out, beer_eur = q.one()
    return float(liters_out or 0.0), float(beer_eur or 0.0)


def summarize_client_detail(c: Client) -> Dict:
    """
    Vue 'détails client' consolidée.
    Renvoie (entre autres) :
      - liters_out_cum, beer_eur
      - deposit_eur total + split Ecocup/Fûts
      - cup_qty_in_play, keg_qty_in_play
    """
    dep_cup, qty_cup, dep_keg, qty_keg = compute_deposits_split(c.id)
    liters_out_cum, beer_eur = _beer_totals_for_client(c.id)
    deposit_total = (dep_cup or 0.0) + (dep_keg or 0.0)

    return {
        "liters_out_cum": round(liters_out_cum, 1),
        "beer_eur": round(beer_eur, 2),
        "deposit_eur": round(deposit_total, 2),
        "deposit_cup_eur": round(dep_cup or 0.0, 2),
        "deposit_keg_eur": round(dep_keg or 0.0, 2),
        "cup_qty_in_play": int(qty_cup or 0),
        "keg_qty_in_play": int(qty_keg or 0),
        "equipment": {},  # extension future
    }


def summarize_client_for_index(c: Client) -> Dict:
    """
    Résumé compact pour la page d’accueil.
    """
    dep_cup, qty_cup, dep_keg, qty_keg = compute_deposits_split(c.id)
    liters_out_cum, beer_eur = _beer_totals_for_client(c.id)

    open_total = int((qty_cup or 0) + (qty_keg or 0))  # (hors matériel)
    return {
        "client": c,
        "open_total": open_total,
        "cup_qty_in_play": int(qty_cup or 0),
        "keg_qty_in_play": int(qty_keg or 0),
        "deposit_eur": round((dep_cup or 0.0) + (dep_keg or 0.0), 2),
        "deposit_cup_eur": round(dep_cup or 0.0, 2),
        "deposit_keg_eur": round(dep_keg or 0.0, 2),
        "liters_out_cum": round(liters_out_cum or 0.0, 1),
        "beer_eur": round(beer_eur or 0.0, 2),
    }


def summarize_totals(cards: List[Dict]) -> Dict:
    """
    Totaux agrégés d’accueil à partir des cartes.
    """
    return dict(
        total_clients=len(cards),
        total_open=sum(c.get("open_total", 0) for c in cards),
        total_deposit=round(sum(c.get("deposit_eur", 0.0) for c in cards), 2),
        cups=sum(c.get("cup_qty_in_play", 0) for c in cards),
        kegs=sum(c.get("keg_qty_in_play", 0) for c in cards),
        liters=round(sum(c.get("liters_out_cum", 0.0) for c in cards), 1),
        beer_eur=round(sum(c.get("beer_eur", 0.0) for c in cards), 2),
    )


# -------------------- Réassort & Stock --------------------

def get_stock_items() -> List[Tuple[Variant, Product, int, int]]:
    """
    Liste le stock sous forme [(Variant, Product, inv_qty, min_qty), ...]
    inv_qty=0 et min_qty=0 par défaut si absents.
    Masque l’Ecocup maintenance (lavage/perdu).
    """
    inv_sq = db.session.query(
        Inventory.variant_id.label("vid"),
        Inventory.qty.label("inv_qty"),
    ).subquery()

    rr_sq = db.session.query(
        ReorderRule.variant_id.label("vid"),
        ReorderRule.min_qty.label("min_qty"),
    ).subquery()

    rows = (
        db.session.query(
            Variant,
            Product,
            func.coalesce(inv_sq.c.inv_qty, 0).label("inv_qty"),
            func.coalesce(rr_sq.c.min_qty, 0).label("min_qty"),
        )
        .join(Product, Variant.product_id == Product.id)
        .outerjoin(inv_sq, Variant.id == inv_sq.c.vid)
        .outerjoin(rr_sq, Variant.id == rr_sq.c.vid)
        .order_by(Product.name.asc(), Variant.size_l.asc())
        .all()
    )

    def _is_hidden(p: Product) -> bool:
        return _is_cup_maintenance(p)

    filtered = [(v, p, int(inv_qty or 0), int(min_qty or 0))
                for (v, p, inv_qty, min_qty) in rows
                if not _is_hidden(p)]
    return filtered


def compute_reorder_alerts() -> List[SimpleNamespace]:
    """
    Construit la liste d’alertes réassort pour le tableau de bord.
    On renvoie des objets avec .product et .variant pour coller aux macros Jinja :
      a.product.name / a.variant.size_l
    """
    alerts: List[SimpleNamespace] = []
    for (v, p, inv_qty, min_qty) in get_stock_items():
        # On ignore les variantes sans règle de réassort (min_qty <= 0)
        if (min_qty or 0) <= 0:
            continue
        if (inv_qty or 0) < (min_qty or 0):
            alerts.append(
                SimpleNamespace(
                    product=p,
                    variant=v,
                    inv_qty=int(inv_qty or 0),
                    min_qty=int(min_qty or 0),
                    missing=int((min_qty or 0) - (inv_qty or 0)),
                )
            )
    return alerts
