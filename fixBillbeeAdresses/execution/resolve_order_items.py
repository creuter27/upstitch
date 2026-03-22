"""
Resolve order items into physical components, expanding BOM/listing products.

Billbee product types:
  Type 1 — Physical product (standalone)
  Type 2 — Listing / BOM product (maps to physical components via BillOfMaterial)

For each order item:
  1. Look up the product in the cache (by ArticleId or SKU)
  2. If the product is a BOM (Type 2 or has BillOfMaterial entries), expand it into
     its physical components
  3. For each physical product: extract manufacturer, Produktkategorie, Produktgröße

Usage:
    from execution.resolve_order_items import resolve_items
    items = resolve_items(order, by_id, by_sku, field_defs)
    # items: list of {"sku", "manufacturer", "category", "size", "quantity", "title"}
"""


def _get_title(product: dict, fallback: str = "") -> str:
    titles = product.get("Title") or []
    if isinstance(titles, list):
        for t in titles:
            if isinstance(t, dict):
                text = t.get("Text") or t.get("text") or ""
                if text:
                    return text
        return fallback
    if isinstance(titles, dict):
        return next(iter(titles.values()), fallback)
    return str(titles) if titles else fallback


def _extract_attrs(product: dict, field_defs: dict) -> tuple[str, str, str]:
    """
    Return (manufacturer, category, size) from a product dict.

    manufacturer: product["Manufacturer"] field, or first dash-segment of SKU as fallback.
    category:     custom field "Produktkategorie"
    size:         custom field "Produktgröße"
    """
    manufacturer = (product.get("Manufacturer") or "").strip()
    if not manufacturer:
        sku = product.get("SKU") or ""
        parts = sku.split("-")
        manufacturer = parts[0].strip() if parts else ""

    category = ""
    size = ""
    for cf in product.get("CustomFields") or []:
        def_id = cf.get("DefinitionId") or cf.get("Id")
        name = field_defs.get(int(def_id)) if def_id is not None else ""
        if name == "Produktkategorie":
            category = (cf.get("Value") or "").strip()
        elif name == "Produktgröße":
            size = (cf.get("Value") or "").strip()

    return manufacturer, category, size


def resolve_items(
    order: dict,
    by_id: dict,
    by_sku: dict,
    field_defs: dict,
) -> list[dict]:
    """
    Expand an order's items into a flat list of physical components.

    Parameters
    ----------
    order : dict
        Full Billbee order dict (as returned by get_orders()).
    by_id : dict[int, product_dict]
        Product cache keyed by Billbee article ID.
    by_sku : dict[str, product_dict]
        Product cache keyed by SKU.
    field_defs : dict[int, str]
        Custom field definition mapping {def_id: field_name}.

    Returns
    -------
    list[dict] each with keys:
        sku, manufacturer, category, size, quantity, title
    Items with no identifiable product are silently skipped.
    """
    raw_items = order.get("OrderItems") or []
    resolved: list[dict] = []

    for item in raw_items:
        # Skip coupons and zero-quantity items
        if item.get("IsCoupon"):
            continue
        qty = float(item.get("Quantity") or 0)
        if qty <= 0:
            continue

        # Locate the product in the cache
        sold = item.get("Product") or {}
        article_id = sold.get("Id")
        sku = (sold.get("SKU") or "").strip()

        product = None
        if article_id:
            product = by_id.get(int(article_id))
        if product is None and sku:
            product = by_sku.get(sku)

        if product is None:
            # Product not in cache — skip silently (could have been deleted or not synced)
            continue

        # Check if this is a BOM / listing product
        bom = product.get("BillOfMaterial") or []
        is_listing = product.get("Type") == 2 or bool(bom)

        if is_listing and bom:
            # Expand into physical BOM components
            for component in bom:
                comp_id = component.get("ArticleId")
                comp_sku = (component.get("SKU") or "").strip()
                comp_qty = float(component.get("Amount") or 1)

                comp_product = None
                if comp_id:
                    comp_product = by_id.get(int(comp_id))
                if comp_product is None and comp_sku:
                    comp_product = by_sku.get(comp_sku)

                if comp_product is None:
                    # BOM component not found in cache — skip
                    print(f"[resolve] Warning: BOM component {comp_sku or comp_id} not in cache")
                    continue

                mfr, cat, sz = _extract_attrs(comp_product, field_defs)
                resolved.append({
                    "sku": comp_product.get("SKU") or comp_sku,
                    "manufacturer": mfr,
                    "category": cat,
                    "size": sz,
                    "quantity": qty * comp_qty,
                    "title": _get_title(comp_product, fallback=comp_sku),
                })
        else:
            # Physical product — use directly
            mfr, cat, sz = _extract_attrs(product, field_defs)
            resolved.append({
                "sku": product.get("SKU") or sku,
                "manufacturer": mfr,
                "category": cat,
                "size": sz,
                "quantity": qty,
                "title": _get_title(product, fallback=sku),
            })

    return resolved
