"""
Fahrtkosten-Tabelle Generator

Kopiert die Beispielzeile (Zeile 2, Spalten A–N) aus Tab "2024" für jeden
Di/Mi/Do zwischen 08.01.2024 und 31.03.2024 und trägt das jeweilige Datum
in Spalte A ein. Die vorhandene Beispielzeile bleibt erhalten.
"""

import json
import time
from datetime import date, timedelta
from pathlib import Path

from google_sheets_client import open_sheet

# ── Config ─────────────────────────────────────────────────────────────────
CONFIG    = json.loads((Path(__file__).parent / "config.json").read_text())
SHEET_URL = CONFIG["sheet_url"]
TAB_NAME  = "2024"
START     = date(2024, 1, 8)
END       = date(2024, 3, 31)
WEEKDAYS  = {1, 2, 3}   # Di=1, Mi=2, Do=3 (Mo=0)
SLEEP_SEC = 2
# ───────────────────────────────────────────────────────────────────────────


def dates_in_range(start: date, end: date, weekdays: set[int]):
    d = start
    while d <= end:
        if d.weekday() in weekdays:
            yield d
        d += timedelta(days=1)


def main():
    spreadsheet = open_sheet(SHEET_URL)
    ws = spreadsheet.worksheet(TAB_NAME)

    # Beispielzeile (Zeile 2) Spalten A–N lesen
    template_row = ws.row_values(2)[:14]  # A–N = 14 Spalten

    # Vorhandene Daten ab Zeile 3 lesen um Duplikate zu vermeiden
    existing = ws.col_values(1)[2:]  # Spalte A ab Zeile 3
    existing_dates = set(existing)

    target_dates = list(dates_in_range(START, END, WEEKDAYS))
    print(f"Trage {len(target_dates)} Fahrten in Tab '{TAB_NAME}' ein…\n")

    new_rows = []
    for d in target_dates:
        date_str = d.strftime("%d.%m.%Y")
        if date_str in existing_dates:
            print(f"  {date_str} — bereits vorhanden, übersprungen")
            continue
        row = [date_str] + template_row[1:]
        new_rows.append(row)
        print(f"  {date_str}")

    if not new_rows:
        print("Nichts zu tun.")
        return

    # Ab Zeile 3 (nach Header und Beispielzeile) einfügen
    start_row = 3 + len([v for v in existing if v.strip()])
    end_col = chr(ord("A") + 13)  # N
    cell_range = f"A{start_row}:{end_col}{start_row + len(new_rows) - 1}"

    ws.update(new_rows, cell_range, value_input_option="USER_ENTERED")
    time.sleep(SLEEP_SEC)

    print(f"\n{len(new_rows)} Zeilen eingetragen (ab Zeile {start_row}).")


if __name__ == "__main__":
    main()
