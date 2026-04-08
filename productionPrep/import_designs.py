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
import sys
import unicodedata
import webbrowser
from difflib import get_close_matches
from datetime import datetime
from pathlib import Path

# Make the google-client sibling repo importable without being installed
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parent / "google-client"))
sys.path.insert(0, str(_HERE / "execution"))

from google_sheets_client import get_client  # noqa: E402
from config_loader import load_config as _load_config  # noqa: E402


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

# Design code: letter M (case-insensitive) followed by 1–4 digits.
# \b on the left prevents matching inside longer words like "SM10".
# No \b on the right: stops naturally at the first non-digit (e.g. M103-10.8 → M103).
DESIGN_RE = re.compile(r"(?i)\bM[0-9]{1,4}")

# Matches a valid M### design at the start, optionally followed by junk to strip.
# Used to clean the design column if it already contains M### with extra characters.
_DESIGN_STRIP_RE = re.compile(r"(?i)^(M[0-9]{1,4})\D.*$")

# Date-pattern tabs: yy-mm-dd or yy-mm-dd-N (suffix from _find_available_tab_name)
DATE_TAB_RE = re.compile(r"^\d{2}-\d{2}-\d{2}(-\d+)?$")

# Extracts the optional second wish-text line from the "original" field on towel orders.
# Matches: "Wunschtext Zeile 2 (optional):<captured>(Schrift"
WUNSCHTEXT2_RE = re.compile(r"Wunschtext Zeile 2 \(optional\):(.+?)\(Schrift", re.IGNORECASE)

# Substrings that mean "no personalisation" — cells containing these are cleared.
_NO_NAME_TERMS = ("kein name", "no name")

# Extracts the personalization text from "Personalisierung: <text>" up to end of line.
# Used in rule h to auto-fill the text column when it is empty.
_PERSONALISIERUNG_TEXT_RE = re.compile(r"(?i)Personalisierung:\s*([^\r\n]+)")

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


def _resolve_color(value: str, valid_names: set[str], alias_map: dict[str, str]) -> str | None:
    """
    Try to resolve a raw textColor value to a canonical "Name intern" string.

    Priority:
      1. Exact match against valid_names                  → keep unchanged
      2. Case-insensitive exact match in alias_map        → replace with intern name
      3. Fuzzy match (≥ 0.7) against alias keys + intern names → replace with intern name
      4. No match                                         → return None (caller marks cell red)
    """
    if not value.strip():
        return value  # empty is fine, no validation needed

    # 1. Already a valid canonical name
    if value in valid_names:
        return value

    lower = value.lower()

    # 2. Exact alias (case-insensitive)
    if lower in alias_map:
        return alias_map[lower]

    # 3. Fuzzy match — search aliases first, then canonical names
    all_aliases  = list(alias_map.keys())
    intern_lower = {n.lower(): n for n in valid_names}
    candidates   = all_aliases + list(intern_lower.keys())

    matches = get_close_matches(lower, candidates, n=1, cutoff=0.7)
    if matches:
        hit = matches[0]
        if hit in alias_map:
            return alias_map[hit]
        if hit in intern_lower:
            return intern_lower[hit]

    return None  # unresolvable


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
# Data-level row transformations (rules d and e)
# ---------------------------------------------------------------------------

def _process_rows(
    rows: list[list[str]],
    col_map: dict,
    valid_colors: set[str] | None = None,
    color_alias_map: dict[str, str] | None = None,
    stitched_categories: set[str] | None = None,
) -> tuple[list[list[str]], list[tuple], list[tuple], list[tuple]]:
    """
    Apply value-level transformations to the CSV rows.

    Rule d: text / text2 equals exactly "X" or "x" → cell cleared.
    Rule e: original contains a design-code (M[0-9]{1,4}) → design is set to
            that code (uppercase); cell recorded for yellow background and note
            "war <old-design>".
    Rule f: category contains "handtuch" AND original matches
            "Wunschtext Zeile 2 (optional):<text>(Schrift" with non-blank
            <text> → stripped <text> written into text (if empty), else text2.
    Rule g: textColor is not empty → resolve against Garnfarben color list:
              - exact or alias match  → replace value with canonical name
              - fuzzy match           → replace value with canonical name
              - no match              → cell recorded for red/white formatting

    Rule-a notes ("Ohne Name bestellt aber Name angegeben") are also recorded
    here but applied after upload.

    Returns:
        processed_rows  — list[list[str]] with modifications applied
        notes           — list of (row_0based, col_0based, note_text)
        yellow_cells    — list of (row_0based, col_0based)
        red_cells       — list of (row_0based, col_0based)  [unresolved textColor]
    """
    if len(rows) < 2:
        return rows, [], [], []

    text_idx      = col_map.get("text")
    text2_idx     = col_map.get("text2")
    orig_idx      = col_map.get("original")
    design_idx    = col_map.get("design")
    category_idx  = col_map.get("category")
    textcolor_idx = col_map.get("textcolor")

    max_col = max(
        (i for i in [text_idx, text2_idx, orig_idx, design_idx,
                      category_idx, textcolor_idx] if i is not None),
        default=0,
    )

    notes:        list[tuple] = []
    yellow_cells: list[tuple] = []
    red_cells:    list[tuple] = []
    processed = [list(rows[0])]  # header unchanged

    for row_0based, raw in enumerate(rows[1:], start=1):
        row = list(raw)
        while len(row) <= max_col:
            row.append("")

        # Rule f — towel orders: extract "Wunschtext Zeile 2" from original
        #          → text if text is empty, otherwise → text2
        if (
            category_idx is not None
            and orig_idx is not None
            and "handtuch" in row[category_idx].lower()
        ):
            m = WUNSCHTEXT2_RE.search(row[orig_idx])
            if m:
                extracted = m.group(1).strip()
                if extracted:
                    if text_idx is not None and not row[text_idx].strip():
                        row[text_idx] = extracted
                    elif text2_idx is not None:
                        row[text2_idx] = extracted

        # Rule h — remember whether text was originally empty (before rule d clears "X")
        _text_originally_empty = text_idx is not None and not row[text_idx].strip()

        # Rule d — clear lone "X" / "x" and "Kein Name" / "No Name" variants
        for _ci in (text_idx, text2_idx):
            if _ci is None:
                continue
            _v = row[_ci].strip()
            if _v in ("x", "X") or any(t in _v.lower() for t in _NO_NAME_TERMS):
                row[_ci] = ""

        # Rule h — if text was originally empty, extract personalization from original.
        # Skip if original contains "ohne name", or if the extracted value is "ohne" / "x".
        if _text_originally_empty and text_idx is not None and orig_idx is not None:
            orig = row[orig_idx]
            if "ohne name" not in orig.lower():
                _m = _PERSONALISIERUNG_TEXT_RE.search(orig)
                if _m:
                    _extracted = _m.group(1).strip()
                    if _extracted.lower() not in ("ohne", "x", ""):
                        row[text_idx] = _extracted

        # Rule i — strip trailing non-design text from the design column.
        #          Applied unconditionally to all rows.
        #          E.g. "M93(Elefant)" → "M93", "M103-10.8" → "M103".
        if design_idx is not None:
            _raw_design = row[design_idx].strip()
            _strip_m = _DESIGN_STRIP_RE.match(_raw_design)
            if _strip_m:
                _cleaned = _strip_m.group(1).upper()
                if _cleaned != _raw_design:
                    notes.append((row_0based, design_idx, f"war {_raw_design}"))
                    yellow_cells.append((row_0based, design_idx))
                    row[design_idx] = _cleaned

        # Rule e — extract design code from "original", update design column.
        #          Only applies to stitched categories (bestickt=True in Defaults).
        #          If stitched_categories is None (failed to load), applies to all rows.
        _row_category = (
            unicodedata.normalize("NFC", row[category_idx].strip().lower())
            if category_idx is not None else ""
        )
        _is_stitched = (
            stitched_categories is None
            or _row_category in stitched_categories
        )
        if _is_stitched and orig_idx is not None and design_idx is not None:
            m = DESIGN_RE.search(row[orig_idx])
            if m:
                new_design = m.group(0).upper()
                old_design = row[design_idx]  # may already be cleaned by rule i
                if new_design != old_design.upper().strip():
                    row[design_idx] = new_design
                    yellow_cells.append((row_0based, design_idx))
                    notes.append((row_0based, design_idx, f"war {old_design}"))

        # Rule g — resolve textColor against Garnfarben list
        if (
            textcolor_idx is not None
            and valid_colors is not None
            and color_alias_map is not None
            and row[textcolor_idx].strip()
        ):
            resolved = _resolve_color(row[textcolor_idx], valid_colors, color_alias_map)
            if resolved is None:
                red_cells.append((row_0based, textcolor_idx))
            elif resolved != row[textcolor_idx]:
                row[textcolor_idx] = resolved

        # Rule a — note on "original" cell (red/white visual is handled by CF)
        if orig_idx is not None and text_idx is not None:
            if "ohne name" in row[orig_idx].lower() and row[text_idx].strip():
                notes.append((
                    row_0based, orig_idx,
                    "Ohne Name bestellt aber Name angegeben",
                ))

        processed.append(row)

    return processed, notes, yellow_cells, red_cells


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
    # Transform CSV rows (rules d, e, f, g, h)
    # ------------------------------------------------------------------
    col_map = _build_col_map(rows[0])
    processed_rows, notes, yellow_cells, red_cells = _process_rows(
        rows, col_map, valid_colors, color_alias_map, stitched_categories
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
