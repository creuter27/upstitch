#!/usr/bin/env python3
"""
Read SKUs and quantities from any Google Sheet tab and resolve Billbee IDs from
the manufacturer's ProductList.  Billbee live-stock is NOT fetched here; the
frontend calls the stock endpoint separately so the table can appear immediately.

Args:
  --sheet        Source Google Sheet name
  --tab          Source tab name
  --sku-col      Column name for SKU (default: SKU)
  --qty-col      Column name for quantity (default: Qty)
  --manufacturer Manufacturer code (e.g. TRX) — used to look up the ProductList

Output: JSON {"items": [{sku, billbeeId, qty, billbeeStock: null}], "errors": [...]}
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
    parser.add_argument("--sheet",        required=True, help="Source Google Sheet name")
    parser.add_argument("--tab",          required=True, help="Source tab name")
    parser.add_argument("--sku-col",      default="SKU", help="Column name for SKU")
    parser.add_argument("--qty-col",      default="Qty", help="Column name for quantity")
    parser.add_argument("--manufacturer", required=True, help="Manufacturer code for ProductList lookup")
    args = parser.parse_args()

    errors: list[dict] = []
    items: list[dict] = []

    # 1. Read source sheet / tab
    try:
        ss = open_sheet_by_name(args.sheet)
        ws = ss.worksheet(args.tab)
        rows = ws.get_all_records()
    except Exception as exc:
        print(json.dumps({"items": [], "errors": [{"error": f"Source sheet error: {exc}"}]},
                         ensure_ascii=False))
        return

    # 2. Load SKU → billbeeId map from manufacturer's ProductList
    sku_to_billbee: dict[str, int] = {}
    try:
        mfr_sheet_name = f"Billbee Artikelmanager {args.manufacturer}"
        mfr_ss = open_sheet_by_name(mfr_sheet_name)
        tab_names = [w.title for w in mfr_ss.worksheets()]
        pl_tab = "ProductList" if "ProductList" in tab_names else "downloaded"
        pl_rows = mfr_ss.worksheet(pl_tab).get_all_records()
        for r in pl_rows:
            sku = str(r.get("SKU", "") or "").strip()
            billbee_id = r.get("Id", "")
            if sku and billbee_id:
                try:
                    sku_to_billbee[sku] = int(billbee_id)
                except (ValueError, TypeError):
                    pass
    except Exception as exc:
        errors.append({"error": f"ProductList error for {args.manufacturer}: {exc}"})

    # 3. Parse source rows
    for row in rows:
        sku = str(row.get(args.sku_col, "") or "").strip()
        if not sku:
            continue

        qty_raw = row.get(args.qty_col, "")
        # Skip rows with empty or zero quantity (nothing to add/subtract/set)
        if qty_raw == "" or qty_raw is None:
            continue
        try:
            qty = float(qty_raw)
        except (ValueError, TypeError):
            errors.append({"sku": sku, "error": f"Invalid qty: {qty_raw!r}"})
            continue
        if qty == 0:
            continue

        billbee_id = sku_to_billbee.get(sku)
        if billbee_id is None:
            errors.append({"sku": sku, "error": "SKU not found in ProductList"})
            continue

        items.append({"sku": sku, "billbeeId": billbee_id, "qty": qty, "billbeeStock": None})

    print(json.dumps({"items": items, "errors": errors}, ensure_ascii=False))


if __name__ == "__main__":
    main()
