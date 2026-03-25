#!/usr/bin/env python3
"""
Update the Billbee stock level for a single SKU.

Supports two modes:
  --delta N         Add N to current stock (N may be negative to subtract)
  --new-quantity N  Set stock to absolute value N

Always fetches the current product from Billbee first to obtain the stock location ID
and to report the previous stock level.

Output: JSON object {"ok": true, "sku": "...", "previousStock": N, "newStock": N} to stdout.

Usage:
  python execution/gui_update_stock.py --sku TRX-bp-s-bear --billbee-id 12345 --delta -1
  python execution/gui_update_stock.py --sku TRX-bp-s-bear --billbee-id 12345 --new-quantity 5
  python execution/gui_update_stock.py --sku TRX-bp-s-bear --billbee-id 12345 --delta 3 \
      --reason "manual correction via GUI"
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
    parser.add_argument("--sku",          required=True)
    parser.add_argument("--billbee-id",   required=True, type=int, dest="billbee_id")
    parser.add_argument("--delta",        type=float, default=None,
                        help="Amount to add (negative = subtract)")
    parser.add_argument("--new-quantity", type=float, default=None, dest="new_quantity",
                        help="Absolute target stock level")
    parser.add_argument("--reason",       default="GUI: manual stock correction")
    args = parser.parse_args()

    if args.delta is None and args.new_quantity is None:
        print(json.dumps({"ok": False, "error": "Provide --delta or --new-quantity"}))
        sys.exit(1)

    client = BillbeeClient()

    # Fetch current state to get stock location ID and previous quantity
    product = client.get_product_by_id(args.billbee_id)
    stock_list = product.get("Stocks") or []
    previous_stock = float(stock_list[0].get("StockCurrent", 0)) if stock_list else 0.0
    stock_id       = int(stock_list[0].get("Id", 0))             if stock_list else 0

    new_stock = (previous_stock + args.delta) if args.delta is not None else args.new_quantity

    client.update_stock(args.sku, new_stock, stock_id=stock_id, reason=args.reason)

    print(json.dumps({
        "ok":            True,
        "sku":           args.sku,
        "previousStock": previous_stock,
        "newStock":      new_stock,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
