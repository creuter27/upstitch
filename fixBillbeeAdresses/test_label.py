"""
One-off test: create shipping labels for specific orders by their external order number.

Usage:
    .venv/bin/python test_label.py ORDER_NUMBER [ORDER_NUMBER ...]

Example:
    .venv/bin/python test_label.py 123456789 987654321

Labels are saved to labels/ inside the project folder.
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

sys.path.insert(0, str(Path(__file__).parent))
from execution.billbee_client import BillbeeClient
from execution.create_labels import _create_label as create_label

OUTPUT_DIR = Path(__file__).parent / "labels"


def main():
    if len(sys.argv) < 2:
        print("Usage: .venv/bin/python test_label.py ORDER_NUMBER [ORDER_NUMBER ...]")
        sys.exit(1)

    order_numbers = sys.argv[1:]
    client = BillbeeClient()

    # Print configured shipping providers so we can verify IDs
    print("Fetching configured shipping providers...")
    try:
        providers = client.get_shipping_providers()
        for p in providers:
            print(f"  Provider: id={p.get('id')}  name={p.get('name')!r}")
            for prod in (p.get("products") or []):
                print(f"    Product: id={prod.get('id')}  displayName={prod.get('displayName')!r}")
    except Exception as e:
        print(f"  Could not fetch providers: {e}")

    for order_number in order_numbers:
        print(f"\nLooking up order {order_number}...")
        order = client.find_order_by_number(order_number)
        if not order:
            print(f"  ✗ Order '{order_number}' not found in Billbee")
            continue

        order_id = order.get("BillBeeOrderId") or order.get("Id")
        print(f"  BillBeeOrderId: {order_id}")
        print(f"  ShippingProviderId (order):        {order.get('ShippingProviderId')}")
        print(f"  ShippingProviderName (order):      {order.get('ShippingProviderName')!r}")
        print(f"  ShippingProviderProductId (order): {order.get('ShippingProviderProductId')}")
        print(f"  ShippingProviderProductName:       {order.get('ShippingProviderProductName')!r}")

        print(f"  Creating label via POST /shipment/shipwithlabel ...")
        try:
            path = create_label(client, order, OUTPUT_DIR)
            if path:
                print(f"  ✓ Saved: {path}")
            else:
                print(f"  — No PDF returned (already labelled, or provider not resolved)")
        except Exception as e:
            print(f"  ✗ Error: {e}")


if __name__ == "__main__":
    main()
