#!/usr/bin/env python3
"""
Apply ordered quantities to Billbee stock for a set of items.

Each item in the --items JSON array is expected to have:
  sku        – product SKU
  billbeeId  – Billbee product ID
  qty        – quantity to add (delta)

Fetches the current Billbee stock fresh before each update so the
delta is always applied to the live value, even if stock changed
since the preview was shown.

Output: JSON {"ok": true, "updated": N, "errors": [...]}

Usage:
  python execution/gui_add_stock_apply.py --manufacturer TRX \
      --items '[{"sku":"TRX-bp-s","billbeeId":123,"qty":5},...]'
"""
import argparse
import json
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_repo = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, _here)
sys.path.insert(0, os.path.join(_repo, "billbee-python-client"))

from billbee_client import BillbeeClient  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manufacturer", required=True,
                        help="Manufacturer code, e.g. TRX (used in the Billbee reason string)")
    parser.add_argument("--items", required=True,
                        help='JSON array of {sku, billbeeId, qty}')
    args = parser.parse_args()

    try:
        items = json.loads(args.items)
    except json.JSONDecodeError as exc:
        print(json.dumps({
            "ok": False,
            "updated": 0,
            "errors": [{"error": f"Invalid items JSON: {exc}"}],
        }))
        sys.exit(1)

    mfr    = args.manufacturer.upper()
    client = BillbeeClient()
    updated = 0
    errors: list[dict] = []

    for item in items:
        sku       = str(item.get("sku") or "").strip()
        billbee_id = item.get("billbeeId")
        try:
            qty = int(float(str(item.get("qty") or "0")))
        except (ValueError, TypeError):
            qty = 0

        if not sku or not billbee_id or qty <= 0:
            continue

        try:
            product  = client.get_product_by_id(int(billbee_id))
            stocks   = product.get("Stocks") or []
            current  = float(stocks[0].get("StockCurrent") or 0) if stocks else 0.0
            stock_id = int(stocks[0].get("Id") or 0) if stocks else 0
        except Exception as exc:
            errors.append({"sku": sku, "error": f"Fetch failed: {exc}"})
            continue

        new_qty = current + qty
        try:
            client.update_stock(sku, new_qty, stock_id=stock_id,
                                reason=f"B2B order {mfr}")
            updated += 1
        except Exception as exc:
            errors.append({"sku": sku, "error": f"Update failed: {exc}"})

    print(json.dumps({
        "ok":      len(errors) == 0,
        "updated": updated,
        "errors":  errors,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
