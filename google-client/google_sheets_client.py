"""
Google Sheets helper using OAuth user account.
Functions: get_client, create_sheet, open_sheet, read_tab, write_tab.

credentials.json and token.json live alongside this file in ~/code/google-client/.
GOOGLE_CREDENTIALS_FILE is loaded from the .env in that same directory.

First run opens a browser for authorization; subsequent runs use the cached token.json.
"""

import os
import re
import time
from pathlib import Path

import gspread
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


def _retry(fn, *args, retries: int = 4, **kwargs):
    """Call fn(*args, **kwargs), retrying on transient Google API errors (503/429)."""
    delay = 5.0
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (429, 500, 503) and attempt < retries - 1:
                print(f"  [Google API {status}] retrying in {delay:.0f}s ...")
                time.sleep(delay)
                delay = min(delay * 2, 60)
            else:
                raise

# Load credentials path from google-client's own .env
load_dotenv(Path(__file__).parent / ".env")

# Combined scopes shared with google_drive_downloader so both modules use one token.json
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]

# Both Google modules share one token file in the google-client directory
TOKEN_FILE = Path(__file__).parent / "token.json"


def _get_credentials() -> Credentials:
    """
    Load OAuth credentials from token.json if available and valid;
    otherwise run the browser-based authorization flow and save token.json.
    """
    creds_file = Path(
        os.environ.get("GOOGLE_CREDENTIALS_FILE", str(Path(__file__).parent / "credentials.json"))
    ).expanduser()

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())

    return creds


def get_client() -> gspread.Client:
    """Return an authenticated gspread client."""
    return gspread.authorize(_get_credentials())


def create_sheet(title: str) -> gspread.Spreadsheet:
    """Create a new Google Sheet with the given title and return it."""
    return _retry(get_client().create, title)


def open_sheet(url_or_id: str) -> gspread.Spreadsheet:
    """Open an existing Google Sheet by URL or spreadsheet ID."""
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", url_or_id)
    sheet_id = match.group(1) if match else url_or_id
    return _retry(get_client().open_by_key, sheet_id)


def open_sheet_by_name(name: str) -> gspread.Spreadsheet:
    """Open an existing Google Sheet by its exact title in Google Drive."""
    return _retry(get_client().open, name)


def read_tab(spreadsheet: gspread.Spreadsheet, tab_name: str) -> list[dict]:
    """Read all rows from a tab and return as a list of dicts (header row = keys)."""
    return _retry(spreadsheet.worksheet(tab_name).get_all_records)


def read_tab_visible(spreadsheet: gspread.Spreadsheet, tab_name: str) -> list[dict]:
    """
    Read only the visible (non-filtered) rows from a tab.

    Queries the Sheets API for row-level hiddenByFilter metadata, then returns
    only rows that are currently visible. Falls back to read_tab behaviour if no
    filter is active or if the metadata cannot be retrieved.
    """
    import requests as _requests

    ws = spreadsheet.worksheet(tab_name)

    # Ensure the OAuth token is fresh before making a raw API call.
    creds = spreadsheet.client.auth
    if not creds.valid:
        creds.refresh(Request())

    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet.id}"
    resp = _requests.get(
        url,
        params={"fields": "sheets(properties(sheetId),data(rowMetadata(hiddenByFilter)))"},
        headers={"Authorization": f"Bearer {creds.token}"},
    )
    resp.raise_for_status()

    hidden_rows: set[int] = set()
    for sheet in resp.json().get("sheets", []):
        if sheet["properties"]["sheetId"] == ws.id:
            for block in sheet.get("data", []):
                for i, meta in enumerate(block.get("rowMetadata", [])):
                    if meta.get("hiddenByFilter"):
                        hidden_rows.add(i)  # 0-based; row 0 = header row
            break

    if not hidden_rows:
        return ws.get_all_records()

    all_values = ws.get_all_values()
    if not all_values:
        return []

    headers = all_values[0]
    return [
        {h: (row[j] if j < len(row) else "") for j, h in enumerate(headers)}
        for i, row in enumerate(all_values[1:], start=1)
        if i not in hidden_rows
    ]


def write_tab(spreadsheet: gspread.Spreadsheet, tab_name: str, rows: list[dict]) -> None:
    """
    Write rows to a tab (worksheet) in the spreadsheet.
    Creates the tab if it doesn't exist; clears it first if it does.
    rows: list of dicts — all dicts must share the same keys (column headers).
    """
    if not rows:
        print(f"[warn] No data to write to tab '{tab_name}'.")
        return

    headers = list(rows[0].keys())
    # Keep int/float as-is so Sheets stores them as numbers.
    # Convert everything else to str, except None → "".
    def _cell(v):
        if v is None:
            return ""
        if isinstance(v, (int, float)):
            return v
        return str(v)

    matrix = [headers] + [
        [_cell(row.get(h)) for h in headers]
        for row in rows
    ]

    try:
        ws = spreadsheet.worksheet(tab_name)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=tab_name, rows=len(matrix) + 10, cols=len(headers))

    _retry(ws.update, matrix, value_input_option="USER_ENTERED")
    print(f"[ok] Wrote {len(rows)} rows to tab '{tab_name}'.")
