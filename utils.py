# --- utils.py : ajout ---
from sqlalchemy import func
from models import db, Movement, Variant, Product

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

    # On retourne les montants (peuvent être négatifs si "sur-retours" historiques)
    return (cup_eur, cup_qty, keg_eur, keg_qty)
