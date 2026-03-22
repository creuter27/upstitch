"""
Create missing physical products in Billbee and append them to the Google Sheet.

A "missing" physical product is a SKU referenced in a listing row's BOM_SKUs
that has no corresponding physical row in the Google Sheet.

Workflow
--------
Phase 1 — Identify:
  Read the sheet; find all BOM SKUs that have no matching physical row.

Phase 2 — Generate:
  For each missing SKU:
  - Parse the SKU → manufacturer, category, size, variant, color.
  - Find a template: an existing physical row with the same (manufacturer,
    category, size) that has a Billbee Id.
  - Fetch the full template product from Billbee.
  - Clone it: swap SKU + variant/design custom fields + title, fill
    weight/dims/cost from product_specs.yaml.

Phase 3 — Create (--execute only):
  POST each new product to Billbee → receive new Billbee Id.
  Fetch the created product back to get the complete record.
  Flatten it (same logic as download_to_sheet.py).
  APPEND the new rows to the Google Sheet — no overwrite of existing data.

Phase 4 — Verify:
  Report which listing BOM_SKUs now resolve and which still need attention.
  Run validate_bom_skus.py afterwards to do a full cross-check.

Usage:
  python execution/create_missing_physical.py --sheet-url URL            # dry-run
  python execution/create_missing_physical.py --sheet-url URL --execute  # create
"""

import argparse
import copy
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from google_sheets_client import open_sheet, read_tab
from billbee_client import BillbeeClient
from execution.mappings_loader import Mappings
from execution.sku_parser import parse_sku
from execution.specs_loader import SpecsLoader
from execution.download_to_sheet import flatten_product

TAB_NAME   = "ProductList"
COL_SKU    = "SKU"
COL_MFR    = "Manufacturer"
COL_CAT    = "Custom Field Produktkategorie"
COL_SIZE   = "Custom Field Produktgröße"
COL_BOM    = "BOM_SKUs"
COL_ACTION = "Action"
COL_ID     = "Id"


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _is_delete(row: dict) -> bool:
    return str(row.get(COL_ACTION) or "").strip().lower() == "delete"


def _is_listing(row: dict) -> bool:
    return bool(str(row.get(COL_BOM) or "").strip())


def _bom_skus(row: dict) -> list[str]:
    raw = str(row.get(COL_BOM) or "").strip()
    return [s.strip() for s in raw.split("|") if s.strip()] if raw else []


def _int_id(val) -> int | None:
    try:
        return int(float(str(val))) if str(val).strip() else None
    except (ValueError, TypeError):
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Display name conversion  (canonical key → German display name)
# e.g. "baer" → "Bär", "loewe" → "Löwe", "koala" → "Koala"
# ──────────────────────────────────────────────────────────────────────────────

_UMLAUT = [("ae", "ä"), ("oe", "ö"), ("ue", "ü")]


def _display_name(key: str) -> str:
    if not key:
        return ""
    name = key[0].upper() + key[1:]
    for ascii_pair, umlaut in _UMLAUT:
        name = name.replace(ascii_pair, umlaut)
    return name


# ──────────────────────────────────────────────────────────────────────────────
# Custom-field helpers
# ──────────────────────────────────────────────────────────────────────────────

def _cf_value(product: dict, field_name: str, field_defs: dict[int, str]) -> str:
    name_to_id = {v: k for k, v in field_defs.items()}
    def_id = name_to_id.get(field_name)
    if def_id is None:
        return ""
    for cf in (product.get("CustomFields") or []):
        if (cf.get("DefinitionId") or cf.get("Id")) == def_id:
            return str(cf.get("Value") or "")
    return ""


def _set_cf_value(product: dict, field_name: str, value: str,
                  field_defs: dict[int, str]) -> None:
    name_to_id = {v: k for k, v in field_defs.items()}
    def_id = name_to_id.get(field_name)
    if def_id is None:
        return
    for cf in (product.setdefault("CustomFields", [])):
        if (cf.get("DefinitionId") or cf.get("Id")) == def_id:
            cf["Value"] = value
            return
    product["CustomFields"].append({"DefinitionId": def_id, "Value": value})


# ──────────────────────────────────────────────────────────────────────────────
# Title update
# ──────────────────────────────────────────────────────────────────────────────

def _update_titles(product: dict, old_text: str, new_text: str) -> bool:
    """
    Replace old_text with new_text in all multilingual Title entries
    (case-insensitive).  Returns True if at least one substitution was made.
    """
    changed = False
    for entry in (product.get("Title") or []):
        text = entry.get("Text") or ""
        if old_text and old_text.lower() in text.lower():
            entry["Text"] = re.sub(re.escape(old_text), new_text, text,
                                   flags=re.IGNORECASE)
            changed = True
    return changed


# ──────────────────────────────────────────────────────────────────────────────
# Phase 1 — identify missing physical SKUs
# ──────────────────────────────────────────────────────────────────────────────

def find_missing_physical_skus(rows: list[dict]) -> set[str]:
    """
    Return the set of SKUs referenced in any listing BOM_SKUs that have no
    matching physical (non-listing, non-deleted) row in the sheet.
    """
    physical_skus = {
        str(row.get(COL_SKU) or "").strip()
        for row in rows
        if not _is_delete(row) and not _is_listing(row)
        and str(row.get(COL_SKU) or "").strip()
    }
    missing: set[str] = set()
    for row in rows:
        if _is_delete(row) or not _is_listing(row):
            continue
        for sku in _bom_skus(row):
            if sku and sku not in physical_skus:
                missing.add(sku)
    return missing


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2 — find template + build new product dict
# ──────────────────────────────────────────────────────────────────────────────

def find_template_row(rows: list[dict], mfr_canonical: str, cat: str,
                      size: str, mappings: Mappings) -> dict | None:
    """
    Find an existing physical row with the same (manufacturer, category, size)
    that has a Billbee Id.  Manufacturer comparison uses canonical codes so that
    "Trixie Baby" in the sheet matches "TRX" from the parsed SKU.
    """
    for row in rows:
        if _is_delete(row) or _is_listing(row):
            continue
        if not _int_id(row.get(COL_ID)):
            continue
        row_mfr_raw  = str(row.get(COL_MFR)  or "").strip()
        row_mfr_can  = (mappings.canonical_manufacturer(row_mfr_raw) or
                        row_mfr_raw).upper()
        row_cat      = str(row.get(COL_CAT)  or "").strip().lower()
        row_size     = str(row.get(COL_SIZE) or "").strip().lower()
        if (row_mfr_can  == mfr_canonical.upper() and
                row_cat  == cat.lower() and
                row_size == size.lower()):
            return row
    return None


def build_new_product(
    missing_sku:     str,
    parsed:          dict,
    template_billbee: dict,
    field_defs:      dict[int, str],
    specs:           SpecsLoader,
) -> dict:
    """
    Clone the Billbee template product and adapt it for the missing physical SKU.
    """
    product = copy.deepcopy(template_billbee)

    # Strip Billbee-assigned identity fields so POST creates a new record
    for field in ("Id", "CreatedAt", "UpdatedAt", "InvoiceAddress"):
        product.pop(field, None)

    # ── SKU ──────────────────────────────────────────────────────────────────
    product["SKU"] = missing_sku

    # ── Variant / design display names ───────────────────────────────────────
    new_variant_disp = _display_name(parsed.get("variant") or "")
    new_color_disp   = _display_name(parsed.get("color")   or "")

    # Determine old text from the template's custom fields for title replacement
    old_variant = _cf_value(template_billbee, "Produktvariante", field_defs)
    old_design  = _cf_value(template_billbee, "Produktdesign",   field_defs)
    old_text    = old_variant or old_design   # whichever is populated

    # Update custom fields
    cf_names = set(field_defs.values())
    if "Produktvariante" in cf_names:
        _set_cf_value(product, "Produktvariante", new_variant_disp, field_defs)
    if "Produktdesign" in cf_names:
        _set_cf_value(product, "Produktdesign",   new_variant_disp, field_defs)
    if "Produktfarbe" in cf_names and new_color_disp:
        _set_cf_value(product, "Produktfarbe", new_color_disp, field_defs)

    # ── Title ─────────────────────────────────────────────────────────────────
    new_text = new_variant_disp or new_color_disp
    if new_text:
        replaced = _update_titles(product, old_text, new_text)
        if not replaced:
            # Fallback: append the new variant to every title
            for entry in (product.get("Title") or []):
                entry["Text"] = (entry.get("Text") or "").rstrip() + f" – {new_text}"

    # ── Physical dimensions / cost from product_specs.yaml ───────────────────
    mfr  = parsed.get("manufacturer") or ""
    cat  = parsed.get("category")     or ""
    size = parsed.get("size")         or ""
    spec = specs.lookup(mfr, cat, size)
    if spec.get("weight")  is not None:
        product["WeightNet"]   = spec["weight"]
        product["WeightGross"] = spec["weight"]
    if spec.get("length")  is not None: product["LengthCm"]  = spec["length"]
    if spec.get("width")   is not None: product["WidthCm"]   = spec["width"]
    if spec.get("height")  is not None: product["HeightCm"]  = spec["height"]
    if spec.get("cost")    is not None: product["CostPrice"] = spec["cost"]

    # Physical product has no BOM
    product["BillOfMaterial"] = []

    # Ensure Type=1 (physical)
    product["Type"] = 1

    return product


# ──────────────────────────────────────────────────────────────────────────────
# Phase 3 — append new rows to sheet (safe: no overwrite)
# ──────────────────────────────────────────────────────────────────────────────

def append_rows_to_sheet(spreadsheet, new_rows: list[dict]) -> None:
    """
    Append rows to the 'upload' tab using gspread append_rows.
    Matches existing column order from the sheet header — columns not present
    in the header are silently dropped; missing columns are filled with "".
    """
    ws = spreadsheet.worksheet(TAB_NAME)
    headers = ws.row_values(1)
    matrix  = [[str(row.get(h, "")) for h in headers] for row in new_rows]
    ws.append_rows(matrix, value_input_option="RAW")
    print(f"[ok] Appended {len(matrix)} row(s) to '{TAB_NAME}' tab.")


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Create missing physical products in Billbee and append to the Google Sheet."
    )
    parser.add_argument("--sheet-url", required=True)
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually create products in Billbee and append to sheet. "
             "Default is dry-run (plan only).",
    )
    args = parser.parse_args()

    mappings = Mappings()
    specs    = SpecsLoader(mappings=mappings)
    client   = BillbeeClient()

    print("Opening sheet …")
    ss   = open_sheet(args.sheet_url)
    rows = read_tab(ss, TAB_NAME)
    print(f"  {len(rows)} rows loaded.")

    # ── Phase 1 ───────────────────────────────────────────────────────────────
    missing = find_missing_physical_skus(rows)
    print(f"\nMissing physical SKUs referenced by listing BOM_SKUs: {len(missing)}")
    if not missing:
        print("Nothing to create. All BOM SKUs are present in the sheet.")
        return

    # ── Phase 2 ───────────────────────────────────────────────────────────────
    print("\nFetching custom field definitions …")
    field_defs = client.get_custom_field_definitions()

    planned:     list[tuple[str, dict]] = []   # (missing_sku, new_billbee_dict)
    no_template: list[tuple[str, str]]  = []   # (missing_sku, reason)

    print("\nBuilding new product dicts …")
    for sku in sorted(missing):
        parsed = parse_sku(sku, mappings)
        mfr    = parsed.get("manufacturer") or ""
        cat    = parsed.get("category")     or ""
        size   = parsed.get("size")         or ""
        variant = parsed.get("variant")     or ""

        if not cat:
            reason = f"could not parse category from SKU '{sku}'"
            print(f"  [skip] {sku:50s}  {reason}")
            no_template.append((sku, reason))
            continue

        template_row = find_template_row(rows, mfr, cat, size, mappings)
        if template_row is None:
            reason = f"no template found for mfr={mfr!r} cat={cat!r} size={size!r}"
            print(f"  [skip] {sku:50s}  {reason}")
            no_template.append((sku, reason))
            continue

        template_id  = _int_id(template_row.get(COL_ID))
        template_sku = template_row.get(COL_SKU, "")
        print(f"  {sku:50s}  template={template_sku!r}  (Billbee Id={template_id})")

        template_billbee = client.get_product_by_id(template_id)
        new_product      = build_new_product(sku, parsed, template_billbee,
                                             field_defs, specs)
        planned.append((sku, new_product))

    print(f"\n{'─'*60}")
    print(f"  Ready to create : {len(planned)}")
    print(f"  Skipped         : {len(no_template)}")

    # ── Dry-run report ────────────────────────────────────────────────────────
    if not args.execute:
        print(f"\n[DRY-RUN] Planned products:\n")
        for sku, prod in planned:
            titles  = prod.get("Title") or []
            de_title = next((t["Text"] for t in titles
                             if t.get("LanguageCode") == "DE"), "")
            variant_val = _cf_value(prod, "Produktvariante", field_defs)
            print(f"  SKU     : {sku}")
            print(f"  Title   : {de_title}")
            print(f"  Variant : {variant_val}")
            w = prod.get("WeightNet", "—")
            l = prod.get("LengthCm", "—")
            wi = prod.get("WidthCm", "—")
            h = prod.get("HeightCm", "—")
            print(f"  Dims    : weight={w} kg  L={l} W={wi} H={h} cm")
            print(f"  Cost    : {prod.get('CostPrice', '—')} EUR")
            print()

        if no_template:
            print("Skipped (no template or unparseable SKU):")
            for sku, reason in no_template:
                print(f"  {sku:50s}  {reason}")

        print("\nRe-run with --execute to create in Billbee and append to sheet.")
        return

    # ── Phase 3: create in Billbee + append to sheet ─────────────────────────
    created_rows: list[dict] = []
    failed:       list[str]  = []

    print(f"\nCreating {len(planned)} products in Billbee …")
    for sku, prod in planned:
        print(f"  Creating {sku} …", end=" ", flush=True)
        try:
            created = client.create_product(prod)
            new_id  = _int_id(created.get("Id"))
            if not new_id:
                print(f"ERROR — no Id in response: {created}")
                failed.append(sku)
                continue
            print(f"→ Id={new_id}")

            # Fetch the full record to get all server-populated fields
            full = client.get_product_by_id(new_id)
            flat = flatten_product(full, field_defs)
            created_rows.append(flat)

        except Exception as exc:
            print(f"ERROR — {exc}")
            failed.append(sku)

    # Append new rows to sheet (never overwrites)
    if created_rows:
        print(f"\nAppending {len(created_rows)} new row(s) to the sheet …")
        append_rows_to_sheet(ss, created_rows)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print(f"  Created in Billbee + added to sheet : {len(created_rows)}")
    print(f"  Failed (API error)                  : {len(failed)}")
    print(f"  Skipped (no template / parse error) : {len(no_template)}")

    if failed:
        print("\nFailed SKUs:")
        for s in failed:
            print(f"  {s}")
    if no_template:
        print("\nSkipped SKUs:")
        for s, reason in no_template:
            print(f"  {s:50s}  {reason}")

    if created_rows:
        print(
            "\nNext steps:"
            "\n  1. Run apply_product_specs.py to fill weight/dims for the new rows."
            "\n  2. Run validate_bom_skus.py to confirm all listing BOM refs resolve."
            "\n  3. Run assign_taric.py to assign TARIC codes to the new rows."
        )

    print("\n[done]")


if __name__ == "__main__":
    main()
