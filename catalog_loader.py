# catalog_loader.py
import csv
import os
from sqlalchemy import select
from models import db, Product, Equipment

# Règles catalogue (métier)
FORBIDDEN_30L = {"Coreff Blanche", "Coreff Rousse", "Cidre Brut"}  # pas de 30L
ONLY_22L = {"Coreff Ambrée"}  # uniquement 22L

def _row_bool(v, default=True):
    try:
        return bool(int(str(v).strip()))
    except Exception:
        return bool(default)

def _row_float(v, default=0.0):
    try:
        return float(str(v).replace(",", "."))
    except Exception:
        return float(default)

def _valid_volume(name: str, volume_l: float) -> bool:
    # Ambrée uniquement 22L
    if name in ONLY_22L:
        return abs(volume_l - 22.0) < 1e-6
    # Interdiction du 30L pour certaines références
    if name in FORBIDDEN_30L and abs(volume_l - 30.0) < 1e-6:
        return False
    return True

def _upsert_product(row):
    name = row["name"].strip()
    brand = (row.get("brand") or "").strip()
    volume_l = _row_float(row.get("volume_l"), 0)
    price_eur = _row_float(row.get("price_eur"), 0)
    deposit_eur = _row_float(row.get("deposit_eur"), 30)
    is_active = _row_bool(row.get("is_active"), True)
    notes = (row.get("notes") or "").strip()

    if not _valid_volume(name, volume_l):
        # On ignore poliment les lignes hors des règles
        return

    existing = db.session.execute(
        select(Product).where(
            Product.name == name,
            Product.brand == brand,
            Product.volume_l == volume_l,
        )
    ).scalar_one_or_none()

    if existing:
        existing.price_eur = price_eur
        existing.deposit_eur = deposit_eur
        existing.is_active = is_active
        existing.notes = notes
    else:
        db.session.add(
            Product(
                name=name,
                brand=brand,
                volume_l=volume_l,
                price_eur=price_eur,
                deposit_eur=deposit_eur,
                is_active=is_active,
                notes=notes,
            )
        )

def _upsert_equipment(row):
    name = row["name"].strip()
    category = (row.get("category") or "").strip()
    deposit_eur = _row_float(row.get("deposit_eur"), 0)
    is_active = _row_bool(row.get("is_active"), True)
    notes = (row.get("notes") or "").strip()

    existing = db.session.execute(
        select(Equipment).where(
            Equipment.name == name,
            Equipment.category == category,
        )
    ).scalar_one_or_none()

    if existing:
        existing.deposit_eur = deposit_eur
        existing.is_active = is_active
        existing.notes = notes
    else:
        db.session.add(
            Equipment(
                name=name,
                category=category,
                deposit_eur=deposit_eur,
                is_active=is_active,
                notes=notes,
            )
        )

def _load_csv(path, handler):
    if not os.path.exists(path):
        return 0
    count = 0
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            handler(row)
            count += 1
    return count

def load_initial_catalog_if_empty():
    """
    À appeler au démarrage, dans un app_context & après db.create_all().
    Ne fait l'import que si les tables sont vides ; sinon, effectue un upsert
    sur les lignes des CSV (utile pour MAJ du catalogue).
    """
    # PRODUITS
    prod_count = db.session.scalar(select(db.func.count(Product.id))) or 0
    if prod_count == 0:
        _load_csv(os.path.join("data", "products.csv"), _upsert_product)
    else:
        # Mise à jour douce si CSV modifiés
        _load_csv(os.path.join("data", "products.csv"), _upsert_product)

    # MATERIEL
    equip_count = db.session.scalar(select(db.func.count(Equipment.id))) or 0
    if equip_count == 0:
        _load_csv(os.path.join("data", "equipment.csv"), _upsert_equipment)
    else:
        _load_csv(os.path.join("data", "equipment.csv"), _upsert_equipment)

    db.session.commit()
