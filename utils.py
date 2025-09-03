from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, List
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
    ecocup: int = 0   # prêt de gobelets via notes (optionnel)


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
        # Parsing permissif
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
    return ("ecocup" in name) or ("éco" in name and "cup" in name) or ("gobelet" in name)


def effective_deposit(m: Movement, product_name: Optional[str] = None) -> float:
    """
    Consigne effective:
      - si présente sur le mouvement => utiliser cette valeur
      - sinon: 1,00 € pour 'Ecocup', 30,00 € pour le reste (fûts)
    Param 'product_name' optionnel pour compat avec anciens appels.
    """
    if m.deposit_per_keg is not None:
        try:
            return float(m.deposit_per_keg)
        except Exception:
            pass
    if product_name and is_ecocup_product(product_name):
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
    rows: List[Dict] = []
    beer_eur = 0.0
    deposit_eur = 0.0
    equipment = Equipment()
    liters_out_cum = 0.0
    last_date = None

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

        if last_date is None:
            last_date = m.created_at  # tri desc → première itération = plus récente

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

    # Totaux simples par type de mouvement
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
        last_movement_date=last_date,
    )


# --- Fonctions attendues par app.py / templates ---

def summarize_client_for_index(c: Client):
    """
    Version compacte pour la page d’accueil.
    Retourne un SimpleNamespace accessible par attributs en Jinja.
    Champs disponibles:
      client, client_id, client_name, kegs, beer_eur, deposit_eur,
      equipment, liters_out_cum, last_movement_date
    """
    d = summarize_client_detail(c)
    payload = {
        "client": c,
        "client_id": c.id,
        "client_name": c.name,
        "kegs": d.get("kegs", 0),
        "beer_eur": d.get("beer_eur", 0.0),
        "deposit_eur": d.get("deposit_eur", 0.0),
        "equipment": d.get("equipment"),
        "liters_out_cum": d.get("liters_out_cum", 0.0),
        "last_movement_date": d.get("last_movement_date"),
    }
    return SimpleNamespace(**payload)


def summarize_totals(cards: List[SimpleNamespace]):
    """
    Agrège les totaux pour l’en-tête de l’accueil.
    Compatible avec les templates existants qui accèdent à .beer_eur, .deposit_eur, .kegs, .equipment, .liters_out_cum
    """
    total_beer = 0.0
    total_dep = 0.0
    total_kegs = 0
    total_liters = 0.0
    eq = Equipment()

    for c in cards or []:
        total_beer += float(getattr(c, "beer_eur", 0.0) or 0.0)
        total_dep += float(getattr(c, "deposit_eur", 0.0) or 0.0)
        total_kegs += int(getattr(c, "kegs", 0) or 0)
        total_liters += float(getattr(c, "liters_out_cum", 0.0) or 0.0)
        ceq: Equipment = getattr(c, "equipment", None)
        if isinstance(ceq, Equipment):
            combine_equipment(eq, ceq, +1)

    return SimpleNamespace(
        beer_eur=round(total_beer, 2),
        deposit_eur=round(total_dep, 2),
        kegs=int(total_kegs),
        liters_out_cum=round(total_liters, 2),
        equipment=eq,
    )


def get_stock_items():
    """
    Liste des items de stock (par variante) pour /stock.
    Champs: product_id, product_name, variant_id, size_l, qty, reorder_min, below_min, status
    """
    # Stock agrégé
    inv = dict(
        db.session.query(Inventory.variant_id, func.coalesce(func.sum(Inventory.qty), 0))
        .group_by(Inventory.variant_id)
        .all()
    )

    # Règles de réassort (optionnelles)
    rules = {r.variant_id: r.min_qty for r in ReorderRule.query.all()}

    rows: List[SimpleNamespace] = []
    q = (
        db.session.query(Variant, Product)
        .join(Product, Variant.product_id == Product.id)
        .order_by(Product.name.asc(), Variant.size_l.asc())
    )
    for v, p in q.all():
        qty = int(inv.get(v.id, 0) or 0)
        min_qty = rules.get(v.id)
        below = (min_qty is not None) and (qty < int(min_qty))
        status = "LOW" if below else "OK"
        rows.append(SimpleNamespace(
            product_id=p.id,
            product_name=p.name,
            variant_id=v.id,
            size_l=getattr(v, "size_l", None),
            qty=qty,
            reorder_min=min_qty,
            below_min=bool(below),
            status=status,
        ))

    return rows


def compute_reorder_alerts():
    """
    Calcule les alertes de réassort attendues par l’accueil.
    Retourne UNIQUEMENT les variantes en dessous du seuil défini dans ReorderRule.
    Chaque alerte est un SimpleNamespace avec:
      - product_id, product_name
      - variant_id, size_l
      - qty (stock courant)
      - min_qty (seuil)
      - delta (min_qty - qty, >0)
      - status = 'LOW'
    Si aucune règle n’est définie → renvoie [] (pas d’alertes).
    """
    # Récup stock agrégé
    inv = dict(
        db.session.query(Inventory.variant_id, func.coalesce(func.sum(Inventory.qty), 0))
        .group_by(Inventory.variant_id)
        .all()
    )
    # Règles
    rules = {r.variant_id: int(r.min_qty) for r in ReorderRule.query.all()}
    if not rules:
        return []

    alerts: List[SimpleNamespace] = []

    q = (
        db.session.query(Variant, Product)
        .join(Product, Variant.product_id == Product.id)
    )
    for v, p in q.all():
        if v.id not in rules:
            continue
        qty = int(inv.get(v.id, 0) or 0)
        min_qty = rules[v.id]
        if qty < min_qty:
            delta = int(min_qty - qty)
            alerts.append(SimpleNamespace(
                product_id=p.id,
                product_name=p.name,
                variant_id=v.id,
                size_l=getattr(v, "size_l", None),
                qty=qty,
                min_qty=min_qty,
                delta=delta,
                status="LOW",
            ))

    # Tri: les plus urgents d’abord (delta le plus grand), puis nom produit
    alerts.sort(key=lambda a: (-a.delta, a.product_name or "", a.size_l or 0))
    return alerts


# --- Helpers d'affichage tolérants (facultatif, pour éviter d'autres AttributeError côté templates) ---

def format_eur(x):
    try:
        return f"{float(x):.2f} €"
    except Exception:
        return "0.00 €"

def fmt_qty(x):
    try:
        return int(x)
    except Exception:
        return 0

def fmt_date(dt):
    try:
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return ""
