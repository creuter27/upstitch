#!/usr/bin/env python3
"""
clone_gdocs_from_sheet.py

Scans every tab of a Google Sheet for Google Drive file links stored in any
of the four possible locations:
  1. Cell formula / value text (e.g. =HYPERLINK("url", "text"))
  2. Cell-level hyperlink metadata (Insert > Link on the whole cell)
  3. textFormatRuns — inline rich-text runs with individual link URIs
  4. chipRuns — smart chip embeds (rich link preview chips)

Clones every referenced file the user doesn't already own into their Drive
folder, then rewrites all links so the sheet is fully self-contained.

Strategy per file:
  1. Skip if already owned (ownedByMe = true)
  2. Try Drive API copy (preserves everything when copy is allowed)
  3. Fall back to export-as-Office + re-import (works for copy-protected files)

Config: edit config.yaml to change the destination folder name.

Usage:
    python clone_gdocs_from_sheet.py --sheet URL
    python clone_gdocs_from_sheet.py --sheet URL --dry-run
"""

import argparse
import copy
import io
import json
import re
import sys
import time
from pathlib import Path

import requests
import yaml
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

from google_sheets_client import _get_credentials, open_sheet
import gspread.utils

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIG_FILE  = Path(__file__).parent / "config.yaml"
ID_MAP_FILE  = Path(__file__).parent / ".id_map.json"


def load_config():
    if not CONFIG_FILE.exists():
        return {}
    with CONFIG_FILE.open() as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

GDRIVE_URL_RE = re.compile(
    r"https://docs\.google\.com/"
    r"(?:document|spreadsheets|presentation|forms)/d/([a-zA-Z0-9_-]+)"
)
IMPORTRANGE_URL_RE = re.compile(r'IMPORTRANGE\s*\(\s*"([^"]+)"', re.IGNORECASE)
GDRIVE_ID_FROM_URL_RE = re.compile(r"/d/([a-zA-Z0-9_-]+)")

# ---------------------------------------------------------------------------
# MIME helpers
# ---------------------------------------------------------------------------

MIME_GDOC   = "application/vnd.google-apps.document"
MIME_GSHEET = "application/vnd.google-apps.spreadsheet"
MIME_GSLIDE = "application/vnd.google-apps.presentation"

EXPORT_CFG = {
    MIME_GDOC: {
        "office_mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "export_url":  "https://docs.google.com/feeds/download/documents/export/Export?id={id}&exportFormat=docx",
    },
    MIME_GSHEET: {
        "office_mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "export_url":  "https://docs.google.com/spreadsheets/d/{id}/export?format=xlsx",
    },
    MIME_GSLIDE: {
        "office_mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "export_url":  "https://docs.google.com/presentation/d/{id}/export/pptx",
    },
}

# ---------------------------------------------------------------------------
# Drive helpers
# ---------------------------------------------------------------------------


def build_services(creds):
    drive  = build("drive",  "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)
    return drive, sheets


def get_file_info(drive, file_id):
    try:
        return drive.files().get(
            fileId=file_id, fields="id,name,mimeType,ownedByMe"
        ).execute()
    except HttpError as e:
        if e.resp.status in (403, 404):
            return None
        raise


def find_or_create_folder(drive, folder_name):
    query = (
        f"name='{folder_name}' "
        f"and mimeType='application/vnd.google-apps.folder' "
        f"and trashed=false"
    )
    files = drive.files().list(q=query, fields="files(id,name)").execute().get("files", [])
    if files:
        fid = files[0]["id"]
        print(f"[folder] Using existing '{folder_name}' (id={fid})")
        return fid
    folder = drive.files().create(
        body={"name": folder_name, "mimeType": "application/vnd.google-apps.folder"},
        fields="id",
    ).execute()
    fid = folder["id"]
    print(f"[folder] Created '{folder_name}' (id={fid})")
    return fid


def try_drive_copy(drive, file_id, name, folder_id):
    try:
        return drive.files().copy(
            fileId=file_id, body={"name": name, "parents": [folder_id]}
        ).execute()["id"]
    except HttpError as e:
        if e.resp.status in (400, 403):
            return None
        raise


def export_and_upload(drive, creds, file_id, file_info, folder_id):
    mime = file_info["mimeType"]
    cfg  = EXPORT_CFG.get(mime)
    if not cfg:
        return None
    resp = requests.get(
        cfg["export_url"].format(id=file_id),
        headers={"Authorization": f"Bearer {creds.token}"},
        timeout=60,
    )
    if resp.status_code != 200:
        print(f"    [error] Export HTTP {resp.status_code}")
        return None
    return drive.files().create(
        body={"name": file_info["name"], "mimeType": mime, "parents": [folder_id]},
        media_body=MediaIoBaseUpload(
            io.BytesIO(resp.content), mimetype=cfg["office_mime"], resumable=True
        ),
        fields="id",
    ).execute()["id"]


def find_existing_clone(drive, file_name, folder_id):
    """Return the ID of an existing file with file_name inside folder_id, or None."""
    # Escape single quotes in name for Drive query
    safe_name = file_name.replace("'", "\\'")
    query = f"name='{safe_name}' and '{folder_id}' in parents and trashed=false"
    results = drive.files().list(q=query, fields="files(id,name)").execute()
    files = results.get("files", [])
    return files[0]["id"] if files else None


def clone_one(drive, creds, file_id, folder_id):
    """
    Returns (new_id, method).  method = 'already-owned' | 'existing-clone' |
    'copy' | 'export+upload' | 'failed' | 'not-found-or-no-access' | 'unsupported-mime:...'
    """
    info = get_file_info(drive, file_id)
    if not info:
        return None, "not-found-or-no-access"
    if info.get("ownedByMe"):
        return None, "already-owned"

    mime = info["mimeType"]
    name = info["name"]
    if mime not in EXPORT_CFG:
        return None, f"unsupported-mime:{mime}"

    # Reuse an existing clone in the destination folder (handles interrupted runs)
    existing_id = find_existing_clone(drive, name, folder_id)
    if existing_id:
        print(f"  [reuse]  '{name}' → {existing_id}")
        return existing_id, "existing-clone"

    print(f"  Cloning '{name}' …")

    new_id = try_drive_copy(drive, file_id, name, folder_id)
    if new_id:
        print(f"    [copy]           → {new_id}")
        return new_id, "copy"

    print(f"    [copy restricted] → trying export+upload …")
    new_id = export_and_upload(drive, creds, file_id, info, folder_id)
    if new_id:
        print(f"    [export+upload]  → {new_id}")
        return new_id, "export+upload"

    return None, "failed"


# ---------------------------------------------------------------------------
# ID extraction helpers
# ---------------------------------------------------------------------------


def _ids_from_text(text):
    """Return set of Google Drive file IDs found in a string."""
    if not text:
        return set()
    ids = set(GDRIVE_URL_RE.findall(text))
    for url in IMPORTRANGE_URL_RE.findall(text):
        m = GDRIVE_ID_FROM_URL_RE.search(url)
        if m:
            ids.add(m.group(1))
    return ids


def _replace_ids(text, id_map):
    for old_id, new_id in id_map.items():
        text = text.replace(old_id, new_id)
    return text


# ---------------------------------------------------------------------------
# Sheet scanning — one API call, all four link locations
# ---------------------------------------------------------------------------


def scan_all_tabs(sheets_svc, spreadsheet_id):
    """
    Returns:
        tabs: list of {title, sheet_id, cells}
              Each cell dict: {r0, c0, val, hyperlink, text_format_runs, chip_runs}
        all_file_ids: set of Drive file IDs found anywhere
        all_importrange_ids: set
    """
    print("Fetching full cell data …")
    result = sheets_svc.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        includeGridData=True,
        fields=(
            "sheets(properties(title,sheetId),"
            "data(rowData(values("
            "userEnteredValue,"
            "hyperlink,"
            "userEnteredFormat(textFormat(link)),"
            "textFormatRuns,"
            "chipRuns"
            "))))"
        ),
    ).execute()

    tabs                = []
    all_file_ids        = set()
    all_importrange_ids = set()

    for sheet in result.get("sheets", []):
        title    = sheet["properties"]["title"]
        sheet_id = sheet["properties"]["sheetId"]
        cells    = []

        for grid_data in sheet.get("data", []):
            for r, row_data in enumerate(grid_data.get("rowData", [])):
                for c, cell in enumerate(row_data.get("values", [])):
                    # 1. formula / plain value
                    uev = cell.get("userEnteredValue", {})
                    if "formulaValue" in uev:
                        val = uev["formulaValue"]
                    elif "stringValue" in uev:
                        val = uev["stringValue"]
                    elif "numberValue" in uev:
                        val = str(uev["numberValue"])
                    else:
                        val = ""

                    # 2. cell-level hyperlink
                    hyperlink = cell.get("hyperlink") or (
                        cell.get("userEnteredFormat", {})
                            .get("textFormat", {})
                            .get("link", {})
                            .get("uri")
                    )

                    # 3. textFormatRuns
                    text_format_runs = cell.get("textFormatRuns", [])

                    # 4. chipRuns (smart chip embeds)
                    chip_runs = cell.get("chipRuns", [])

                    if not val and not hyperlink and not text_format_runs and not chip_runs:
                        continue

                    cells.append({
                        "r0": r, "c0": c,
                        "val": val,
                        "is_formula": "formulaValue" in uev,
                        "hyperlink": hyperlink,
                        "text_format_runs": text_format_runs,
                        "chip_runs": chip_runs,
                    })

                    # Collect all referenced IDs
                    for fid in _ids_from_text(val):
                        all_file_ids.add(fid)
                    for fid in _ids_from_text(hyperlink):
                        all_file_ids.add(fid)
                    for run in text_format_runs:
                        uri = run.get("format", {}).get("link", {}).get("uri", "")
                        for fid in _ids_from_text(uri):
                            all_file_ids.add(fid)
                    for chip_run in chip_runs:
                        uri = (
                            chip_run.get("chip", {})
                                    .get("richLinkProperties", {})
                                    .get("uri", "")
                        )
                        for fid in _ids_from_text(uri):
                            all_file_ids.add(fid)

        # Count how many cells in this tab have any Drive links
        drive_count = sum(
            1 for cl in cells if (
                GDRIVE_URL_RE.search(cl["val"] or "")
                or (cl["hyperlink"] and GDRIVE_URL_RE.search(cl["hyperlink"]))
                or any(GDRIVE_URL_RE.search(r.get("format", {}).get("link", {}).get("uri", ""))
                       for r in cl["text_format_runs"])
                or any(GDRIVE_URL_RE.search(
                           cr.get("chip", {}).get("richLinkProperties", {}).get("uri", ""))
                       for cr in cl["chip_runs"])
            )
        )
        if drive_count:
            print(f"  '{title}': {drive_count} Google Drive link(s)")

        tabs.append({"title": title, "sheet_id": sheet_id, "cells": cells})

    return tabs, all_file_ids, all_importrange_ids


# ---------------------------------------------------------------------------
# Link rewriting
# ---------------------------------------------------------------------------


def rewrite_sheet_links(spreadsheet, sheets_svc, tabs, id_map):
    """
    Rewrite every occurrence of an old file ID across all four link locations.
    """
    spreadsheet_id = spreadsheet.id

    for tab in tabs:
        title        = tab["title"]
        sheet_id_num = tab["sheet_id"]
        cells        = tab["cells"]

        try:
            ws = spreadsheet.worksheet(title)
        except Exception:
            print(f"  '{title}': could not open — skipping")
            continue

        value_updates      = []   # gspread batch_update (value / formula cells)
        api_requests       = []   # Sheets API batchUpdate (hyperlink / runs / chips)

        for cl in cells:
            r0, c0 = cl["r0"], cl["c0"]
            cell_range = {
                "sheetId":          sheet_id_num,
                "startRowIndex":    r0,
                "endRowIndex":      r0 + 1,
                "startColumnIndex": c0,
                "endColumnIndex":   c0 + 1,
            }

            # --- 1. formula / value text ---
            new_val = _replace_ids(cl["val"] or "", id_map)
            if new_val != cl["val"]:
                a1 = gspread.utils.rowcol_to_a1(r0 + 1, c0 + 1)
                value_updates.append({"range": a1, "values": [[new_val]]})

            # --- 2. cell-level hyperlink ---
            if cl["hyperlink"]:
                new_hl = _replace_ids(cl["hyperlink"], id_map)
                # Only update hyperlink if val didn't change (avoids double-update)
                if new_hl != cl["hyperlink"] and new_val == cl["val"]:
                    api_requests.append({
                        "updateCells": {
                            "range": cell_range,
                            "rows": [{"values": [{"userEnteredFormat": {
                                "textFormat": {"link": {"uri": new_hl}}
                            }}]}],
                            "fields": "userEnteredFormat.textFormat.link",
                        }
                    })

            # --- 3. textFormatRuns ---
            runs = cl["text_format_runs"]
            if runs:
                new_runs = copy.deepcopy(runs)
                runs_changed = False
                for run in new_runs:
                    old_uri = run.get("format", {}).get("link", {}).get("uri", "")
                    if old_uri:
                        new_uri = _replace_ids(old_uri, id_map)
                        if new_uri != old_uri:
                            run["format"]["link"]["uri"] = new_uri
                            runs_changed = True
                if runs_changed:
                    api_requests.append({
                        "updateCells": {
                            "range": cell_range,
                            "rows": [{"values": [{"textFormatRuns": new_runs}]}],
                            "fields": "textFormatRuns",
                        }
                    })

            # --- 4. chipRuns (smart chip embeds) ---
            # The API only allows updating chipRuns on plain string cells (not formulas).
            chips = cl["chip_runs"]
            if chips and not cl.get("is_formula"):
                new_chips = copy.deepcopy(chips)
                chips_changed = False
                for chip_run in new_chips:
                    rlp = chip_run.get("chip", {}).get("richLinkProperties", {})
                    old_uri = rlp.get("uri", "")
                    if old_uri:
                        new_uri = _replace_ids(old_uri, id_map)
                        if new_uri != old_uri:
                            chip_run["chip"]["richLinkProperties"]["uri"] = new_uri
                            chips_changed = True
                if chips_changed:
                    api_requests.append({
                        "updateCells": {
                            "range": cell_range,
                            "rows": [{"values": [{"chipRuns": new_chips}]}],
                            "fields": "chipRuns",
                        }
                    })

        changed = len(value_updates) + len(api_requests)
        if not changed:
            print(f"  '{title}': nothing to update")
            continue

        if value_updates:
            ws.batch_update(value_updates, value_input_option="USER_ENTERED")

        # Send API requests one at a time so a bad chipRun doesn't block the rest
        for req in api_requests:
            req_type = list(req.keys())[0]
            fields = req.get(req_type, {}).get("fields", "")
            for attempt in range(3):
                try:
                    sheets_svc.spreadsheets().batchUpdate(
                        spreadsheetId=spreadsheet_id,
                        body={"requests": [req]},
                    ).execute()
                    break
                except HttpError as exc:
                    if exc.resp.status == 400 and "chip" in str(exc).lower():
                        # API rejects chipRuns on computed cells — skip silently
                        print(f"    [warn] chip update skipped (computed cell)")
                        break
                    if attempt < 2:
                        wait = 10 * (attempt + 1)
                        print(f"    [retry {attempt+1}] {exc} — waiting {wait}s …")
                        time.sleep(wait)
                    else:
                        print(f"    [error] {fields} update failed: {exc}")
                        break
                except Exception as exc:
                    if attempt < 2:
                        wait = 10 * (attempt + 1)
                        print(f"    [retry {attempt+1}] {exc} — waiting {wait}s …")
                        time.sleep(wait)
                    else:
                        print(f"    [error] {fields} update failed: {exc}")
                        break

        parts = []
        if value_updates:
            parts.append(f"{len(value_updates)} value/formula")
        if api_requests:
            parts.append(f"{len(api_requests)} hyperlink/chip")
        print(f"  '{title}': updated {', '.join(parts)} cell(s)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", required=True, help="Google Sheet URL or ID")
    parser.add_argument("--dry-run", action="store_true",
                        help="Scan only, no cloning or sheet updates")
    parser.add_argument("--rewrite-only", action="store_true",
                        help="Skip cloning; rewrite the sheet using the saved .id_map.json")
    args = parser.parse_args()

    config             = load_config()
    destination_folder = config.get("destination_folder", "Cloned Docs")
    print(f"Destination folder: '{destination_folder}'  (edit config.yaml to change)\n")

    creds             = _get_credentials()
    drive, sheets_svc = build_services(creds)
    spreadsheet       = open_sheet(args.sheet)
    sheet_id          = spreadsheet.id
    print(f"Opened sheet: '{spreadsheet.title}'  (id={sheet_id})\n")

    # Step 1: scan
    tabs, all_file_ids, all_importrange_ids = scan_all_tabs(sheets_svc, sheet_id)
    all_file_ids.discard(sheet_id)
    all_importrange_ids.discard(sheet_id)
    all_to_clone = all_file_ids | all_importrange_ids

    print(f"\n{'='*60}")
    print(f"Total unique referenced files: {len(all_to_clone)}")

    if args.dry_run:
        print("\n[dry-run] Stopping here.")
        return

    # --rewrite-only: load saved id_map and skip cloning
    if args.rewrite_only:
        if not ID_MAP_FILE.exists():
            print(f"\n[error] No saved id_map found at {ID_MAP_FILE}. Run without --rewrite-only first.")
            return
        id_map = json.loads(ID_MAP_FILE.read_text())
        print(f"[rewrite-only] Loaded {len(id_map)} mapping(s) from {ID_MAP_FILE.name}")
    else:
        if not all_to_clone:
            print("Nothing to clone.")
            return

        # Step 2: destination folder
        folder_id = find_or_create_folder(drive, destination_folder)

        # Step 3: clone
        print(f"\n{'='*60}")
        print("Cloning files …\n")
        id_map   = {}
        skipped  = 0
        failures = []

        for file_id in sorted(all_to_clone):
            new_id, method = clone_one(drive, creds, file_id, folder_id)
            if method == "already-owned":
                skipped += 1
            elif new_id:
                id_map[file_id] = new_id
            else:
                failures.append((file_id, method))

        reused = sum(1 for fid in id_map if True)  # all entries in id_map
        new_count = sum(1 for _ in id_map)
        print(f"\nMapped {new_count} file(s) (new clones + reused), {skipped} already owned (skipped).")
        if failures:
            print(f"Failed ({len(failures)}):")
            for fid, reason in failures:
                print(f"  {fid}  — {reason}")

        if not id_map:
            print("No new files cloned — nothing to rewrite.")
            return

        # Save id_map so a --rewrite-only run can pick it up if the rewrite is interrupted
        # Merge with any existing saved map (accumulate across runs)
        saved = json.loads(ID_MAP_FILE.read_text()) if ID_MAP_FILE.exists() else {}
        saved.update(id_map)
        ID_MAP_FILE.write_text(json.dumps(saved, indent=2))
        print(f"[saved] id_map written to {ID_MAP_FILE.name} ({len(saved)} total entries)")

    # Step 4: rewrite
    print(f"\n{'='*60}")
    print("Rewriting links in the sheet …\n")
    rewrite_sheet_links(spreadsheet, sheets_svc, tabs, id_map)

    print("\nDone. Your sheet is now self-contained.")
    if not args.rewrite_only and failures:
        print(f"\nNote: {len(failures)} file(s) could not be cloned — original links remain.")


if __name__ == "__main__":
    main()
