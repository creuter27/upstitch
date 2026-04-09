#!/usr/bin/env python3
"""
import_designs.py

Imports a design CSV as a new date-named tab into "Upstitch Design Sheet":
  1. Applies data-level transformations to the CSV rows (rules d, e).
  2. Duplicates the "Template" tab → new date tab (inheriting all formatting
     and conditional-formatting rules set up by setup_design_template.py).
  3. Uploads the processed rows.
  4. Adds per-cell notes and yellow backgrounds for rule-e hits.
  5. Opens the new tab in the browser.

Tab-naming: if today's tab already exists, appends -1, -2, … — no prompt.

Usage:
    python import_designs.py
    python import_designs.py --csv "Z:\\other\\path\\export.csv"
    python import_designs.py --date 26-03-25   # override the tab name
"""

import argparse
import csv
import re
import shutil
import sys
import unicodedata
import webbrowser
from datetime import datetime
from pathlib import Path

# Make the google-client sibling repo importable without being installed
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parent / "google-client"))
sys.path.insert(0, str(_HERE / "execution"))

from google_sheets_client import get_client  # noqa: E402
from config_loader import load_config as _load_config  # noqa: E402
from design_rule_engine import load_rules, process_rows as _engine_process_rows  # noqa: E402


def _default_csv_from_config() -> Path:
    """Read design_csv from platform config; fall back to the legacy Windows path."""
    try:
        cfg = _load_config()
        p = cfg.get("design_csv")
        if p:
            return Path(p).expanduser()
    except Exception:
        pass
    return Path(r"Z:\import\designs\export-current-design.csv")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SHEET_NAME     = "Upstitch Design Sheet"
MAPPINGS_SHEET = "Upstitch Mappings"
TEMPLATE_TAB   = "Template"
GARNFARBEN_TAB = "Garnfarben"
DEFAULTS_TAB   = "Defaults"
DEFAULT_CSV    = None  # resolved at runtime from platform config

_YELLOW = {"red": 1.0, "green": 1.0, "blue": 0.0}

# Date-pattern tabs: yy-mm-dd or yy-mm-dd-N (suffix from _find_available_tab_name)
DATE_TAB_RE = re.compile(r"^\d{2}-\d{2}-\d{2}(-\d+)?$")

# ---------------------------------------------------------------------------
# Color data (loaded from "Upstitch Mappings" > "Garnfarben")
# ---------------------------------------------------------------------------

def _load_color_data(spreadsheet) -> tuple[set[str], dict[str, str]]:
    """
    Reads the Garnfarben tab (imported via IMPORTRANGE into the same spreadsheet).

    Returns:
        valid_names  — set of canonical color names from "Name intern"
        alias_map    — {alias_lower: canonical_name} built from all
                       "Name extern1" … "Name extern12" columns
    """
    try:
        ws = spreadsheet.worksheet(GARNFARBEN_TAB)
        records = ws.get_all_records()
    except Exception as e:
        print(f"[warn] Could not load color data from '{GARNFARBEN_TAB}' tab: {e}")
        return set(), {}

    valid_names: set[str] = set()
    alias_map:   dict[str, str] = {}

    for row in records:
        intern = str(row.get("Name intern", "")).strip()
        if not intern:
            continue
        valid_names.add(intern)
        for i in range(1, 13):
            extern = str(row.get(f"Name extern{i}", "")).strip()
            if extern:
                alias_map[extern.lower()] = intern

    print(f"[ok] Loaded {len(valid_names)} colors, {len(alias_map)} aliases from '{GARNFARBEN_TAB}'")
    return valid_names, alias_map


def _load_stitched_categories(gc) -> set[str] | None:
    """
    Read the 'Defaults' tab of 'Upstitch Mappings' and return the set of
    category values where the 'bestickt' column is truthy.

    Returns None on any error so the caller can fall back to applying rule e
    unconditionally rather than silently suppressing it.
    """
    try:
        mappings_ss = gc.open(MAPPINGS_SHEET)
        ws = mappings_ss.worksheet(DEFAULTS_TAB)
        records = ws.get_all_records()
    except Exception as e:
        print(f"[warn] Could not load '{DEFAULTS_TAB}' from '{MAPPINGS_SHEET}': {e}")
        return None

    if not records:
        print(f"[warn] '{DEFAULTS_TAB}' tab in '{MAPPINGS_SHEET}' is empty — rule e applies to all rows")
        return None

    # Find category and bestickt column names (case-insensitive)
    headers = list(records[0].keys())
    cat_col = next(
        (h for h in headers if "kategor" in h.lower() or "categor" in h.lower()), None
    )
    bes_col = next(
        (h for h in headers if h.strip().lower() == "bestickt"), None
    )
    if cat_col is None or bes_col is None:
        missing = []
        if cat_col is None: missing.append("category column")
        if bes_col is None: missing.append("'bestickt'")
        print(f"[warn] '{DEFAULTS_TAB}': could not find {', '.join(missing)} — rule e applies to all rows")
        return None

    stitched: set[str] = set()
    for row in records:
        val = row.get(bes_col)
        is_stitched = val is True or str(val).strip().upper() in ("TRUE", "1", "JA", "YES")
        if is_stitched:
            cat = unicodedata.normalize("NFC", str(row.get(cat_col, "")).strip().lower())
            if cat:
                stitched.add(cat)

    print(f"[ok] Loaded {len(stitched)} stitched category/ies from '{DEFAULTS_TAB}': {sorted(stitched)}")
    return stitched


# ---------------------------------------------------------------------------
# CSV reading
# ---------------------------------------------------------------------------

def _read_csv(path: Path) -> list[list[str]]:
    """Read CSV, trying UTF-8-BOM first, then Windows-1252."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with open(path, newline="", encoding=encoding) as f:
                rows = list(csv.reader(f))
            if rows:
                print(f"[ok] Read {len(rows)} rows from {path.name}  (encoding: {encoding})")
                return rows
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Cannot decode {path} — tried utf-8-sig and cp1252.")


# ---------------------------------------------------------------------------
# Column map
# ---------------------------------------------------------------------------

def _build_col_map(headers: list[str]) -> dict[str, int]:
    """
    Return lowercase-name → 0-based-index map.

    Also adds short aliases for columns with a 'product' prefix so that
    e.g. 'productCategory' is accessible as both 'productcategory' and 'category'.
    """
    mapping = {h.strip().lower(): i for i, h in enumerate(headers)}
    for key, idx in list(mapping.items()):
        if key.startswith("product") and len(key) > 7:
            alias = key[7:]  # strip "product" prefix
            if alias not in mapping:
                mapping[alias] = idx
    for name in ("original", "text", "design", "textfont", "category"):
        if name not in mapping:
            print(f"[warn] Expected column '{name}' not found in headers.")
    return mapping


# ---------------------------------------------------------------------------
# Post-upload: notes and yellow backgrounds
# ---------------------------------------------------------------------------

_RED   = {"red": 0.867, "green": 0.0, "blue": 0.0}
_WHITE = {"red": 1.0,   "green": 1.0, "blue": 1.0}


def _apply_post_upload(
    spreadsheet,
    ws,
    notes:        list[tuple],
    yellow_cells: list[tuple],
    red_cells:    list[tuple],
) -> None:
    """Write per-cell notes, yellow backgrounds, and red/white invalid-color cells."""
    if not notes and not yellow_cells and not red_cells:
        return

    sheet_id = ws.id
    requests: list[dict] = []

    for row_idx, col_idx, note_text in notes:
        requests.append({
            "updateCells": {
                "rows": [{"values": [{"note": note_text}]}],
                "fields": "note",
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": row_idx,
                    "endRowIndex": row_idx + 1,
                    "startColumnIndex": col_idx,
                    "endColumnIndex": col_idx + 1,
                },
            }
        })

    for row_idx, col_idx in yellow_cells:
        requests.append({
            "updateCells": {
                "rows": [{"values": [{"userEnteredFormat": {"backgroundColor": _YELLOW}}]}],
                "fields": "userEnteredFormat.backgroundColor",
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": row_idx,
                    "endRowIndex": row_idx + 1,
                    "startColumnIndex": col_idx,
                    "endColumnIndex": col_idx + 1,
                },
            }
        })

    for row_idx, col_idx in red_cells:
        requests.append({
            "updateCells": {
                "rows": [{"values": [{
                    "userEnteredFormat": {
                        "backgroundColor": _RED,
                        "textFormat": {"foregroundColor": _WHITE},
                    }
                }]}],
                "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat.foregroundColor",
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": row_idx,
                    "endRowIndex": row_idx + 1,
                    "startColumnIndex": col_idx,
                    "endColumnIndex": col_idx + 1,
                },
            }
        })

    spreadsheet.batch_update({"requests": requests})
    parts = []
    if notes:        parts.append(f"{len(notes)} note(s)")
    if yellow_cells: parts.append(f"{len(yellow_cells)} yellow cell(s)")
    if red_cells:    parts.append(f"{len(red_cells)} unresolved color(s) marked red")
    print(f"[ok] Applied: {', '.join(parts)}")


# ---------------------------------------------------------------------------
# Sheet / tab helpers
# ---------------------------------------------------------------------------

def _find_available_tab_name(spreadsheet, base: str) -> str:
    """Return base if unused, otherwise base-1, base-2, … (never prompts)."""
    existing = {ws.title for ws in spreadsheet.worksheets()}
    if base not in existing:
        return base
    for i in range(1, 200):
        candidate = f"{base}-{i}"
        if candidate not in existing:
            return candidate
    raise RuntimeError("Could not find an available tab name after 199 attempts.")


def _move_to_first_date_position(spreadsheet, new_ws) -> None:
    """Move new_ws to be the first tab among all date-pattern (yy-mm-dd) tabs."""
    worksheets = spreadsheet.worksheets()
    for i, ws in enumerate(worksheets):
        if ws.id != new_ws.id and DATE_TAB_RE.match(ws.title):
            spreadsheet.batch_update({"requests": [{
                "updateSheetProperties": {
                    "properties": {"sheetId": new_ws.id, "index": i},
                    "fields": "index",
                }
            }]})
            print(f"[ok] Moved '{new_ws.title}' to tab position {i} (first among date tabs)")
            return
    print(f"[ok] '{new_ws.title}' is the only date tab — no reordering needed")


def _duplicate_template(spreadsheet, template_id: int, new_name: str):
    """Duplicate the Template sheet; return the new gspread Worksheet."""
    response = spreadsheet.batch_update({
        "requests": [{
            "duplicateSheet": {
                "sourceSheetId": template_id,
                "insertSheetIndex": len(spreadsheet.worksheets()),
                "newSheetName": new_name,
            }
        }]
    })
    new_sheet_id = response["replies"][0]["duplicateSheet"]["properties"]["sheetId"]
    # Unhide: Template tab is hidden to avoid clutter; the duplicate inherits that state
    spreadsheet.batch_update({"requests": [{
        "updateSheetProperties": {
            "properties": {"sheetId": new_sheet_id, "hidden": False},
            "fields": "hidden",
        }
    }]})
    return next(ws for ws in spreadsheet.worksheets() if ws.id == new_sheet_id)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _rotate_csv(csv_path: Path, tab_name: str, processed_rows: list[list[str]]) -> None:
    """
    After a successful import:
      1. Rename the original CSV to <stem>-<tab_name>.csv  (e.g. export-current-design-26-04-09.csv)
      2. Write the processed data back to the original filename so downstream
         tools always find the latest clean export at the well-known path.
    """
    stem    = csv_path.stem
    suffix  = csv_path.suffix
    archive = csv_path.with_name(f"{stem}-{tab_name}{suffix}")

    try:
        # 1. Rename original → archive (overwrite if somehow already there)
        shutil.move(str(csv_path), str(archive))
        print(f"[ok] Renamed original CSV → {archive.name}")

        # 2. Write processed rows back to the original path
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerows(processed_rows)
        print(f"[ok] Wrote processed CSV → {csv_path.name}")
    except Exception as e:
        print(f"[warn] Could not rotate CSV files: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import design CSV as a new tab in Upstitch Design Sheet"
    )
    _cfg_default = _default_csv_from_config()
    parser.add_argument(
        "--csv", default=str(_cfg_default), metavar="PATH",
        help=f"Path to the CSV file (default from config: {_cfg_default})",
    )
    parser.add_argument(
        "--date", default=None, metavar="YY-MM-DD",
        help="Override the tab name (default: today as YY-MM-DD)",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv).expanduser()
    if not csv_path.exists():
        print(f"[skip] Design CSV not found at {csv_path} — skipping import.")
        sys.exit(0)

    rows = _read_csv(csv_path)
    if not rows:
        print("[error] CSV is empty.")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Connect
    # ------------------------------------------------------------------
    print("[..] Connecting to Google Sheets …")
    gc = get_client()
    spreadsheet = gc.open(SHEET_NAME)
    print(f"[ok] Opened '{SHEET_NAME}'")

    try:
        template = spreadsheet.worksheet(TEMPLATE_TAB)
    except Exception:
        print(f"[error] Tab '{TEMPLATE_TAB}' not found in '{SHEET_NAME}'.")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Load color reference data + stitched-category list
    # ------------------------------------------------------------------
    print(f"[..] Loading color list from '{GARNFARBEN_TAB}' tab …")
    valid_colors, color_alias_map = _load_color_data(spreadsheet)

    print(f"[..] Loading stitched categories from '{MAPPINGS_SHEET}' > '{DEFAULTS_TAB}' …")
    stitched_categories = _load_stitched_categories(gc)

    # ------------------------------------------------------------------
    # Transform CSV rows via rule engine
    # ------------------------------------------------------------------
    col_map = _build_col_map(rows[0])
    rules = load_rules()
    print(f"[ok] Loaded {len(rules)} import rule(s)")
    processed_rows, notes, yellow_cells, red_cells = _engine_process_rows(
        rows, col_map, rules, valid_colors, color_alias_map, stitched_categories
    )
    if yellow_cells:
        print(f"[ok] Extracted design code from 'original' in {len(yellow_cells)} row(s)")
    if red_cells:
        print(f"[warn] {len(red_cells)} unresolved textColor value(s) — will be marked red")

    # ------------------------------------------------------------------
    # Determine tab name (no prompting — suffix if taken)
    # ------------------------------------------------------------------
    base_name = args.date or datetime.now().strftime("%y-%m-%d")
    tab_name  = _find_available_tab_name(spreadsheet, base_name)
    if tab_name != base_name:
        print(f"[..] Tab '{base_name}' already exists — using '{tab_name}'")

    # ------------------------------------------------------------------
    # Duplicate Template → new tab (copies all formatting + CF rules)
    # ------------------------------------------------------------------
    print(f"[..] Duplicating '{TEMPLATE_TAB}' → '{tab_name}' …")
    new_ws = _duplicate_template(spreadsheet, template.id, tab_name)
    print(f"[ok] Tab '{tab_name}' created")
    _move_to_first_date_position(spreadsheet, new_ws)

    # ------------------------------------------------------------------
    # Upload data  (clear() removes values only — formatting stays intact)
    # ------------------------------------------------------------------
    new_ws.clear()
    print(f"[..] Writing {len(processed_rows)} rows …")
    new_ws.update(processed_rows, value_input_option="USER_ENTERED")
    print(f"[ok] {len(processed_rows)} rows × {len(processed_rows[0])} columns written")

    # ------------------------------------------------------------------
    # Per-cell notes and yellow backgrounds
    # ------------------------------------------------------------------
    _apply_post_upload(spreadsheet, new_ws, notes, yellow_cells, red_cells)

    # ------------------------------------------------------------------
    # Rename original CSV and write processed version back
    # ------------------------------------------------------------------
    _rotate_csv(csv_path, tab_name, processed_rows)

    # ------------------------------------------------------------------
    # Open in browser
    # ------------------------------------------------------------------
    url = (
        f"https://docs.google.com/spreadsheets/d/{spreadsheet.id}"
        f"/edit#gid={new_ws.id}"
    )
    webbrowser.open(url)
    print(f"[ok] Done.  {url}")


if __name__ == "__main__":
    main()
