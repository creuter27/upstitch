"""
Download a complete snapshot of all Billbee products and save to a JSON file.

The backup file is used as the source of truth for upload_to_billbee.py (PUT body)
and can be used to restore products via restore_from_backup.py.

Usage:
  python execution/backup_billbee.py
  python execution/backup_billbee.py --output-dir backups/

Output:
  backups/billbee_YYYY-MM-DD_HHMMSS.json  — JSON array of complete product dicts
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from billbee_client import BillbeeClient

DEFAULT_OUTPUT_DIR = "backups"


def backup(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    client = BillbeeClient()

    print("Downloading all Billbee products …")
    products = []
    for i, product in enumerate(client.get_all_products(), 1):
        products.append(product)
        if i % 500 == 0:
            print(f"  … {i} downloaded")

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_path = output_dir / f"billbee_{timestamp}.json"

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)

    print(f"\n[ok] {len(products)} products saved to: {out_path}")
    return out_path


def main():
    parser = argparse.ArgumentParser(
        description="Back up the entire Billbee product catalog to a JSON file."
    )
    parser.add_argument(
        "--output-dir", default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for backup files (default: {DEFAULT_OUTPUT_DIR}/)",
    )
    args = parser.parse_args()

    backup(Path(args.output_dir))


if __name__ == "__main__":
    main()
