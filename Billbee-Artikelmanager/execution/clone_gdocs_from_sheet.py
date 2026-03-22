#!/usr/bin/env python3
"""
clone_gdocs_from_sheet.py

Scans every tab of a Google Sheet for Google Drive file links and IMPORTRANGE
references, clones every referenced file into your own Drive, then rewrites all
links in the sheet so it is fully self-contained.

Strategy per file:
  1. Try Drive API copy  (fast, preserves everything, works when copy is allowed)
  2. Fall back to export-as-Office-format + re-import as Google format
     (works even for copy-protected files; preserves all formatting)

IMPORTRANGE formulas are also rewritten: the referenced spreadsheet is cloned
the same way and the formula URL is updated in-place.

Usage:
    python execution/clone_gdocs_from_sheet.py --sheet URL
    python execution/clone_gdocs_from_sheet.py --sheet URL --dry-run
"""

import argparse
import io
import re
import sys
import time
from pathlib import Path

import requests
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

sys.path.insert(0, str(Path(__file__).parent.parent))
from google_sheets_client import _get_credentials, open_sheet
import gspread.utils

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Matches Google Docs/Sheets/Slides/Forms URLs and captures the file ID
GDRIVE_URL_RE = re.compile(
    r"https://docs\.google\.com/"
    r"(?:document|spreadsheets|presentation|forms)/d/([a-zA-Z0-9_-]+)"
)

# Matches IMPORTRANGE(url, range) and captures the URL argument
IMPORTRANGE_URL_RE = re.compile(
    r'IMPORTRANGE\s*\(\s*"([^"]+)"',
    re.IGNORECASE,
)

# Extracts a spreadsheet/file ID from any Google Drive URL
GDRIVE_ID_FROM_URL_RE = re.compile(r"/d/([a-zA-Z0-9_-]+)")

# ---------------------------------------------------------------------------
# MIME helpers
# ---------------------------------------------------------------------------

MIME_GDOC   = "application/vnd.google-apps.document"
MIME_GSHEET = "application/vnd.google-apps.spreadsheet"
MIME_GSLIDE = "application/vnd.google-apps.presentation"

# Export format: mime → (office extension, office mime, google mime after re-import)
EXPORT_CFG = {
    MIME_GDOC: {
        "ext":         "docx",
        "office_mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "export_url":  "https://docs.google.com/feeds/download/documents/export/Export?id={id}&exportFormat=docx",
    },
    MIME_GSHEET: {
        "ext":         "xlsx",
        "office_mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "export_url":  "https://docs.google.com/spreadsheets/d/{id}/export?format=xlsx",
    },
    MIME_GSLIDE: {
        "ext":         "pptx",
        "office_mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "export_url":  "https://docs.google.com/presentation/d/{id}/export/pptx",
    },
}


# ---------------------------------------------------------------------------
# Drive helpers
# ---------------------------------------------------------------------------

def build_drive(creds):
    return build("drive", "v3", credentials=creds)


def get_file_info(drive, file_id):
    """Return {id, name, mimeType} or None if file not found / no access."""
    try:
        return drive.files().get(fileId=file_id, fields="id,name,mimeType").execute()
    except HttpError as e:
        if e.resp.status in (403, 404):
            return None
        raise


def try_drive_copy(drive, file_id, name):
    """Attempt a Drive copy. Returns new file ID, or None if copy is restricted."""
    try:
        result = drive.files().copy(fileId=file_id, body={"name": name}).execute()
        return result["id"]
    except HttpError as e:
        if e.resp.status in (400, 403):
            return None
        raise


def export_and_upload(drive, creds, file_id, file_info):
    """
    Export the file as an Office format and re-upload it as a Google Workspace
    file.  Works even when the source document has copy-protection disabled.
    Preserves all formatting (tables, styles, images, etc.).
    """
    mime = file_info["mimeType"]
    name = file_info["name"]
    cfg  = EXPORT_CFG.get(mime)

    if cfg is None:
        print(f"    [skip] No export config for mime type '{mime}'")
        return None

    export_url = cfg["export_url"].format(id=file_id)
    resp = requests.get(
        export_url,
        headers={"Authorization": f"Bearer {creds.token}"},
        timeout=60,
    )
    if resp.status_code != 200:
        print(f"    [error] Export HTTP {resp.status_code} for '{name}'")
        return None

    file_meta = {"name": name, "mimeType": mime}
    media = MediaIoBaseUpload(
        io.BytesIO(resp.content),
        mimetype=cfg["office_mime"],
        resumable=True,
    )
    new_file = drive.files().create(
        body=file_meta, media_body=media, fields="id"
    ).execute()
    return new_file["id"]


def clone_one(drive, creds, file_id):
    """
    Clone a single Drive file into the authenticated user's Drive.
    Returns (new_id, method) or (None, reason).
    """
    info = get_file_info(drive, file_id)
    if not info:
        return None, "not-found-or-no-access"

    mime = info["mimeType"]
    name = info["name"]

    if mime not in EXPORT_CFG:
        return None, f"unsupported-mime:{mime}"

    print(f"  Cloning '{name}' ({mime.split('.')[-1]}) …")

    new_id = try_drive_copy(drive, file_id, name)
    if new_id:
        print(f"    [copy]           → {new_id}")
        return new_id, "copy"

    print(f"    [copy restricted] → trying export+upload …")
    new_id = export_and_upload(drive, creds, file_id, info)
    if new_id:
        print(f"    [export+upload]  → {new_id}")
        return new_id, "export+upload"

    return None, "failed"


# ---------------------------------------------------------------------------
# Sheet scanning helpers
# ---------------------------------------------------------------------------

def _get_all_values_with_retry(ws, max_retries=5):
    """Call ws.get_all_values with exponential backoff on 429s."""
    import gspread.exceptions
    delay = 15
    for attempt in range(max_retries):
        try:
            return ws.get_all_values(value_render_option="FORMULA")
        except gspread.exceptions.APIError as e:
            if "429" in str(e) and attempt < max_retries - 1:
                print(f"    [rate limit] waiting {delay}s …")
                time.sleep(delay)
                delay *= 2
            else:
                raise


def scan_worksheet(ws):
    """
    Return (file_ids, importrange_sheet_ids, cells) where:
      - file_ids: set of Google Drive file IDs found in plain cell values / formulas
      - importrange_sheet_ids: set of spreadsheet IDs referenced by IMPORTRANGE
      - cells: list of (row1, col1, raw_value) for all non-empty cells
    """
    # Read raw formulas (not computed values)
    all_values = _get_all_values_with_retry(ws)

    file_ids             = set()
    importrange_ids      = set()
    cells                = []

    for r, row in enumerate(all_values):
        for c, val in enumerate(row):
            if not val:
                continue
            val = str(val)  # numbers come back as int/float
            cells.append((r + 1, c + 1, val))  # 1-based

            # Plain URLs or HYPERLINK("url", ...) formulas
            for fid in GDRIVE_URL_RE.findall(val):
                file_ids.add(fid)

            # IMPORTRANGE URLs
            for url in IMPORTRANGE_URL_RE.findall(val):
                m = GDRIVE_ID_FROM_URL_RE.search(url)
                if m:
                    importrange_ids.add(m.group(1))

    return file_ids, importrange_ids, cells


def replace_ids(text, id_map):
    """Replace every old file ID with its corresponding new ID."""
    for old_id, new_id in id_map.items():
        text = text.replace(old_id, new_id)
    return text


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", required=True, help="Google Sheet URL or ID")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Scan only — print what would be cloned but don't do anything",
    )
    args = parser.parse_args()

    creds = _get_credentials()
    drive = build_drive(creds)

    spreadsheet = open_sheet(args.sheet)
    sheet_id    = spreadsheet.id
    print(f"Opened sheet: '{spreadsheet.title}'  (id={sheet_id})\n")

    # ------------------------------------------------------------------
    # Step 1: scan all tabs
    # ------------------------------------------------------------------
    all_file_ids        = set()
    all_importrange_ids = set()
    tab_cells           = {}  # tab title → list of (r1, c1, val)

    for ws in spreadsheet.worksheets():
        print(f"Scanning tab '{ws.title}' …")
        time.sleep(1)  # stay under Sheets API read quota (60 req/min)
        fids, irids, cells = scan_worksheet(ws)
        all_file_ids.update(fids)
        all_importrange_ids.update(irids)
        tab_cells[ws.title] = cells
        if fids or irids:
            print(f"  {len(fids)} file link(s), {len(irids)} IMPORTRANGE ref(s)")

    # The sheet itself is not a "referenced doc" that needs cloning
    all_file_ids.discard(sheet_id)
    all_importrange_ids.discard(sheet_id)

    # IMPORTRANGE sheet IDs also need to be in the clone set so we rewrite them
    all_to_clone = all_file_ids | all_importrange_ids

    print(f"\n{'='*60}")
    print(f"Total unique files to clone: {len(all_to_clone)}")
    if all_importrange_ids:
        print(f"  of which IMPORTRANGE spreadsheets: {len(all_importrange_ids)}")
        for sid in sorted(all_importrange_ids):
            print(f"    https://docs.google.com/spreadsheets/d/{sid}")

    if args.dry_run:
        print("\n[dry-run] Stopping here — no files cloned, no sheet updated.")
        return

    if not all_to_clone:
        print("Nothing to clone.")
        return

    # ------------------------------------------------------------------
    # Step 2: clone each file
    # ------------------------------------------------------------------
    print(f"\n{'='*60}")
    print("Cloning files …\n")
    id_map   = {}   # old_id → new_id
    failures = []

    for file_id in sorted(all_to_clone):
        new_id, method = clone_one(drive, creds, file_id)
        if new_id:
            id_map[file_id] = new_id
        else:
            failures.append((file_id, method))

    print(f"\nCloned {len(id_map)}/{len(all_to_clone)} file(s).")
    if failures:
        print(f"Failed ({len(failures)}):")
        for fid, reason in failures:
            print(f"  {fid}  — {reason}")

    if not id_map:
        print("No files cloned — nothing to update in the sheet.")
        return

    # ------------------------------------------------------------------
    # Step 3: rewrite links in the sheet
    # ------------------------------------------------------------------
    print(f"\n{'='*60}")
    print("Rewriting links in the sheet …\n")

    for ws in spreadsheet.worksheets():
        cells   = tab_cells.get(ws.title, [])
        updates = []

        for r1, c1, val in cells:
            new_val = replace_ids(val, id_map)
            if new_val != val:
                a1 = gspread.utils.rowcol_to_a1(r1, c1)
                updates.append({"range": a1, "values": [[new_val]]})

        if updates:
            ws.batch_update(updates, value_input_option="USER_ENTERED")
            print(f"  '{ws.title}': updated {len(updates)} cell(s)")
        else:
            print(f"  '{ws.title}': nothing to update")

    print("\nDone. Your sheet is now self-contained.")
    if failures:
        print(
            f"\nNote: {len(failures)} file(s) could not be cloned (see above). "
            "Their original links remain in the sheet."
        )


if __name__ == "__main__":
    main()
