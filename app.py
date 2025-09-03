@app.route("/movement/wizard", methods=["GET", "POST"])
def movement_wizard():
    # État du wizard en session
    if "wiz" not in session:
        session["wiz"] = {}
    wiz = session["wiz"]

    # Étape courante (par défaut 1)
    try:
        step = int(request.args.get("step", 1))
    except Exception:
        step = 1

    # ---------- ÉTAPE 1 : choix client / type / date ----------
    if step == 1:
        if request.method == "POST":
            wiz["client_id"] = request.form.get("client_id", type=int)
            wiz["type"] = request.form.get("type")
            wiz["date"] = request.form.get("date")  # AAAA-MM-JJ (optionnel)
            session.modified = True
            if not wiz.get("client_id") or not wiz.get("type"):
                flash("Sélectionne un client et un type de mouvement.", "warning")
                return render_template(
                    "movement_wizard.html",
                    step=1,
                    clients=Client.query.order_by(Client.name.asc()).all(),
                    rows=[],
                    wiz=wiz,
                )
            return redirect(url_for("movement_wizard", step=2))
        # GET
        clients = Client.query.order_by(Client.name.asc()).all()
        return render_template("movement_wizard.html", step=1, clients=clients, rows=[], wiz=wiz)

    # ---------- ÉTAPE 2 : choix des produits/variantes ----------
    if step == 2:
        if request.method == "POST":
            # Autoriser 'variant_id' (checkboxes) ou 'variant_ids' (fallback)
            vids = request.form.getlist("variant_id") or request.form.getlist("variant_ids")
            if not vids:
                flash("Sélectionne au moins un produit.", "warning")
                return redirect(url_for("movement_wizard", step=2))
            try:
                wiz["variant_ids"] = [int(v) for v in vids]
            except Exception:
                wiz["variant_ids"] = []
            session.modified = True
            return redirect(url_for("movement_wizard", step=3))

        # GET : liste de variantes
        clients = Client.query.order_by(Client.name.asc()).all()
        base_q = (
            db.session.query(Variant, Product)
            .join(Product, Variant.product_id == Product.id)
            .order_by(Product.name, Variant.size_l)
        )
        if wiz.get("type") == "IN" and wiz.get("client_id"):
            # Limiter aux variantes réellement en jeu chez ce client + “Matériel seul”
            def _open_kegs_by_variant(client_id: int):
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

            open_map = _open_kegs_by_variant(wiz["client_id"])
            allowed_ids = {vid for vid, openq in open_map.items() if openq > 0}

            equip_ids = set(
                vid for (vid,) in (
                    db.session.query(Variant.id)
                    .join(Product, Variant.product_id == Product.id)
                    .filter(
                        (
                            Product.name.ilike("%matériel%seul%")
                            | Product.name.ilike("%materiel%seul%")
                            | Product.name.ilike("%Matériel seul%")
                            | Product.name.ilike("%Materiel seul%")
                        )
                    )
                    .all()
                )
            )

            final_ids = list(allowed_ids | equip_ids)
            if final_ids:
                base_q = base_q.filter(Variant.id.in_(final_ids))

        rows = base_q.all()  # rows = list of (Variant, Product)
        return render_template("movement_wizard.html", step=2, clients=clients, rows=rows, wiz=wiz)

    # ---------- ÉTAPE 3 : saut direct vers 4 (pas d'écran intermédiaire) ----------
    if step == 3:
        # Optionnel : si tu as un écran de récap ici, rends-le; sinon on file à 4
        return redirect(url_for("movement_wizard", step=4))

    # ---------- ÉTAPE 4 : saisie quantités/prix/consignes + enregistrement ----------
    if step == 4:
        if request.method == "POST":
            if (wiz.get("client_id") is None) or (wiz.get("type") is None):
                flash("Informations incomplètes.", "warning")
                return redirect(url_for("movement_wizard", step=1))

            # Date finale
            if wiz.get("date"):
                try:
                    y, m_, d2 = [int(x) for x in wiz["date"].split("-")]
                    created_at = datetime.combine(date(y, m_, d2), time(hour=12))
                except Exception:
                    created_at = U.now_utc()
            else:
                created_at = U.now_utc()

            variant_ids = request.form.getlist("variant_id")
            qtys = request.form.getlist("qty")
            unit_prices = request.form.getlist("unit_price_ttc")
            deposits = request.form.getlist("deposit_per_keg")
            notes = request.form.get("notes") or None

            # Matériel prêté/repris → notes
            t = request.form.get("eq_tireuse", type=int)
            c2 = request.form.get("eq_co2", type=int)
            cpt = request.form.get("eq_comptoir", type=int)
            ton = request.form.get("eq_tonnelle", type=int)
            equip_parts = []
            if t:   equip_parts.append(f"tireuse={t}")
            if c2:  equip_parts.append(f"co2={c2}")
            if cpt: equip_parts.append(f"comptoir={cpt}")
            if ton: equip_parts.append(f"tonnelle={ton}")
            notes = (";".join(equip_parts) + (";" + notes if notes else "")) or None

            client_id = int(wiz["client_id"])
            mtype = wiz["type"]

            # Contrôle des retours vs enjeu
            def _open_kegs_by_variant(client_id: int):
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

            violations = []
            open_map = _open_kegs_by_variant(client_id) if mtype == "IN" else {}

            for i, vid in enumerate(variant_ids):
                try:
                    vid_int = int(vid)
                except Exception:
                    continue

                try:
                    qty_int = int(qtys[i])
                except Exception:
                    qty_int = 0

                up = None
                if i < len(unit_prices) and unit_prices[i] not in ("", None):
                    try:
                        up = float(unit_prices[i])
                    except Exception:
                        up = None

                dep = None
                if i < len(deposits) and deposits[i] not in ("", None):
                    try:
                        dep = float(deposits[i])
                    except Exception:
                        dep = None

                v = Variant.query.get(vid_int)
                if not v:
                    continue

                pname = (v.product.name if v and v.product else "") or ""
                is_equipment_only = "matériel" in pname.lower() and "seul" in pname.lower()
                if is_equipment_only:
                    qty_int = 0
                    up = 0.0
                    dep = 0.0
                else:
                    if up is None and (v.price_ttc is not None):
                        up = v.price_ttc
                    if dep is None:
                        # Ecocup = 1 €, sinon 30 €
                        dep = U.default_deposit_for_product(v.product)

                    if mtype == "IN":
                        open_q = int(open_map.get(vid_int, 0))
                        if open_q <= 0 or qty_int > open_q:
                            label = f"{v.product.name} — {v.size_l} L"
                            violations.append((label, open_q))
                            continue

                mv = Movement(
                    client_id=client_id,
                    variant_id=vid_int,
                    type=mtype,
                    qty=qty_int,
                    unit_price_ttc=up,
                    deposit_per_keg=dep,
                    notes=notes,
                    created_at=created_at,
                )
                db.session.add(mv)

                # MAJ inventaire bar (OUT / FULL)
                inv = U.get_or_create_inventory(vid_int)
                if mtype == "OUT":
                    inv.qty = (inv.qty or 0) - qty_int
                elif mtype == "FULL":
                    inv.qty = (inv.qty or 0) + qty_int

            if violations:
                text = "Certains retours dépassent l’enjeu autorisé : " + ", ".join(
                    f"{lab} (max {q})" for lab, q in violations
                )
            #    flash(text, "warning")
            #    return redirect(url_for("movement_wizard", step=2))
            # ↑ si tu veux bloquer, dé-commente les deux lignes ci-dessus.

            db.session.commit()
            flash("Saisie enregistrée.", "success")
            session.pop("wiz", None)
            return redirect(url_for("client_detail", client_id=client_id))

        # GET -> afficher le formulaire de l’étape 4 (on charge les variantes sélectionnées)
        selected = []
        for vid in wiz.get("variant_ids", []):
            v = Variant.query.get(vid)
            if v:
                selected.append((v, v.product))
        return render_template("movement_wizard.html", step=4, wiz=wiz, selected=selected)
