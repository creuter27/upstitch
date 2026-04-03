"""Zeigt die ersten Zeilen des '2024'-Tabs."""

import json
from pathlib import Path
from google_sheets_client import open_sheet

CONFIG = json.loads((Path(__file__).parent / "config.json").read_text())
spreadsheet = open_sheet(CONFIG["sheet_url"])
ws = spreadsheet.worksheet("2024")

all_values = ws.get_all_values()
# Zeige erste 10 nicht-leere Zeilen
count = 0
for i, row in enumerate(all_values, 1):
    non_empty = [(chr(ord("A") + j) if j < 26 else f"A{chr(ord('A')+j-26)}", v) for j, v in enumerate(row[:28]) if v.strip()]
    if non_empty:
        print(f"Zeile {i:2d}: " + "  ".join(f"{c}={v!r}" for c, v in non_empty))
        count += 1
    if count >= 10:
        break
