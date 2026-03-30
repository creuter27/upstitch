#!/usr/bin/env python3
"""
screenshot_fallback.py

Fallback for copy-protected Google Docs that can't be exported via the API.
Pipeline for each document:
  1. Open the doc in a headless Chromium browser (using a saved OAuth session)
  2. Render to PDF via Chromium's built-in print engine
  3. Convert each PDF page to an image (pymupdf)
  4. OCR each page (pytesseract)
  5. Create a new Google Doc containing the page images + extracted text
  6. Return the new doc ID

First run: a visible browser opens for you to sign in to Google.
           The session is saved to sessions/playwright_state.json for reuse.

Required packages (add to your venv):
    pip install playwright pymupdf pytesseract pillow
    playwright install chromium
macOS: brew install tesseract
"""

import io
import sys
import time
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

SESSIONS_DIR = Path(__file__).parent / "sessions"
STATE_FILE   = SESSIONS_DIR / "playwright_state.json"

GDOC_MIME    = "application/vnd.google-apps.document"

# A4 usable width in points (595pt page − 2 × 63.5pt margins)
PAGE_WIDTH_PT  = 468
PAGE_HEIGHT_PT = 661   # proportional to A4 aspect ratio


# ---------------------------------------------------------------------------
# Browser session management
# ---------------------------------------------------------------------------

def _ensure_session() -> str:
    """
    Return path to a valid Playwright storage_state file.
    If none exists, opens a visible browser for the user to sign in once.
    """
    SESSIONS_DIR.mkdir(exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "playwright not installed — run: pip install playwright && playwright install chromium"
        )

    if STATE_FILE.exists():
        return str(STATE_FILE)

    print("\n[auth] No saved browser session found.")
    print("       A browser window will open. Sign in to Google, then press Enter here.")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx     = browser.new_context()
        page    = ctx.new_page()
        page.goto("https://accounts.google.com/")
        input("       Press Enter once you are signed in … ")
        ctx.storage_state(path=str(STATE_FILE))
        browser.close()
    print(f"[auth] Session saved to {STATE_FILE.name}\n")
    return str(STATE_FILE)


def _invalidate_session():
    if STATE_FILE.exists():
        STATE_FILE.unlink()
        print("[auth] Stale session deleted — re-login required next run.")


# ---------------------------------------------------------------------------
# Step 1: Render doc as PDF via headless browser
# ---------------------------------------------------------------------------

def _render_as_pdf(doc_url: str, state_path: str) -> bytes:
    """
    Open the Google Doc in headless Chromium, wait for it to fully render,
    then use Chromium's built-in print engine to produce PDF bytes.

    This works even when the Drive API export endpoint returns 403, because
    print-to-PDF is a client-side browser operation that the server cannot block.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx     = browser.new_context(storage_state=state_path)
        page    = ctx.new_page()

        page.goto(doc_url, wait_until="networkidle", timeout=60_000)

        # Detect redirect to login (stale session)
        if "accounts.google.com" in page.url:
            browser.close()
            raise RuntimeError("session_expired")

        # Wait for the Google Docs editor to be present
        page.wait_for_selector(".kix-appview-editor", timeout=30_000)
        time.sleep(3)  # Let async rendering finish

        pdf_bytes = page.pdf(
            format="A4",
            print_background=True,
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
        )
        browser.close()

    return pdf_bytes


# ---------------------------------------------------------------------------
# Step 2: PDF → PIL images
# ---------------------------------------------------------------------------

def _pdf_to_images(pdf_bytes: bytes, dpi: int = 150):
    """Convert PDF bytes to a list of PIL Images."""
    try:
        import fitz
        from PIL import Image
    except ImportError:
        raise RuntimeError("pymupdf / pillow not installed — run: pip install pymupdf pillow")

    doc    = fitz.open(stream=pdf_bytes, filetype="pdf")
    mat    = fitz.Matrix(dpi / 72, dpi / 72)
    images = []
    for page in doc:
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        images.append(img)
    return images


def _is_blank(img, threshold: int = 250) -> bool:
    """True if the image is almost entirely white (likely a blank/failed render)."""
    import statistics
    pixels = list(img.getdata())
    avg = statistics.mean(v for rgb in pixels for v in rgb)
    return avg >= threshold


# ---------------------------------------------------------------------------
# Step 3: OCR
# ---------------------------------------------------------------------------

def _ocr_images(images) -> str:
    """Run pytesseract on each page image and return combined text."""
    try:
        import pytesseract
    except ImportError:
        raise RuntimeError("pytesseract not installed — run: pip install pytesseract")

    parts = []
    for i, img in enumerate(images, 1):
        text = pytesseract.image_to_string(img, lang="eng+deu")
        parts.append(f"--- Page {i} ---\n{text.strip()}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Step 4+5: Upload images to Drive, create Google Doc
# ---------------------------------------------------------------------------

def _upload_png(drive, img, name: str, folder_id: str) -> str:
    """Upload a PIL image as PNG to Drive and return its file ID."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    result = drive.files().create(
        body={"name": name, "mimeType": "image/png", "parents": [folder_id]},
        media_body=MediaIoBaseUpload(buf, mimetype="image/png", resumable=False),
        fields="id",
    ).execute()
    return result["id"]


def _make_public(drive, file_id: str):
    drive.permissions().create(
        fileId=file_id,
        body={"role": "reader", "type": "anyone"},
    ).execute()


def _make_private(drive, file_id: str):
    perms = drive.permissions().list(fileId=file_id, fields="permissions(id,type)").execute()
    for p in perms.get("permissions", []):
        if p.get("type") == "anyone":
            drive.permissions().delete(fileId=file_id, permissionId=p["id"]).execute()


def _create_doc_with_images_and_text(docs_svc, drive, name: str,
                                     images, ocr_text: str,
                                     folder_id: str) -> str:
    """
    Create a new Google Doc containing page screenshots and OCR text.

    Layout: [page 1 image] [page 2 image] … [=== OCR Text ===] [text]
    """
    # Create the empty doc
    doc    = docs_svc.documents().create(body={"title": name}).execute()
    doc_id = doc["documentId"]

    # Move it into the target folder
    drive.files().update(
        fileId=doc_id,
        addParents=folder_id,
        removeParents="root",
        fields="id,parents",
    ).execute()

    # Upload all page images to Drive (public so Docs API can fetch them)
    image_ids  = []
    image_urls = []
    for i, img in enumerate(images, 1):
        img_id  = _upload_png(drive, img, f"__tmp_{doc_id}_page{i}.png", folder_id)
        _make_public(drive, img_id)
        image_ids.append(img_id)
        image_urls.append(f"https://drive.google.com/uc?id={img_id}")

    # Build a single batchUpdate:
    #   1. Insert OCR text at index 1 (will be pushed down by image insertions)
    #   2. Insert images in REVERSE order at index 1
    #      → page 1 ends up at the top, OCR text at the bottom
    requests = []

    requests.append({
        "insertText": {
            "location": {"index": 1},
            "text": f"\n\n=== OCR Text ===\n\n{ocr_text}\n",
        }
    })

    for url in reversed(image_urls):
        requests.append({
            "insertInlineImage": {
                "location": {"index": 1},
                "uri": url,
                "objectSize": {
                    "width":  {"magnitude": PAGE_WIDTH_PT,  "unit": "PT"},
                    "height": {"magnitude": PAGE_HEIGHT_PT, "unit": "PT"},
                },
            }
        })

    docs_svc.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": requests},
    ).execute()

    # Clean up: revoke public access and remove the temp image files from Drive
    for img_id in image_ids:
        try:
            _make_private(drive, img_id)
            drive.files().delete(fileId=img_id).execute()
        except Exception:
            pass  # non-fatal

    return doc_id


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def screenshot_clone(drive, creds, file_id: str, file_info: dict,
                     folder_id: str) -> "str | None":
    """
    Full screenshot-based clone pipeline for one copy-protected Google Doc.

    Returns the new Google Doc ID on success, or None on failure.
    Only handles MIME type application/vnd.google-apps.document.
    """
    if file_info.get("mimeType") != GDOC_MIME:
        return None  # only implemented for Docs

    name    = file_info["name"]
    doc_url = f"https://docs.google.com/document/d/{file_id}/edit"

    print(f"    [screenshot] '{name}' …")
    try:
        docs_svc   = build("docs", "v1", credentials=creds)
        state_path = _ensure_session()

        try:
            pdf_bytes = _render_as_pdf(doc_url, state_path)
        except RuntimeError as e:
            if "session_expired" in str(e):
                _invalidate_session()
                state_path = _ensure_session()
                pdf_bytes  = _render_as_pdf(doc_url, state_path)
            else:
                raise

        images = _pdf_to_images(pdf_bytes)
        if not images or all(_is_blank(img) for img in images):
            print(f"    [screenshot] rendered pages appear blank — skipping")
            return None

        print(f"    [screenshot] {len(images)} page(s) — running OCR …")
        ocr_text = _ocr_images(images)

        new_id = _create_doc_with_images_and_text(
            docs_svc, drive, name, images, ocr_text, folder_id
        )
        print(f"    [screenshot] → {new_id}")
        return new_id

    except RuntimeError as e:
        print(f"    [screenshot] skipped: {e}")
        return None
    except Exception as e:
        print(f"    [screenshot] failed: {e}")
        return None
