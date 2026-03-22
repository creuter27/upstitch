"""
Restore Billbee products from a backup file created by backup_billbee.py.

IMPORTANT — API limitation (confirmed Feb 2026)
------------------------------------------------
Billbee's public REST API has no PUT /products/{id} endpoint; calling it
returns HTTP 500.  Only PATCH is available, and PATCH supports only 8 fields.
This means a full restore of custom fields, BOM, TaricNumber, dimensions, etc.
is NOT possible via the API.

This script is kept as documentation of the backup format and for potential
future use if Billbee adds a full-replace endpoint.  For now, use Billbee's
XLSX article import (Artikel → Importieren) for restoring non-patchable data.

Usage:
  # Dry-run (default) — shows what would be sent, no API calls:
  python execution/restore_from_backup.py --backup-file backups/billbee_YYYY-MM-DD_HHMMSS.json

  # Restore a single product (will fail — see API limitation above):
  python execution/restore_from_backup.py --backup-file backups/... --product-id 12345 --execute
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from billbee_client import BillbeeClient


def _short_summary(product: dict) -> str:
    sku = product.get("SKU") or ""
    pid = product.get("Id") or ""
    typ = product.get("Type") or ""
    bom_count = len(product.get("BillOfMaterial") or [])
    cf_count   = len(product.get("CustomFields")   or [])
    return (
        f"SKU={sku!r:30s}  Id={pid}  Type={typ}"
        f"  BOM_items={bom_count}  CustomFields={cf_count}"
    )


def restore(
    backup_path: Path,
    execute: bool,
    product_id_filter: int | None,
) -> None:
    with open(backup_path, encoding="utf-8") as f:
        products: list[dict] = json.load(f)

    print(f"Backup file : {backup_path}")
    print(f"Products    : {len(products)}")

    if product_id_filter is not None:
        products = [p for p in products if p.get("Id") == product_id_filter]
        if not products:
            print(f"[error] No product with Id={product_id_filter} found in backup.")
            sys.exit(1)
        print(f"Filtered to : {len(products)} product(s) with Id={product_id_filter}")

    if not execute:
        print("\n[DRY-RUN] No changes will be made.  Pass --execute to restore.\n")

    client = BillbeeClient() if execute else None

    skipped = 0
    restored = 0

    for i, product in enumerate(products, 1):
        pid = product.get("Id")
        prefix = f"[{i}/{len(products)}]"

        if not pid:
            print(f"{prefix} SKIP — no Id in backup entry")
            skipped += 1
            continue

        print(f"{prefix} {'PUT' if execute else 'DRY-RUN PUT'}  {_short_summary(product)}")

        if execute:
            try:
                client.update_product(int(pid), product)
                restored += 1
            except Exception as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status == 404:
                    print(f"         !! SKIPPED — product Id={pid} no longer exists in Billbee.")
                    print(f"            (Was deleted after backup? Manual re-creation required.)")
                    skipped += 1
                else:
                    print(f"         !! ERROR: {e}")
                    print("Stopping restore due to unexpected error.")
                    sys.exit(1)

    print()
    if execute:
        print(f"[done] Restored: {restored}  Skipped (not found / no Id): {skipped}")
    else:
        print(f"[done] DRY-RUN complete.  Would restore {len(products) - skipped} product(s).")
        print(f"       Run with --execute to apply.")


def main():
    parser = argparse.ArgumentParser(
        description="Restore Billbee products from a backup JSON file."
    )
    parser.add_argument(
        "--backup-file", required=True,
        help="Path to the backup JSON file (created by backup_billbee.py).",
    )
    parser.add_argument(
        "--product-id", type=int, default=None,
        help="Restore only this specific Billbee product Id (for targeted recovery).",
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually send PUT requests.  Without this flag, dry-run only.",
    )
    args = parser.parse_args()

    restore(
        backup_path=Path(args.backup_file),
        execute=args.execute,
        product_id_filter=args.product_id,
    )


if __name__ == "__main__":
    main()
