"""
One-off fix: set Billbee stock to the correct values for the 2026-03-08 TRX order.
These are the pre-computed totals (original stock + ordered qty) that were
computed correctly but written to Billbee with wrong JSON field names (set to 0).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from billbee_client import BillbeeClient

CORRECT_STOCK = {
    "TRX-Backp-big-butterfly":      11,
    "TRX-Backp-big-cat":            11,
    "TRX-Backp-big-dino":           10,
    "TRX-Backp-big-dragon":          9,
    "TRX-Backp-big-fox":             8,
    "TRX-Backp-big-monkey":          1,
    "TRX-Backp-big-triceratops":     8,
    "TRX-Backp-small-bear":          8,
    "TRX-Backp-small-bumblebee":     6,
    "TRX-Backp-small-butterfly":    10,
    "TRX-Backp-small-cat":          13,
    "TRX-Backp-small-dino":          9,
    "TRX-Backp-small-dragon":       11,
    "TRX-Backp-small-fox":           6,
    "TRX-Backp-small-lion":          6,
    "TRX-Backp-small-rabbit":       12,
    "TRX-Backp-small-triceratops":  12,
    "TRX-Bottle-350-butterfly":     12,
    "TRX-Bottle-350-cat":            7,
    "TRX-Bottle-350-dino":          15,
    "TRX-Bottle-350-dragon":         6,
    "TRX-Bottle-350-fox":            8,
    "TRX-Bottle-350-lion":           6,
    "TRX-Bottle-350-mouse":         14,
    "TRX-Bottle-350-rabbit":        14,
    "TRX-Bus":                       4,
}

client = BillbeeClient()
ok = 0
for sku, qty in CORRECT_STOCK.items():
    try:
        client.update_stock(sku, qty, reason="fix: 2026-03-08 order (field name bug)")
        print(f"  {sku}: → {qty}")
        ok += 1
    except Exception as e:
        print(f"  [error] {sku}: {e}")

print(f"\n{ok}/{len(CORRECT_STOCK)} updated.")
