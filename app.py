# ---------- Data guard : crée le produit “Ecocup” générique si manquant ----------
def _ensure_ecocup_simple():
    """
    Si aucun produit 'Ecocup' générique n'existe (hors 'lavage/perdu/perte/wash/clean'),
    on le crée + 1 variante avec size_l=1 (NOT NULL) pour qu'il apparaisse
    dans Catalogue/Saisie/Stock. On normalise aussi les variantes existantes
    qui auraient size_l=NULL.
    """
    # Détecter "ecocup générique" = (ecocup|eco cup|gobelet) ET PAS (lavage|perdu|perte|wash|clean)
    name_lc = func.lower(Product.name)
    is_cup = (name_lc.like("%ecocup%") | name_lc.like("%eco cup%") | name_lc.like("%gobelet%"))
    is_maintenance = (
        name_lc.like("%lavage%")
        | name_lc.like("%perdu%")
        | name_lc.like("%perte%")
        | name_lc.like("%wash%")
        | name_lc.like("%clean%")
    )

    # Chercher un produit ecocup "simple"
    p = Product.query.filter(is_cup, ~is_maintenance).first()
    if not p:
        # Créer le produit générique
        p = Product(name="Ecocup")
        db.session.add(p)
        db.session.flush()  # pour obtenir p.id

    # 1) Backfill: si des variantes de ce produit ont size_l NULL, les passer à 1
    vs = Variant.query.filter_by(product_id=p.id).all()
    changed = False
    for v in vs:
        if v.size_l is None:
            v.size_l = 1     # NOT NULL garanti
            if v.price_ttc is None:
                v.price_ttc = 0.0
            changed = True
    if changed:
        db.session.flush()

    # 2) S’assurer qu’il y a au moins UNE variante valide
    v_any = Variant.query.filter_by(product_id=p.id).first()
    if not v_any:
        v = Variant(product_id=p.id, size_l=1, price_ttc=0.0)
        db.session.add(v)

    db.session.commit()
