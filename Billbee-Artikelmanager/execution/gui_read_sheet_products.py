#!/usr/bin/env python3
"""
Read non-BOM, non-deactivated products from Google Sheet(s) for the given manufacturers.

Each manufacturer's sheet is "Billbee Artikelmanager {CODE}" and is expected to have
a "downloaded" tab with columns from download_to_sheet.py.

Output: JSON object {"products": [...], "errors": [...]} to stdout.

Usage:
  python execution/gui_read_sheet_products.py --manufacturers TRX FRE
  python execution/gui_read_sheet_products.py --manufacturers TRX --category rucksack --size big
"""
import argparse
import json
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_repo = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, _here)
sys.path.insert(0, os.path.join(_repo, "google-client"))

from google_sheets_client import open_sheet_by_name  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manufacturers", nargs="+", required=True,
                        help="Manufacturer codes, e.g. TRX FRE")
    parser.add_argument("--category", default="", help="Filter by Produktkategorie (substring)")
    parser.add_argument("--size",     default="", help="Filter by Produktgröße (substring)")
    parser.add_argument("--color",    default="", help="Filter by Produktfarbe (substring)")
    parser.add_argument("--variant",  default="", help="Filter by Produktvariante (substring)")
    args = parser.parse_args()

    products: list[dict] = []
    errors: list[dict] = []

    for mfr in args.manufacturers:
        sheet_name = f"Billbee Artikelmanager {mfr}"
        try:
            ss = open_sheet_by_name(sheet_name)
            tab_names = [ws.title for ws in ss.worksheets()]
            tab = "ProductList" if "ProductList" in tab_names else "downloaded"
            rows = ss.worksheet(tab).get_all_records()
        except Exception as exc:
            errors.append({"manufacturer": mfr, "error": str(exc)})
            continue

        for row in rows:
            # Skip BOM / listing products (Type=2)
            if str(row.get("IsBom", "")).strip().upper() == "TRUE":
                continue
            # Skip deactivated products
            if str(row.get("IsDeactivated", "")).strip().upper() == "TRUE":
                continue

            sku = str(row.get("SKU", "") or "").strip()
            if not sku:
                continue

            category = str(row.get("Custom Field Produktkategorie", "") or "")
            size     = str(row.get("Custom Field Produktgröße",      "") or "")
            variant  = str(row.get("Custom Field Produktvariante",   "") or "")
            color    = str(row.get("Custom Field Produktfarbe",      "") or "")

            # Apply optional attribute filters (case-insensitive substring match)
            if args.category and args.category.lower() not in category.lower():
                continue
            if args.size     and args.size.lower()     not in size.lower():
                continue
            if args.color    and args.color.lower()    not in color.lower():
                continue
            if args.variant  and args.variant.lower()  not in variant.lower():
                continue

            # Cached stock from the sheet (may be stale; live values fetched separately)
            raw_stock = row.get("Stock current Standard", "")
            try:
                cached_stock: float | None = float(raw_stock) if raw_stock != "" else None
            except (ValueError, TypeError):
                cached_stock = None

            raw_target = row.get("Stock target Standard", "")
            try:
                stock_target: float | None = float(raw_target) if raw_target != "" else None
            except (ValueError, TypeError):
                stock_target = None

            products.append({
                "sku":          sku,
                "title":        str(row.get("Title DE", "") or ""),
                "billbeeId":    row.get("Id", ""),
                "category":     category,
                "size":         size,
                "variant":      variant,
                "color":        color,
                "manufacturer": mfr,
                "cachedStock":  cached_stock,
                "stockTarget":  stock_target,
            })

    print(json.dumps({"products": products, "errors": errors}, ensure_ascii=False))


if __name__ == "__main__":
    main()
