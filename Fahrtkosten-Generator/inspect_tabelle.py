"""Liest alle vorhandenen Einträge aus Tab '2024'."""

import json
from pathlib import Path
from google_sheets_client import open_sheet

CONFIG = json.loads((Path(__file__).parent / "config.json").read_text())
spreadsheet = open_sheet(CONFIG["sheet_url"])
ws = spreadsheet.worksheet("2024")

all_values = ws.get_all_values()
print(f"Tab '2024': {len(all_values)} Zeilen\n")

for i, row in enumerate(all_values, 1):
    if row[0].strip():
        print(f"Zeile {i:2d}: " + "  |  ".join(f"{v}" for v in row[:14] if v.strip()))
