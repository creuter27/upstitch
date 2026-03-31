#!/usr/bin/env python3
"""
download_skool_videos.py

Scans the sheet configured in config.yaml for skool.com classroom links,
downloads each video, uploads it to Google Drive (destination folder from config),
and rewrites the sheet links.

Required packages (already in the productionPrep venv):
    browser-cookie3  playwright  yt-dlp  google-api-python-client  gspread  pyyaml

IMPORTANT: Run from Terminal.app (not VS Code) so macOS allows Chrome cookie access.

Usage:
    python download_skool_videos.py [--dry-run]
"""

import argparse
import copy
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from google_sheets_client import _get_credentials, open_sheet
import gspread.utils

# Reuse helpers from main clone script
from clone_gdocs_from_sheet import (
    load_config,
    build_services,
    find_or_create_folder,
    scan_all_tabs,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CONFIG_FILE    = Path(__file__).parent / "config.yaml"
SKOOL_MAP_FILE  = Path(__file__).parent / ".skool_map.json"
TOOLS_DIR       = Path.home() / ".local" / "share" / "upstitch-tools"
DOWNLOAD_DIR    = TOOLS_DIR / "skool-videos"
SKOOL_STATE     = TOOLS_DIR / "skool_playwright_state.json"

# ---------------------------------------------------------------------------
# URL pattern
# ---------------------------------------------------------------------------

SKOOL_URL_RE = re.compile(r"https://(?:www\.)?skool\.com/[^\s\"'<>\]]+")


def _normalize_url(url: str) -> str:
    """Strip trailing punctuation that may have been captured."""
    return url.rstrip(".,;)")


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def _login_and_save_state():
    """
    Open a VISIBLE browser window, navigate to skool.com, and wait for the
    user to confirm they are logged in.  Then save the full storage state
    (cookies + localStorage) for headless reuse.

    Must be run from Terminal.app to avoid macOS accessibility blocks.
    """
    from playwright.sync_api import sync_playwright

    print("\n[login] Opening a browser window for you to confirm your skool.com session …")
    print("[login] The page should open directly to the classroom.")
    print("[login] If you see the /about page, log in manually.")
    print("[login] Once you can see the classroom content, press ENTER here.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=200)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.goto("https://www.skool.com/gno-partners-9427/classroom", timeout=30_000)

        input("\n>>> Press ENTER once you're logged in and can see classroom content: ")

        TOOLS_DIR.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(SKOOL_STATE))
        browser.close()

    print(f"[login] Session saved to {SKOOL_STATE}")
    return str(SKOOL_STATE)


def _ensure_skool_state() -> str:
    """
    Return path to a valid skool.com Playwright state file.
    If none exists, runs the interactive login flow.
    """
    if SKOOL_STATE.exists():
        return str(SKOOL_STATE)
    return _login_and_save_state()


def _invalidate_skool_state():
    if SKOOL_STATE.exists():
        SKOOL_STATE.unlink()
    print("[auth] Stale skool.com session — re-running login …")
    return _login_and_save_state()


# ---------------------------------------------------------------------------
# Video URL interception via Playwright
# ---------------------------------------------------------------------------

# CDN URL fragments that indicate an actual video stream
_VIDEO_PATTERNS = [
    ".m3u8", "/manifest.m3u8", "videodelivery.net", "cloudflarestream.com",
    "wistia.net", "wistia.com", "fast.wistia", ".mp4", "cdn.skool.com",
    "media.skool.com",
]

# These are irrelevant hits we want to ignore (thumbnail images, tracking, etc.)
_VIDEO_EXCLUDES = ["thumbnail", "poster", "preview", "analytics", "track"]


def _intercept_video_url(skool_url: str, state_path: str) -> str | None:
    """
    Open the skool.com classroom page in headless Chromium, wait for the
    video player to fire network requests, and return the best CDN URL found.

    Raises RuntimeError("auth_failed") if the page redirects to /about.
    """
    from playwright.sync_api import sync_playwright

    captured: list[str] = []

    def on_request(req):
        url = req.url
        if any(pat in url for pat in _VIDEO_PATTERNS):
            if not any(ex in url.lower() for ex in _VIDEO_EXCLUDES):
                captured.append(url)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            storage_state=state_path,
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()
        page.on("request", on_request)

        try:
            page.goto(skool_url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as e:
            browser.close()
            print(f"    [nav] load error: {e}")
            return None

        if "/about" in page.url:
            browser.close()
            raise RuntimeError("auth_failed")

        print(f"    [nav] loaded: {page.url[:80]}")

        # Scroll to trigger lazy-loaded video elements
        for _ in range(4):
            page.evaluate("window.scrollBy(0, 400)")
            time.sleep(1.5)

        # Extra wait for player to initialize and make stream requests
        time.sleep(5)
        browser.close()

    if not captured:
        return None

    # Prefer adaptive streaming (m3u8) → mp4 → anything else
    for pat in [".m3u8", ".mp4", "videodelivery", "wistia"]:
        for url in captured:
            if pat in url:
                return url
    return captured[0]


# ---------------------------------------------------------------------------
# Sheet scanning — find all skool.com URLs
# ---------------------------------------------------------------------------

def _urls_from_text(text: str) -> list[str]:
    if not text:
        return []
    return [_normalize_url(u) for u in SKOOL_URL_RE.findall(text)]


def scan_skool_links(tabs) -> dict[str, list[dict]]:
    """
    Walk all cells in all tabs and return a dict:
        {skool_url: [{"tab": title, "r0": r, "c0": c, "location": "val|hyperlink|run|chip"}, ...]}
    """
    url_cells: dict[str, list[dict]] = {}

    def _record(url, tab_title, r, c, location):
        url_cells.setdefault(url, []).append(
            {"tab": tab_title, "r0": r, "c0": c, "location": location}
        )

    for tab in tabs:
        title = tab["title"]
        for cl in tab["cells"]:
            r, c = cl["r0"], cl["c0"]

            for url in _urls_from_text(cl.get("val", "")):
                _record(url, title, r, c, "val")

            for url in _urls_from_text(cl.get("hyperlink", "") or ""):
                _record(url, title, r, c, "hyperlink")

            for run in cl.get("text_format_runs", []):
                uri = run.get("format", {}).get("link", {}).get("uri", "")
                for url in _urls_from_text(uri):
                    _record(url, title, r, c, "run")

            for chip in cl.get("chip_runs", []):
                uri = chip.get("chip", {}).get("richLinkProperties", {}).get("uri", "")
                for url in _urls_from_text(uri):
                    _record(url, title, r, c, "chip")

    return url_cells


# ---------------------------------------------------------------------------
# Video download via yt-dlp (on the intercepted CDN URL)
# ---------------------------------------------------------------------------

def _yt_dlp_download(cdn_url: str, out_dir: Path, title: str = "video") -> Path | None:
    """Download a direct CDN video URL using yt-dlp. No auth needed here."""
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_title = re.sub(r'[^\w\s-]', '', title)[:60].strip()
    out_template = str(out_dir / f"{safe_title}.%(ext)s")

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "--output", out_template,
        "--merge-output-format", "mp4",
        "--print", "after_move:filepath",
        cdn_url,
    ]

    print(f"    [yt-dlp] downloading from CDN …")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        print("    [yt-dlp] timed out after 10 min")
        return None

    if result.returncode != 0:
        # Try a simpler direct download as fallback
        print(f"    [yt-dlp] failed, trying direct requests download …")
        return _requests_download(cdn_url, out_dir, safe_title)

    output_lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
    if not output_lines:
        return None

    file_path = Path(output_lines[-1].strip())
    if not file_path.exists():
        return None

    print(f"    [yt-dlp] → {file_path.name} ({file_path.stat().st_size // 1_048_576} MB)")
    return file_path


def _requests_download(url: str, out_dir: Path, title: str) -> Path | None:
    """Fallback: stream-download an mp4 directly via requests."""
    import requests as req_lib
    ext = ".mp4"
    out_path = out_dir / f"{title}{ext}"
    try:
        resp = req_lib.get(url, stream=True, timeout=60)
        resp.raise_for_status()
        with out_path.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
        print(f"    [requests] → {out_path.name} ({out_path.stat().st_size // 1_048_576} MB)")
        return out_path
    except Exception as e:
        print(f"    [requests] failed: {e}")
        return None


def download_video(skool_url: str, state_path: str, out_dir: Path) -> tuple[Path | None, bool]:
    """
    Full pipeline for one skool.com video URL:
      1. Intercept the CDN video URL via Playwright
      2. Download via yt-dlp (or requests fallback)

    Returns (file_path, auth_expired).
    auth_expired=True means the caller should refresh the session and retry.
    """
    print(f"    [playwright] intercepting video URL …")
    try:
        cdn_url = _intercept_video_url(skool_url, state_path)
    except RuntimeError as e:
        if "auth_failed" in str(e):
            return None, True   # signal: session is stale
        print(f"    [error] {e}")
        return None, False

    if not cdn_url:
        print("    [intercept] no video URL found on this page")
        return None, False

    print(f"    [intercept] {cdn_url[:100]}")

    # Derive a title from the skool URL's md parameter
    md_match = re.search(r'md=([a-f0-9]+)', skool_url)
    title = md_match.group(1)[:16] if md_match else "skool_video"

    return _yt_dlp_download(cdn_url, out_dir, title), False


# ---------------------------------------------------------------------------
# Drive upload
# ---------------------------------------------------------------------------

def upload_to_drive(drive, file_path: Path, folder_id: str) -> str | None:
    """Upload a local file to Drive and return its file ID."""
    name = file_path.name
    # Determine MIME type
    ext = file_path.suffix.lower()
    mime = {
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
        ".mov": "video/quicktime",
    }.get(ext, "video/mp4")

    print(f"    [drive] uploading '{name}' …")
    media = MediaFileUpload(str(file_path), mimetype=mime, resumable=True)
    request = drive.files().create(
        body={"name": name, "parents": [folder_id]},
        media_body=media,
        fields="id",
    )

    response = None
    while response is None:
        try:
            _, response = request.next_chunk()
        except HttpError as e:
            print(f"    [drive] upload error: {e}")
            return None

    file_id = response["id"]
    print(f"    [drive] → {file_id}")
    return file_id


def drive_view_url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"


# ---------------------------------------------------------------------------
# Sheet link rewriting (skool URLs → Drive URLs)
# ---------------------------------------------------------------------------

def _replace_skool_urls(text: str, url_map: dict[str, str]) -> str:
    for old_url, new_url in url_map.items():
        text = text.replace(old_url, new_url)
    return text


def rewrite_skool_links(spreadsheet, sheets_svc, tabs, url_map: dict[str, str]):
    """Replace skool.com URLs with Drive URLs across all four link locations."""
    if not url_map:
        return

    spreadsheet_id = spreadsheet.id

    for tab in tabs:
        title        = tab["title"]
        sheet_id_num = tab["sheet_id"]
        cells        = tab["cells"]

        # Check if this tab has any skool URLs at all
        tab_has_skool = any(
            any(old in (cl.get("val") or "") or
                any(old in (cl.get("hyperlink") or "") for old in url_map) or
                any(old in run.get("format", {}).get("link", {}).get("uri", "")
                    for run in cl.get("text_format_runs", []) for old in url_map) or
                any(old in chip.get("chip", {}).get("richLinkProperties", {}).get("uri", "")
                    for chip in cl.get("chip_runs", []) for old in url_map)
                for old in url_map)
            for cl in cells
        )
        if not tab_has_skool:
            continue

        try:
            ws = spreadsheet.worksheet(title)
        except Exception:
            print(f"  '{title}': could not open — skipping")
            continue

        value_updates = []
        api_requests  = []

        for cl in cells:
            r0, c0 = cl["r0"], cl["c0"]
            cell_range = {
                "sheetId":          sheet_id_num,
                "startRowIndex":    r0,
                "endRowIndex":      r0 + 1,
                "startColumnIndex": c0,
                "endColumnIndex":   c0 + 1,
            }

            # 1. formula / value
            new_val = _replace_skool_urls(cl.get("val") or "", url_map)
            if new_val != cl.get("val"):
                a1 = gspread.utils.rowcol_to_a1(r0 + 1, c0 + 1)
                value_updates.append({"range": a1, "values": [[new_val]]})

            # 2. cell-level hyperlink
            if cl.get("hyperlink"):
                new_hl = _replace_skool_urls(cl["hyperlink"], url_map)
                if new_hl != cl["hyperlink"] and new_val == cl.get("val"):
                    api_requests.append({
                        "updateCells": {
                            "range": cell_range,
                            "rows": [{"values": [{"userEnteredFormat": {
                                "textFormat": {"link": {"uri": new_hl}}
                            }}]}],
                            "fields": "userEnteredFormat.textFormat.link",
                        }
                    })

            # 3. textFormatRuns
            runs = cl.get("text_format_runs", [])
            if runs:
                new_runs = copy.deepcopy(runs)
                changed  = False
                for run in new_runs:
                    old_uri = run.get("format", {}).get("link", {}).get("uri", "")
                    if old_uri:
                        new_uri = _replace_skool_urls(old_uri, url_map)
                        if new_uri != old_uri:
                            run["format"]["link"]["uri"] = new_uri
                            changed = True
                if changed:
                    api_requests.append({
                        "updateCells": {
                            "range": cell_range,
                            "rows": [{"values": [{"textFormatRuns": new_runs}]}],
                            "fields": "textFormatRuns",
                        }
                    })

            # 4. chipRuns
            chips = cl.get("chip_runs", [])
            if chips and not cl.get("is_formula"):
                new_chips = copy.deepcopy(chips)
                changed   = False
                for chip in new_chips:
                    rlp = chip.get("chip", {}).get("richLinkProperties", {})
                    old_uri = rlp.get("uri", "")
                    if old_uri:
                        new_uri = _replace_skool_urls(old_uri, url_map)
                        if new_uri != old_uri:
                            chip["chip"]["richLinkProperties"]["uri"] = new_uri
                            changed = True
                if changed:
                    api_requests.append({
                        "updateCells": {
                            "range": cell_range,
                            "rows": [{"values": [{"chipRuns": new_chips}]}],
                            "fields": "chipRuns",
                        }
                    })

        changed = len(value_updates) + len(api_requests)
        if not changed:
            continue

        if value_updates:
            ws.batch_update(value_updates, value_input_option="USER_ENTERED")

        for req in api_requests:
            req_type = list(req.keys())[0]
            fields   = req.get(req_type, {}).get("fields", "")
            for attempt in range(3):
                try:
                    sheets_svc.spreadsheets().batchUpdate(
                        spreadsheetId=spreadsheet_id,
                        body={"requests": [req]},
                    ).execute()
                    break
                except HttpError as exc:
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
# Persistent map (skool URL → Drive file ID)
# ---------------------------------------------------------------------------

def _load_skool_map() -> dict[str, str]:
    if SKOOL_MAP_FILE.exists():
        return json.loads(SKOOL_MAP_FILE.read_text())
    return {}


def _save_skool_map(m: dict[str, str]):
    SKOOL_MAP_FILE.write_text(json.dumps(m, indent=2))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Scan and report only — do not download, upload, or rewrite")
    parser.add_argument("--test-one", action="store_true",
                        help="Test auth + video interception on the first URL only (no upload/rewrite)")
    parser.add_argument("--login", action="store_true",
                        help="(Re-)run the interactive browser login to refresh the skool.com session")
    args = parser.parse_args()

    if args.login:
        _login_and_save_state()
        return

    config     = load_config()
    sheet_url  = config.get("sheet_url") or sys.exit("sheet_url missing from config.yaml")
    folder_name = config.get("destination_folder", "GNOpartners")

    creds       = _get_credentials()
    drive, sheets_svc = build_services(creds)
    spreadsheet = open_sheet(sheet_url)

    # Extract spreadsheet ID from URL
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", sheet_url)
    if not m:
        sys.exit("Could not parse spreadsheet ID from sheet_url")
    spreadsheet_id = m.group(1)

    folder_id = find_or_create_folder(drive, folder_name)

    print("\n=== Scanning sheet for skool.com links ===")
    tabs, _, _ = scan_all_tabs(sheets_svc, spreadsheet_id)
    url_cells  = scan_skool_links(tabs)

    if not url_cells:
        print("No skool.com links found.")
        return

    print(f"\nFound {len(url_cells)} unique skool.com URL(s):")
    for url, refs in url_cells.items():
        tabs_mentioned = ", ".join(sorted({r['tab'] for r in refs}))
        print(f"  {url}")
        print(f"    → appears in: {tabs_mentioned} ({len(refs)} cell(s))")

    if args.dry_run:
        print("\n[dry-run] Stopping before download/upload.")
        return

    # Ensure we have a valid skool.com session (interactive login if needed)
    state_path = _ensure_skool_state()

    if args.test_one:
        test_url = next(iter(url_cells))
        print(f"\n=== Testing auth + interception on first URL ===")
        print(f"URL: {test_url}")
        file_path, auth_expired = download_video(test_url, state_path, DOWNLOAD_DIR)
        if auth_expired:
            state_path = _invalidate_skool_state()
            file_path, _ = download_video(test_url, state_path, DOWNLOAD_DIR)
        if file_path:
            print(f"\n[test-one] SUCCESS — video downloaded to {file_path}")
            print("[test-one] Not uploading. Run without --test-one to process all.")
        else:
            print("\n[test-one] FAILED — see messages above for details.")
        return

    # Load existing map to skip already-processed URLs
    skool_map = _load_skool_map()   # {skool_url: drive_file_id}

    # Process each URL
    newly_mapped: dict[str, str] = {}
    session_refreshed = False

    for url in url_cells:
        if url in skool_map:
            print(f"\n[skip] already downloaded: {url}")
            print(f"       Drive ID: {skool_map[url]}")
            newly_mapped[url] = drive_view_url(skool_map[url])
            continue

        print(f"\n[download] {url}")
        file_path, auth_expired = download_video(url, state_path, DOWNLOAD_DIR)

        if auth_expired and not session_refreshed:
            state_path = _invalidate_skool_state()
            session_refreshed = True
            file_path, _ = download_video(url, state_path, DOWNLOAD_DIR)

        if not file_path:
            print(f"    [skip] could not download")
            continue

        file_id = upload_to_drive(drive, file_path, folder_id)
        if not file_id:
            print(f"    [skip] upload failed")
            continue

        skool_map[url] = file_id
        newly_mapped[url] = drive_view_url(file_id)
        _save_skool_map(skool_map)

        # Delete local copy to save space
        try:
            file_path.unlink()
        except Exception:
            pass

    if not newly_mapped:
        print("\nNo videos were successfully downloaded.")
        return

    print(f"\n=== Rewriting {len(newly_mapped)} skool link(s) in sheet ===")
    rewrite_skool_links(spreadsheet, sheets_svc, tabs, newly_mapped)
    print("\nDone.")


if __name__ == "__main__":
    main()
