#!/usr/bin/env python3
"""
Read non-BOM, non-deactivated products directly from the Billbee API
for the given manufacturer codes.

Manufacturer filtering uses the tokens defined in mappings/products.yaml
(same logic as download_to_sheet.py / matches_filter).

Output: JSON object {"products": [...], "errors": [...]} to stdout.
Same product schema as gui_read_sheet_products.py.

Usage:
  python execution/gui_read_billbee_products.py --manufacturers TRX FRE
  python execution/gui_read_billbee_products.py --manufacturers TRX --category rucksack
"""
import argparse
import json
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_repo = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, _here)
sys.path.insert(0, _repo)
sys.path.insert(0, os.path.join(_repo, "billbee-python-client"))

from billbee_client import BillbeeClient  # noqa: E402
from mappings_loader import Mappings  # noqa: E402


def _get_title_de(product: dict) -> str:
    title_list = product.get("Title") or []
    for entry in title_list:
        if entry.get("LanguageCode") == "DE" and entry.get("Text"):
            return entry["Text"]
    return (title_list[0].get("Text") or "") if title_list else ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manufacturers", nargs="+", required=True,
                        help="Manufacturer codes, e.g. TRX FRE")
    parser.add_argument("--category", default="", help="Filter by Produktkategorie (substring)")
    parser.add_argument("--size",     default="", help="Filter by Produktgröße (substring)")
    parser.add_argument("--color",    default="", help="Filter by Produktfarbe (substring)")
    parser.add_argument("--variant",  default="", help="Filter by Produktvariante (substring)")
    args = parser.parse_args()

    client = BillbeeClient()
    mappings = Mappings()

    # Build a set of lowercase tokens for each requested manufacturer code
    mfr_token_map: dict[str, list[str]] = {}
    for code in args.manufacturers:
        mfr_info = mappings._manufacturers.get(code, {})
        tokens = [t.lower() for t in (mfr_info.get("tokens") or [])]
        tokens.append(code.lower())
        mfr_token_map[code] = tokens

    # Fetch custom field definitions once (needed to resolve custom field names)
    try:
        field_defs = client.get_custom_field_definitions()
    except Exception as exc:
        print(json.dumps({"products": [], "errors": [{"manufacturer": "*", "error": f"Failed to fetch custom field definitions: {exc}"}]}, ensure_ascii=False))
        return

    products: list[dict] = []
    errors: list[dict] = []

    try:
        for product in client.get_all_products():
            # Skip BOM products (Type == 2) and deactivated
            if int(product.get("Type") or 0) == 2:
                continue
            if product.get("IsDeactivated"):
                continue

            sku = str(product.get("SKU") or "").strip()
            if not sku:
                continue

            # Determine which manufacturer code this product belongs to
            sku_lower = sku.lower()
            mfr_native = str(product.get("Manufacturer") or "").lower()
            matched_code: str | None = None
            for code, tokens in mfr_token_map.items():
                if any(t in sku_lower or t in mfr_native for t in tokens):
                    matched_code = code
                    break
            if matched_code is None:
                continue

            # Extract custom fields
            custom_fields = product.get("CustomFields") or []
            cf_by_name: dict[str, str] = {}
            for cf in custom_fields:
                fid = cf.get("DefinitionId") or cf.get("Id")
                name = field_defs.get(fid)
                if name:
                    cf_by_name[name] = str(cf.get("Value") or "")

            category = cf_by_name.get("Produktkategorie", "")
            size     = cf_by_name.get("Produktgröße",     "")
            variant  = cf_by_name.get("Produktvariante",  "")
            color    = cf_by_name.get("Produktfarbe",     "")

            # Apply optional attribute filters (case-insensitive substring match)
            if args.category and args.category.lower() not in category.lower():
                continue
            if args.size     and args.size.lower()     not in size.lower():
                continue
            if args.color    and args.color.lower()    not in color.lower():
                continue
            if args.variant  and args.variant.lower()  not in variant.lower():
                continue

            stocks = product.get("Stocks") or []
            raw_stock = stocks[0].get("StockCurrent", "") if stocks else ""
            try:
                cached_stock: float | None = float(raw_stock) if raw_stock != "" else None
            except (ValueError, TypeError):
                cached_stock = None

            raw_target = stocks[0].get("StockDesired", "") if stocks else ""
            try:
                stock_target: float | None = float(raw_target) if raw_target != "" else None
            except (ValueError, TypeError):
                stock_target = None

            products.append({
                "sku":          sku,
                "title":        _get_title_de(product),
                "billbeeId":    product.get("Id", ""),
                "category":     category,
                "size":         size,
                "variant":      variant,
                "color":        color,
                "manufacturer": matched_code,
                "cachedStock":  cached_stock,
                "stockTarget":  stock_target,
            })
    except Exception as exc:
        errors.append({"manufacturer": "*", "error": str(exc)})

    print(json.dumps({"products": products, "errors": errors}, ensure_ascii=False))


if __name__ == "__main__":
    main()
