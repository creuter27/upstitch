#!/usr/bin/env python3
"""
Preview the Billbee stock update from the newest (or specified) order tab
in '{MFR} Orders'.

Reads all rows where 'add to Billbee stock' is checked, Qty > 0, and
'Billbee Id' is present, then fetches live Billbee stock for each.

Output: JSON {"tab": "...", "items": [...], "errors": [...]}

Each item:
  sku              – product SKU
  billbeeId        – Billbee product ID
  billbeeStock     – live stock level in Billbee (null on fetch error)
  sheetStockCurrent – Stock current column from the order tab
  sheetStockTarget  – Stock target column from the order tab
  qty              – ordered quantity (Qty column)
  newStock         – billbeeStock + qty (null if billbeeStock is null)

Usage:
  python execution/gui_add_stock_preview.py --manufacturer TRX
  python execution/gui_add_stock_preview.py --manufacturer TRX --tab "Order 2026-03-20"
"""
import argparse
import json
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_repo = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, _here)
sys.path.insert(0, os.path.join(_repo, "billbee-python-client"))
sys.path.insert(0, os.path.join(_repo, "google-client"))

from billbee_client import BillbeeClient          # noqa: E402
from google_sheets_client import open_sheet_by_name  # noqa: E402


def orders_sheet_name(mfr: str) -> str:
    return f"{mfr} Orders"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manufacturer", required=True,
                        help="Manufacturer code, e.g. TRX")
    parser.add_argument("--tab", default=None,
                        help="Order tab name (default: newest 'Order YYYY-MM-DD' tab)")
    args = parser.parse_args()

    mfr = args.manufacturer.upper()
    oname = orders_sheet_name(mfr)
    errors: list[dict] = []

    try:
        oss = open_sheet_by_name(oname)
    except Exception as exc:
        print(json.dumps({
            "tab": "",
            "items": [],
            "errors": [{"error": f"Could not open '{oname}': {exc}"}],
        }, ensure_ascii=False))
        return

    if args.tab:
        tab = args.tab
    else:
        tab_names = [ws.title for ws in oss.worksheets()]
        order_tabs = sorted(
            [t for t in tab_names if t.startswith("Order 20")],
            reverse=True,
        )
        if not order_tabs:
            print(json.dumps({
                "tab": "",
                "items": [],
                "errors": [{"error": f"No 'Order YYYY-MM-DD' tabs found in '{oname}'"}],
            }, ensure_ascii=False))
            return
        tab = order_tabs[0]

    try:
        rows = oss.worksheet(tab).get_all_records()
    except Exception as exc:
        print(json.dumps({
            "tab": tab,
            "items": [],
            "errors": [{"error": f"Could not read tab '{tab}': {exc}"}],
        }, ensure_ascii=False))
        return

    to_preview = [
        r for r in rows
        if str(r.get("add to Billbee stock") or "").strip().upper() in ("TRUE", "1", "YES")
        and str(r.get("Qty") or "").strip() not in ("", "0")
        and str(r.get("Billbee Id") or "").strip()
    ]

    if not to_preview:
        print(json.dumps({"tab": tab, "items": [], "errors": []}, ensure_ascii=False))
        return

    client = BillbeeClient()
    items: list[dict] = []

    for row in to_preview:
        sku       = str(row.get("SKU") or "").strip()
        billbee_id = str(row.get("Billbee Id") or "").strip()
        try:
            qty = int(float(str(row.get("Qty") or "0")))
        except (ValueError, TypeError):
            qty = 0
        if qty <= 0 or not sku:
            continue

        try:
            sheet_current: float | None = float(str(row.get("Stock current") or "").strip() or "0")
            sheet_current = int(round(sheet_current))
        except (ValueError, TypeError):
            sheet_current = None

        try:
            sheet_target: float | None = float(str(row.get("Stock target") or "").strip() or "0")
            sheet_target = int(round(sheet_target))
        except (ValueError, TypeError):
            sheet_target = None

        try:
            product    = client.get_product_by_id(int(billbee_id))
            stocks     = product.get("Stocks") or []
            live_stock: float | None = float(stocks[0].get("StockCurrent") or 0) if stocks else 0.0
        except Exception as exc:
            errors.append({"sku": sku, "error": f"Could not fetch stock: {exc}"})
            live_stock = None

        new_stock = (live_stock + qty) if live_stock is not None else None

        items.append({
            "sku":              sku,
            "billbeeId":        int(billbee_id),
            "billbeeStock":     live_stock,
            "sheetStockCurrent": sheet_current,
            "sheetStockTarget":  sheet_target,
            "qty":              qty,
            "newStock":         new_stock,
        })

    print(json.dumps({"tab": tab, "items": items, "errors": errors}, ensure_ascii=False))


if __name__ == "__main__":
    main()
