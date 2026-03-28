#!/usr/bin/env python3
"""
setup_design_template.py

One-time (re-)applies all conditional-formatting rules and data-transformation
rules to the "Template" tab of "Upstitch Design Sheet".

Run this whenever the rules change. Normal imports (import_designs.py) do NOT
touch the Template tab — they just duplicate it and carry the rules along.

Conditional-formatting rules applied
─────────────────────────────────────
  a. original contains "ohne name" (case-ins.) AND text ≠ ""
     → red background + white text on both the original and text cells.
     → note added to original cell: "Ohne Name bestellt aber Name angegeben"
        (static note written once to the template; it copies into every new tab)

  b. design doesn't match ^M[0-9]{1,4}$ AND original doesn't contain "ohne name"
     → red/white on the design cell.

  c. textFont ≠ "" AND doesn't match ^S[0-9]{1,2}$
     → red/white on the textFont cell.

Data-transformation rules (applied at import time by import_designs.py,
listed here for reference only — NOT applied to the template itself):
  d. text / text2 equals exactly "X" or "x"  → cell is cleared.
  e. original contains a design-code (M[0-9]{1,4}) → design is set to that
     code (uppercase), cell background yellow, note "war <old-design>".

Usage:
    python setup_design_template.py
"""

import sys
from pathlib import Path

import requests as _http

sys.path.insert(0, str(Path(__file__).parent.parent / "google-client"))

from google.auth.transport.requests import Request  # noqa: E402
from google_sheets_client import get_client         # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SHEET_NAME   = "Upstitch Design Sheet"
TEMPLATE_TAB = "Template"

# Expected column headers (case-insensitive match)
EXPECTED_COLS = ("original", "text", "design", "textfont", "category")

# Categories exempt from the "design must match M[0-9]{1,4}" CF rule (rule b)
EXCLUDED_CATEGORIES = (
    "flasche",
    "brotdose",
    "holzbus",
    "holzflugzeug",
    "holzrassel",
    "motorikschleife",
)

# Colors
_RED    = {"red": 0.867, "green": 0.0, "blue": 0.0}
_WHITE  = {"red": 1.0,   "green": 1.0, "blue": 1.0}

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _col_letter(idx: int) -> str:
    result = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        result = chr(65 + rem) + result
    return result


def _grid_range(sheet_id: int, col_idx: int,
                start_row: int = 1, end_row: int = 2000) -> dict:
    """0-based GridRange. start_row=1 → row 2 (skips the header row)."""
    return {
        "sheetId": sheet_id,
        "startRowIndex": start_row,
        "endRowIndex": end_row,
        "startColumnIndex": col_idx,
        "endColumnIndex": col_idx + 1,
    }


def _red_white() -> dict:
    return {"backgroundColor": _RED, "textFormat": {"foregroundColor": _WHITE}}


def _sheets_get(spreadsheet, fields: str) -> dict:
    creds = spreadsheet.client.auth
    if not creds.valid:
        creds.refresh(Request())
    resp = _http.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet.id}",
        params={"fields": fields},
        headers={"Authorization": f"Bearer {creds.token}"},
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def setup_template(spreadsheet, template_ws) -> None:
    """
    Read the Template tab's header row to locate columns, then replace all
    conditional-formatting rules with the current rule set.
    Also writes the static note on the header cell of "original".
    """
    sheet_id = template_ws.id

    # --- Locate columns ---------------------------------------------------
    header_row = template_ws.row_values(1)
    col_map = {h.strip().lower(): i for i, h in enumerate(header_row)}

    missing = [c for c in EXPECTED_COLS if c not in col_map]
    if missing:
        print(f"[warn] Column(s) not found in Template header: {', '.join(missing)}")
        print(f"       Found: {list(col_map.keys())}")

    def idx(name: str) -> int | None:
        return col_map.get(name)

    def cl(name: str) -> str | None:
        i = idx(name)
        return _col_letter(i) if i is not None else None

    orig_l   = cl("original")
    text_l   = cl("text")
    design_l = cl("design")
    font_l   = cl("textfont")
    cat_l    = cl("category")


    # --- Fetch current CF rules so we know how many to delete -------------
    meta = _sheets_get(spreadsheet, "sheets(properties(sheetId),conditionalFormats)")
    current_rules: list = []
    for s in meta.get("sheets", []):
        if s["properties"]["sheetId"] == sheet_id:
            current_rules = s.get("conditionalFormats", [])
            break

    requests: list[dict] = []

    # Delete old rules (reverse order keeps indices valid)
    for i in range(len(current_rules) - 1, -1, -1):
        requests.append(
            {"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": i}}
        )

    rule_idx = 0

    # Rule a: original contains "ohne name" AND text ≠ "" -----------------
    if orig_l and text_l:
        formula = (
            f'=AND('
            f'ISNUMBER(SEARCH("ohne name",${orig_l}2)),'
            f'${text_l}2<>""'
            f')'
        )
        requests.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [
                        _grid_range(sheet_id, col_map["original"]),
                        _grid_range(sheet_id, col_map["text"]),
                    ],
                    "booleanRule": {
                        "condition": {
                            "type": "CUSTOM_FORMULA",
                            "values": [{"userEnteredValue": formula}],
                        },
                        "format": _red_white(),
                    },
                },
                "index": rule_idx,
            }
        })
        rule_idx += 1

    # Rule b: design doesn't match M[0-9]{1,4} AND no "ohne name"
    #         AND category not in EXCLUDED_CATEGORIES ------------------
    if design_l and orig_l:
        cat_excl = (
            "".join(f'NOT(${cat_l}2="{c}"),' for c in EXCLUDED_CATEGORIES)
            if cat_l else ""
        )
        formula = (
            f'=AND('
            f'NOT(IFERROR(REGEXMATCH(${design_l}2,"(?i)^M[0-9]{{1,4}}$"),FALSE)),'
            f'NOT(ISNUMBER(SEARCH("ohne name",${orig_l}2))),'
            f'{cat_excl}'
            f'TRUE)'  # trailing TRUE closes the AND cleanly after the last comma
        )
        requests.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [_grid_range(sheet_id, col_map["design"])],
                    "booleanRule": {
                        "condition": {
                            "type": "CUSTOM_FORMULA",
                            "values": [{"userEnteredValue": formula}],
                        },
                        "format": _red_white(),
                    },
                },
                "index": rule_idx,
            }
        })
        rule_idx += 1

    # Rule c: textFont ≠ "" AND doesn't match S[0-9]{1,2} ----------------
    if font_l:
        formula = (
            f'=AND('
            f'${font_l}2<>"",'
            f'NOT(IFERROR(REGEXMATCH(${font_l}2,"(?i)^S[0-9]{{1,2}}$"),FALSE))'
            f')'
        )
        requests.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [_grid_range(sheet_id, col_map["textfont"])],
                    "booleanRule": {
                        "condition": {
                            "type": "CUSTOM_FORMULA",
                            "values": [{"userEnteredValue": formula}],
                        },
                        "format": _red_white(),
                    },
                },
                "index": rule_idx,
            }
        })
        rule_idx += 1

    # NOTE: textColor validation (not in Garnfarben "Name intern") cannot be a
    # CF rule — the Sheets API rejects cross-tab references in CUSTOM_FORMULA
    # conditions. This is handled programmatically by import_designs.py instead.

    # --- Send all requests in one call -----------------------------------
    if requests:
        spreadsheet.batch_update({"requests": requests})

    n_del = sum(1 for r in requests if "deleteConditionalFormatRule" in r)
    n_add = sum(1 for r in requests if "addConditionalFormatRule" in r)
    print(f"[ok] Removed {n_del} old CF rule(s), added {n_add} new CF rule(s)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("[..] Connecting to Google Sheets …")
    gc = get_client()
    spreadsheet = gc.open(SHEET_NAME)
    print(f"[ok] Opened '{SHEET_NAME}'")

    try:
        template = spreadsheet.worksheet(TEMPLATE_TAB)
    except Exception:
        print(f"[error] Tab '{TEMPLATE_TAB}' not found in '{SHEET_NAME}'.")
        sys.exit(1)

    print(f"[..] Setting up conditional formatting on '{TEMPLATE_TAB}' …")
    setup_template(spreadsheet, template)
    print("[ok] Done.")


if __name__ == "__main__":
    main()
