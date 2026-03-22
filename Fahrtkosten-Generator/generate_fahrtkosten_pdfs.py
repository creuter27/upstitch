"""
Fahrtkosten PDF Generator

For each Tuesday, Wednesday, and Thursday between Jan 8 and Mar 31 2024:
  1. Write the date (DD.MM.YYYY) into cell I8 of tab "1"
  2. Export the tab as PDF
  3. Save to ./pdfs/Fahrtkosten_YYYY-MM-DD.pdf

Uses the shared google-client OAuth (token.json in ../google-client/).
"""

import time
import requests
from datetime import date, timedelta
from pathlib import Path

from google_sheets_client import open_sheet, _get_credentials

# ── Config ─────────────────────────────────────────────────────────────────
SHEET_URL = "https://docs.google.com/spreadsheets/d/1wFGEYKXfpTbHrxp4s3ywXaELuGO0XNfoD62TGWNPARw/edit"
SHEET_ID  = "1wFGEYKXfpTbHrxp4s3ywXaELuGO0XNfoD62TGWNPARw"
TAB_NAME  = "1"
CELL      = "I8"
OUT_DIR   = Path(__file__).parent / "pdfs"

START     = date(2024, 1, 8)
END       = date(2024, 3, 31)
WEEKDAYS  = {1, 2, 3}   # Tuesday=1, Wednesday=2, Thursday=3 (Mon=0)

# Pause between exports to avoid hitting quota limits
SLEEP_SEC = 6
# ───────────────────────────────────────────────────────────────────────────


def get_tab_gid(spreadsheet, tab_name: str) -> int:
    ws = spreadsheet.worksheet(tab_name)
    return ws.id


def export_tab_as_pdf(sheet_id: str, gid: int, creds) -> bytes:
    """Export a single sheet tab as PDF bytes using the authenticated user."""
    from google.auth.transport.requests import Request as GoogleRequest
    if not creds.valid:
        creds.refresh(GoogleRequest())

    url = (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/export"
        f"?format=pdf"
        f"&gid={gid}"
        f"&size=7"            # A4
        f"&portrait=true"
        f"&scale=4"           # fit to page (forces single page)
        f"&gridlines=false"
        f"&printtitle=false"
        f"&sheetnames=false"
        f"&pagenumbers=false"
    )
    resp = requests.get(url, headers={"Authorization": f"Bearer {creds.token}"})
    resp.raise_for_status()
    return resp.content


def dates_in_range(start: date, end: date, weekdays: set[int]):
    d = start
    while d <= end:
        if d.weekday() in weekdays:
            yield d
        d += timedelta(days=1)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Opening sheet…")
    spreadsheet = open_sheet(SHEET_URL)
    ws = spreadsheet.worksheet(TAB_NAME)
    gid = ws.id

    creds = _get_credentials()

    target_dates = list(dates_in_range(START, END, WEEKDAYS))
    print(f"Generating PDFs for {len(target_dates)} dates…\n")

    for i, d in enumerate(target_dates, 1):
        date_str = d.strftime("%d.%m.%Y")
        out_path = OUT_DIR / f"Fahrtkosten_{d.strftime('%Y-%m-%d')}.pdf"

        if out_path.exists():
            print(f"[{i:02d}/{len(target_dates)}] {date_str} — already exists, skipping")
            continue

        # Write date into I8
        ws.update([[date_str]], CELL)

        # Small pause so Sheets has time to commit before export
        time.sleep(SLEEP_SEC)

        # Export as PDF (retry with backoff on 429)
        for attempt in range(5):
            try:
                pdf_bytes = export_tab_as_pdf(SHEET_ID, gid, creds)
                break
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429 and attempt < 4:
                    wait = SLEEP_SEC * (2 ** attempt)
                    print(f"  Rate limited, waiting {wait}s…")
                    time.sleep(wait)
                else:
                    raise
        out_path.write_bytes(pdf_bytes)
        print(f"[{i:02d}/{len(target_dates)}] {date_str} → {out_path.name}")

    print(f"\nDone. {len(list(OUT_DIR.glob('*.pdf')))} PDFs in {OUT_DIR}")


if __name__ == "__main__":
    main()
