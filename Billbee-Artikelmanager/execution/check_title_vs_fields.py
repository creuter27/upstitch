"""
Cross-check the Title of every BOM row against the attribute columns
Produktkategorie, Produktgröße, and Produktvariante to surface human errors.

Row selection
-------------
All rows where BOM_SKUs is non-empty (i.e. every resolved BOM / listing item),
excluding rows already marked Action='delete'.

Checks
------
Variant  : match known animal names (German + English) as whole words in the
           full title.  If the full title gives an ambiguous result, fall back
           to the suffix (segment after the last " - ").
           Flag when exactly ONE unambiguous variant is found AND it differs
           from the stored Produktvariante.

Size     : match explicit size terms per category in the full title.
           If the full title is ambiguous (both small AND big found, or 350 AND
           500), re-check using only the suffix.
           Flag when an unambiguous size is found AND it is absent from the
           stored Produktgröße.

Category : detect category keywords in the SUFFIX ONLY (part after the last
           " - ").  This avoids false positives from contextual mentions in
           the generic body (e.g. "sold as set with Trinkflasche").
           Flag when detected categories (as a set) differ from the stored
           Produktkategorie (as a set).

Summary  : reports total, per-attribute match/mismatch/ambiguous/no-signal
           counts, then lists all mismatch rows.

Output tabs written to the Google Sheet
----------------------------------------
title_check_summary    — overall + per-attribute counts table
title_check_mismatches — one row per mismatch, formatted with red background

Usage
-----
  python execution/check_title_vs_fields.py --sheet-url URL
  python execution/check_title_vs_fields.py --sheet-url URL --show-ok
"""

import argparse
import re
import sys
from pathlib import Path

import gspread

sys.path.insert(0, str(Path(__file__).parent.parent))

from google_sheets_client import open_sheet, read_tab
from execution.mappings_loader import Mappings
from execution.sku_parser import parse_sku

TAB_NAME       = "ProductList"
SUMMARY_TAB    = "title_check_summary"
MISMATCHES_TAB = "title_check_mismatches"

# Formatting colour constants (Google Sheets API RGB 0-1 floats)
_BLUE_BG = {"red": 0.24, "green": 0.52, "blue": 0.78}
_BLUE_FG = {"red": 1.0,  "green": 1.0,  "blue": 1.0}
_RED_BG  = {"red": 1.0,  "green": 0.87, "blue": 0.87}


# ──────────────────────────────────────────────────────────────────────────────
# Keyword tables
# ──────────────────────────────────────────────────────────────────────────────

# German / special-char aliases for canonical ASCII variant keys
_VAR_EXTRA: dict[str, list[str]] = {
    "baer":      ["bär"],
    "loewe":     ["löwe"],
    "eisbaer":   ["eisbär"],
    "waschbaer": ["waschbär"],
}

# Per-category size patterns (regex alternatives, matched case-insensitively).
# Detecting BOTH small and big in the same text → ambiguous; use suffix instead.
_SIZE_PATS: dict[str, dict[str, list[str]]] = {
    "rucksack": {
        "small": [r"klein/small", r"small/klein", r"\bklein\b", r"\bsmall\b"],
        "big":   [r"groß/big",    r"big/groß",    r"gross/big", r"big/gross",
                  r"\bgroß\b",    r"\bgross\b",   r"\bbig\b"],
    },
    "flasche": {
        "350": [r"350\s*ml", r"\b350\b"],
        "500": [r"500\s*ml", r"\b500\b"],
    },
}

# Keywords that identify a category when found in the title suffix.
# Matched case-insensitively as substrings (all are ≥ 5 chars).
_SUFFIX_CAT_KEYWORDS: dict[str, list[str]] = {
    "rucksack":    ["rucksack"],
    "flasche":     ["flasche"],
    "handtuch":    ["handtuch"],
    "sportbeutel": ["turnbeutel", "sportbeutel"],
    "brotdose":    ["brotdose", "lunchbox"],
}


def _build_var_patterns(mappings: Mappings) -> dict[str, re.Pattern]:
    """Return {canonical: compiled whole-word pattern} for each variant."""
    result: dict[str, re.Pattern] = {}
    for canonical, attrs in mappings.variants.items():
        terms: set[str] = {canonical}
        for tok in (attrs.get("tokens") or []):
            if len(tok) >= 4:
                terms.add(tok)
        for extra in _VAR_EXTRA.get(canonical, []):
            terms.add(extra)
        alts = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
        result[canonical] = re.compile(r"\b(?:" + alts + r")\b", re.IGNORECASE)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Detection helpers
# ──────────────────────────────────────────────────────────────────────────────

def _title_suffix(title: str) -> str:
    """Return the segment after the last ' - ', stripped.  Empty if absent."""
    idx = title.rfind(" - ")
    return title[idx + 3:].strip() if idx >= 0 else ""


def detect_variants(text: str, var_pats: dict[str, re.Pattern]) -> list[str]:
    """Return canonical variant names matched (whole-word) in text."""
    return [c for c, p in var_pats.items() if p.search(text)]


def detect_sizes(text: str, categories: list[str]) -> dict[str, list[str]]:
    """
    Return {category: [matched_canonical_sizes]}.
    Multiple values per category = ambiguous.
    """
    found: dict[str, list[str]] = {}
    for cat in categories:
        size_map = _SIZE_PATS.get(cat)
        if size_map is None:
            continue
        matched = [
            size_can
            for size_can, frags in size_map.items()
            if re.search("|".join(frags), text, re.IGNORECASE)
        ]
        if matched:
            found[cat] = matched
    return found


def detect_categories_from_suffix(suffix: str) -> set[str]:
    """Return canonical categories whose keywords appear in the suffix."""
    found: set[str] = set()
    suffix_lower = suffix.lower()
    for canonical, keywords in _SUFFIX_CAT_KEYWORDS.items():
        if any(kw.lower() in suffix_lower for kw in keywords):
            found.add(canonical)
    return found


def _csv_set(val: str) -> set[str]:
    return {s.strip().lower() for s in val.split(",") if s.strip()}


# ──────────────────────────────────────────────────────────────────────────────
# Per-row analysis
# ──────────────────────────────────────────────────────────────────────────────

def analyse_row(
    row: dict,
    var_pats: dict[str, re.Pattern],
) -> dict:
    """
    Returns a result dict:
      issues  list[str]   mismatch descriptions
      notes   list[str]   informational notes (OK / ambiguous / no-signal)
      var_status  'mismatch' | 'ok' | 'ambiguous' | 'none'
      size_status 'mismatch' | 'ok' | 'ambiguous' | 'none'
      cat_status  'mismatch' | 'ok' | 'none'
    """
    title      = str(row.get("Title DE")            or "").strip()
    stored_cat = str(row.get("Custom Field Produktkategorie") or "").strip()
    stored_sz  = str(row.get("Custom Field Produktgröße")     or "").strip()
    stored_var = str(row.get("Custom Field Produktvariante")  or "").strip()

    suffix = _title_suffix(title)

    issues: list[str] = []
    notes:  list[str] = []

    var_status  = "none"
    size_status = "none"
    cat_status  = "none"

    # ── Variant ──────────────────────────────────────────────────────────────
    found_vars = detect_variants(title, var_pats)
    if len(found_vars) == 0 and suffix:
        # Try suffix alone if nothing found in full title
        found_vars = detect_variants(suffix, var_pats)

    if len(found_vars) == 1:
        fv = found_vars[0]
        if stored_var and fv != stored_var:
            issues.append(f"VARIANT  stored={stored_var!r}  →  title={fv!r}")
            var_status = "mismatch"
        else:
            notes.append(f"variant OK ({fv!r})")
            var_status = "ok"
    elif len(found_vars) > 1:
        notes.append(f"variant: {len(found_vars)} matches — ambiguous ({', '.join(found_vars)})")
        var_status = "ambiguous"
    else:
        notes.append("variant: no match in title")

    # ── Size ─────────────────────────────────────────────────────────────────
    cats          = [s.strip() for s in stored_cat.split(",") if s.strip()]
    sizes_by_cat  = detect_sizes(title, cats or list(_SIZE_PATS.keys()))
    stored_sz_set = _csv_set(stored_sz)

    # For each ambiguous category, retry with suffix
    for cat in list(sizes_by_cat.keys()):
        if len(sizes_by_cat[cat]) > 1 and suffix:
            suffix_sizes = detect_sizes(suffix, [cat])
            if suffix_sizes.get(cat) and len(suffix_sizes[cat]) == 1:
                sizes_by_cat[cat] = suffix_sizes[cat]

    # Also check suffix if no sizes found at all
    if not sizes_by_cat and suffix:
        sizes_by_cat = detect_sizes(suffix, cats or list(_SIZE_PATS.keys()))

    size_statuses: list[str] = []
    for cat, found_sizes in sizes_by_cat.items():
        if len(found_sizes) == 1:
            fs = found_sizes[0]
            if stored_sz_set and fs not in stored_sz_set:
                issues.append(f"SIZE({cat})  stored={stored_sz!r}  →  title={fs!r}")
                size_statuses.append("mismatch")
            else:
                notes.append(f"size({cat}) OK ({fs!r})")
                size_statuses.append("ok")
        else:
            notes.append(f"size({cat}): ambiguous ({'/'.join(found_sizes)})")
            size_statuses.append("ambiguous")

    if "mismatch" in size_statuses:
        size_status = "mismatch"
    elif "ok" in size_statuses:
        size_status = "ok"
    elif "ambiguous" in size_statuses:
        size_status = "ambiguous"

    # ── Category (suffix only) ────────────────────────────────────────────────
    if suffix:
        found_cats = detect_categories_from_suffix(suffix)
        if found_cats:
            stored_cat_set = _csv_set(stored_cat)
            extra = sorted(found_cats - stored_cat_set)  # in title but NOT in stored
            if extra:
                issues.append(
                    f"CATEGORY  stored={stored_cat!r}  →  suffix has extra: {extra}"
                )
                cat_status = "mismatch"
            else:
                notes.append(f"category OK ({sorted(found_cats)})")
                cat_status = "ok"

    return {
        "sku":        str(row.get("SKU") or "").strip(),
        "title":      title,
        "stored_cat": stored_cat,
        "stored_sz":  stored_sz,
        "stored_var": stored_var,
        "issues":     issues,
        "notes":      notes,
        "var_status":  var_status,
        "size_status": size_status,
        "cat_status":  cat_status,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Stats aggregation
# ──────────────────────────────────────────────────────────────────────────────

def _compute_stats(results: list[dict]) -> dict:
    """Aggregate per-attribute and overall counts from analyse_row results."""
    total = len(results)

    def _count(field: str, status: str) -> int:
        return sum(1 for r in results if r[field] == status)

    mismatch_rows = [r for r in results if r["issues"]]

    return {
        "total":          total,
        "total_mismatch": len(mismatch_rows),
        "total_ok":       sum(
            1 for r in results
            if not r["issues"] and any(
                s == "ok" for s in (r["var_status"], r["size_status"], r["cat_status"])
            )
        ),
        "no_signal":      sum(
            1 for r in results
            if not r["issues"] and all(
                s in ("none", "ambiguous")
                for s in (r["var_status"], r["size_status"], r["cat_status"])
            )
        ),
        "var_ok":        _count("var_status", "ok"),
        "var_mismatch":  _count("var_status", "mismatch"),
        "var_ambiguous": _count("var_status", "ambiguous"),
        "var_none":      _count("var_status", "none"),
        "sz_ok":         _count("size_status", "ok"),
        "sz_mismatch":   _count("size_status", "mismatch"),
        "sz_ambiguous":  _count("size_status", "ambiguous"),
        "sz_none":       _count("size_status", "none"),
        "cat_ok":        _count("cat_status", "ok"),
        "cat_mismatch":  _count("cat_status", "mismatch"),
        "cat_none":      _count("cat_status", "none"),
        "mismatch_rows": mismatch_rows,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Google Sheets output helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_or_create_ws(
    spreadsheet: gspread.Spreadsheet,
    name: str,
    rows: int = 200,
    cols: int = 10,
) -> gspread.Worksheet:
    try:
        ws = spreadsheet.worksheet(name)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=name, rows=rows, cols=cols)
    return ws


def _blue_header_req(sheet_id: int, row_idx: int, n_cols: int) -> dict:
    """repeatCell request: blue bold header on one row (0-indexed row_idx)."""
    return {
        "repeatCell": {
            "range": {
                "sheetId":          sheet_id,
                "startRowIndex":    row_idx,
                "endRowIndex":      row_idx + 1,
                "startColumnIndex": 0,
                "endColumnIndex":   n_cols,
            },
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": _BLUE_BG,
                    "textFormat": {
                        "bold": True,
                        "foregroundColor": _BLUE_FG,
                    },
                }
            },
            "fields": "userEnteredFormat(backgroundColor,textFormat)",
        }
    }


def _col_width_req(sheet_id: int, start_col: int, end_col: int, px: int) -> dict:
    """updateDimensionProperties request to set column pixel width."""
    return {
        "updateDimensionProperties": {
            "range": {
                "sheetId":    sheet_id,
                "dimension":  "COLUMNS",
                "startIndex": start_col,
                "endIndex":   end_col,
            },
            "properties": {"pixelSize": px},
            "fields": "pixelSize",
        }
    }


def _write_summary_tab(spreadsheet: gspread.Spreadsheet, stats: dict) -> None:
    """Create / overwrite the title_check_summary tab with formatted counts."""
    ws = _get_or_create_ws(spreadsheet, SUMMARY_TAB, rows=30, cols=6)

    data = [
        # row 0 – big title
        ["Title vs. Attribute Fields \u2014 Quality Check", "", "", "", ""],
        # row 1 – spacer
        ["", "", "", "", ""],
        # row 2 – section label
        ["Overall Stats", "", "", "", ""],
        # row 3 – column headers  (blue)
        ["Metric", "Count", "", "", ""],
        # rows 4-7 – data
        ["Total BOM rows with titles analysed",   stats["total"],          "", "", ""],
        ["Rows with \u22651 mismatch",             stats["total_mismatch"], "", "", ""],
        ["Rows fully checked OK",                 stats["total_ok"],       "", "", ""],
        ["Rows with only ambiguous / no signals", stats["no_signal"],      "", "", ""],
        # row 8 – spacer
        ["", "", "", "", ""],
        # row 9 – section label
        ["Per-Attribute Results", "", "", "", ""],
        # row 10 – column headers  (blue)
        ["Attribute", "OK", "Mismatch", "Ambiguous", "No-Signal"],
        # rows 11-13 – data
        ["Variant",  stats["var_ok"], stats["var_mismatch"], stats["var_ambiguous"], stats["var_none"]],
        ["Size",     stats["sz_ok"],  stats["sz_mismatch"],  stats["sz_ambiguous"],  stats["sz_none"]],
        ["Category", stats["cat_ok"], stats["cat_mismatch"], "\u2014",               stats["cat_none"]],
    ]

    ws.update(data, value_input_option="RAW")

    sid    = ws.id
    n_cols = 5

    spreadsheet.batch_update({"requests": [
        # Title: bold, larger font
        {
            "repeatCell": {
                "range": {
                    "sheetId": sid,
                    "startRowIndex": 0, "endRowIndex": 1,
                    "startColumnIndex": 0, "endColumnIndex": n_cols,
                },
                "cell": {"userEnteredFormat": {
                    "textFormat": {"bold": True, "fontSize": 13},
                }},
                "fields": "userEnteredFormat(textFormat)",
            }
        },
        # Blue header rows
        _blue_header_req(sid, 3,  n_cols),   # "Metric / Count"
        _blue_header_req(sid, 10, n_cols),   # "Attribute / OK / …"
        # Bold section labels (rows 2 and 9)
        {
            "repeatCell": {
                "range": {"sheetId": sid,
                          "startRowIndex": 2, "endRowIndex": 3,
                          "startColumnIndex": 0, "endColumnIndex": 1},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat(textFormat)",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": sid,
                          "startRowIndex": 9, "endRowIndex": 10,
                          "startColumnIndex": 0, "endColumnIndex": 1},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat(textFormat)",
            }
        },
        # Column widths
        _col_width_req(sid, 0, 1, 330),
        _col_width_req(sid, 1, 5, 100),
    ]})
    print(f"[ok] Summary written to '{SUMMARY_TAB}' tab.")


def _write_mismatches_tab(
    spreadsheet: gspread.Spreadsheet,
    mismatch_rows: list[dict],
) -> None:
    """
    Create / overwrite the title_check_mismatches tab.

    Columns: SKU | Title | Produktkategorie | Produktgröße | Produktvariante | Issue
    Formatting: blue bold header row (frozen), light-red background on all data rows.
    """
    n_data = len(mismatch_rows)
    ws = _get_or_create_ws(spreadsheet, MISMATCHES_TAB, rows=n_data + 10, cols=6)

    headers = [
        "SKU", "Title DE",
        "Custom Field Produktkategorie", "Custom Field Produktgröße", "Custom Field Produktvariante",
        "Issue",
    ]
    matrix = [headers] + [
        [
            r["sku"],
            r["title"],
            r["stored_cat"],
            r["stored_sz"],
            r["stored_var"],
            " | ".join(r["issues"]),
        ]
        for r in mismatch_rows
    ]

    ws.update(matrix, value_input_option="RAW")

    sid    = ws.id
    n_cols = len(headers)

    spreadsheet.batch_update({"requests": [
        # Blue bold header row
        _blue_header_req(sid, 0, n_cols),
        # Freeze header row
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sid,
                    "gridProperties": {"frozenRowCount": 1},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        },
        # Light red background on all data rows
        {
            "repeatCell": {
                "range": {
                    "sheetId":          sid,
                    "startRowIndex":    1,
                    "endRowIndex":      1 + n_data,
                    "startColumnIndex": 0,
                    "endColumnIndex":   n_cols,
                },
                "cell": {"userEnteredFormat": {"backgroundColor": _RED_BG}},
                "fields": "userEnteredFormat(backgroundColor)",
            }
        },
        # Column widths
        _col_width_req(sid, 0, 1, 220),   # SKU
        _col_width_req(sid, 1, 2, 430),   # Title
        _col_width_req(sid, 2, 5, 140),   # Cat / Size / Variant
        _col_width_req(sid, 5, 6, 370),   # Issue
    ]})
    print(f"[ok] {n_data} mismatch rows written to '{MISMATCHES_TAB}' tab.")


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Cross-check BOM row Titles against attribute custom columns."
    )
    parser.add_argument("--sheet-url", required=True)
    parser.add_argument(
        "--show-ok", action="store_true",
        help="Also print rows that checked out OK (no mismatches).",
    )
    args = parser.parse_args()

    mappings = Mappings()
    var_pats = _build_var_patterns(mappings)

    print("Opening sheet …")
    spreadsheet = open_sheet(args.sheet_url)
    rows        = read_tab(spreadsheet, TAB_NAME)
    print(f"      {len(rows)} rows loaded.\n")

    bom_rows = [
        r for r in rows
        if str(r.get("BOM_SKUs") or "").strip()
        and str(r.get("Title DE") or "").strip()
        and str(r.get("Action") or "").strip().lower() != "delete"
    ]
    print(f"Analysing {len(bom_rows)} BOM rows with titles (deleted rows excluded) …\n")

    results = [analyse_row(r, var_pats) for r in bom_rows]
    stats   = _compute_stats(results)

    # ── Mismatch report (stdout) ───────────────────────────────────────────────
    mismatch_rows = stats["mismatch_rows"]
    if mismatch_rows:
        print("=" * 72)
        print(f"MISMATCHES  ({stats['total_mismatch']} row(s))")
        print("=" * 72)
        print()
        for r in mismatch_rows:
            print(f"  SKU:    {r['sku']}")
            print(f"  Title:  {r['title'][:115]}")
            print(f"  Stored: cat={r['stored_cat']!r}  size={r['stored_sz']!r}  var={r['stored_var']!r}")
            for issue in r["issues"]:
                print(f"  !! {issue}")
            print()
    else:
        print("No mismatches found.\n")

    # ── OK report (optional stdout) ───────────────────────────────────────────
    if args.show_ok:
        ok_rows = [r for r in results if not r["issues"] and
                   any(s == "ok" for s in (r["var_status"], r["size_status"], r["cat_status"]))]
        if ok_rows:
            print("=" * 72)
            print(f"CHECKED OK  ({len(ok_rows)} row(s))")
            print("=" * 72)
            print()
            for r in ok_rows:
                print(f"  SKU: {r['sku']}")
                for note in r["notes"]:
                    print(f"    {note}")
                print()

    # ── Summary (stdout) ──────────────────────────────────────────────────────
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"  Total BOM rows with titles analysed : {stats['total']}")
    print(f"  Rows with \u22651 mismatch               : {stats['total_mismatch']}")
    print(f"  Rows fully checked OK               : {stats['total_ok']}")
    print(f"  Rows with only ambiguous/no signals  : {stats['no_signal']}")
    print()
    print(f"  Variant   — ok={stats['var_ok']:4d}  mismatch={stats['var_mismatch']:3d}  ambiguous={stats['var_ambiguous']:4d}  no-signal={stats['var_none']:4d}")
    print(f"  Size      — ok={stats['sz_ok']:4d}  mismatch={stats['sz_mismatch']:3d}  ambiguous={stats['sz_ambiguous']:4d}  no-signal={stats['sz_none']:4d}")
    print(f"  Category  — ok={stats['cat_ok']:4d}  mismatch={stats['cat_mismatch']:3d}                     no-signal={stats['cat_none']:4d}")

    # ── Write results to Google Sheet ─────────────────────────────────────────
    print(f"\nWriting results to sheet …")
    _write_summary_tab(spreadsheet, stats)
    _write_mismatches_tab(spreadsheet, mismatch_rows)
    print(f"\n[done] Sheet URL: {spreadsheet.url}")


if __name__ == "__main__":
    main()
