"""
Trixie B2B cart automation.

Uses Playwright (bundled Chromium — no system browser required, works on Mac & Windows).

Commands
--------
  python execution/trixie_cart.py setup
      Opens a visible Chromium window at the Trixie B2B login page.
      Log in manually, then press Enter in the terminal.
      Session saved to .tmp/trixie_session.json for all subsequent runs.

  python execution/trixie_cart.py explore
      Loads the saved session, crawls the product catalog, and writes every
      product found (name, code, category, price, URL) to a new tab
      "TRX B2B Catalog" in the Google Sheet "Billbee Artikelmanager TRX".
      Use that sheet to build the SKU → site-product mapping.

  python execution/trixie_cart.py order [--dry-run]
      Reads the mapping from the sheet and the reorder list, then fills
      the Trixie B2B cart with the correct quantities.
      (Implemented once explore reveals the page structure.)

Session file:  .tmp/trixie_session.json   (created by 'setup')
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from google_sheets_client import open_sheet_by_name, write_tab

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SITE_URL     = "https://b2b.trixie-baby.com/"
LOGIN_URL    = "https://b2b.trixie-baby.com/"          # login form is on the root
SESSION_FILE = Path(__file__).parent.parent / ".tmp" / "trixie_session.json"
CACHE_FILE   = Path(__file__).parent.parent / ".tmp" / "trixie_catalog_cache.json"
SHEET_NAME   = "Billbee Artikelmanager TRX"
CATALOG_TAB  = "TRX B2B Catalog"


# ---------------------------------------------------------------------------
# Setup command
# ---------------------------------------------------------------------------

def cmd_setup():
    from playwright.sync_api import sync_playwright

    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)

    print("Opening Trixie B2B login page in Chromium ...")
    print("Please log in manually in the browser window that opens.")
    print("When you are fully logged in and can see the product catalog,")
    print("come back here and press Enter.")
    print()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=50)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(LOGIN_URL)
        page.wait_for_load_state("domcontentloaded")

        input(">> Logged in? Press Enter to save session ... ")

        ctx.storage_state(path=str(SESSION_FILE))
        browser.close()

    print(f"Session saved to {SESSION_FILE}")
    print(f"Next:  python execution/trixie_cart.py explore")


# ---------------------------------------------------------------------------
# Sheet helper
# ---------------------------------------------------------------------------

def _write_catalog_to_sheet(products: list[dict]):
    import webbrowser
    print(f"\nOpening sheet '{SHEET_NAME}' ...")
    ss = open_sheet_by_name(SHEET_NAME)
    write_tab(ss, CATALOG_TAB, products)
    print(f"Done!  {len(products)} products written to '{CATALOG_TAB}' tab.")
    print(f"Open the sheet, fill in 'Our SKU' and 'Qty' columns, then run 'order'.")
    webbrowser.open(ss.url)


# ---------------------------------------------------------------------------
# Explore command
# ---------------------------------------------------------------------------

def cmd_explore(refresh: bool = False):
    from playwright.sync_api import sync_playwright, Page

    # ── Serve from cache unless --refresh is requested ──────────────────────
    if not refresh and CACHE_FILE.exists():
        print(f"Loading catalog from cache ({CACHE_FILE}) ...")
        print(f"  (Use --refresh to re-crawl the site.)")
        with open(CACHE_FILE, encoding="utf-8") as f:
            deduped = json.load(f)
        print(f"  {len(deduped)} cached product(s).")
        _write_catalog_to_sheet(deduped)
        return

    if not SESSION_FILE.exists():
        print(f"[error] No session file at {SESSION_FILE} — run 'setup' first.")
        sys.exit(1)

    print(f"Loading session ...")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=20)
        ctx = browser.new_context(storage_state=str(SESSION_FILE))
        page = ctx.new_page()

        print(f"Navigating to {SITE_URL} ...")
        page.goto(SITE_URL, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=15000)

        current_url = page.url
        print(f"  Landed on: {current_url}")

        # If we were redirected back to the login page the session has expired.
        if "LoginAction" in current_url or current_url.rstrip("/") == SITE_URL.rstrip("/").replace("b2b.trixie-baby.com", "b2b.trixie-baby.com"):
            # Check if there's a login form
            if page.query_selector("input[type=password]"):
                print("[error] Session expired — run 'setup' again.")
                browser.close()
                sys.exit(1)

        # ── Collect all internal links ──────────────────────────────────────
        print("Collecting all internal links ...")
        raw_links = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => ({text: e.innerText.trim(), href: e.href}))"
        )

        # Filter: same domain, not account/cart/legal pages, has some text
        def is_product_link(lnk):
            h = lnk["href"]
            return (
                h.startswith("https://")          # exclude mailto:, tel:, javascript:
                and "trixie-baby.com" in h
                and lnk["text"]
                and not any(x in h.lower() for x in [
                    "loginaction", "logout", "account", "cart", "checkout",
                    "wishlist", "service", "imprint", "privacy", "terms", "contact",
                    "cookie", "#",
                ])
            )

        seen_urls: set[str] = set()
        unique_links = []
        for lnk in raw_links:
            if lnk["href"] not in seen_urls and is_product_link(lnk):
                seen_urls.add(lnk["href"])
                unique_links.append(lnk)

        print(f"  {len(unique_links)} unique candidate links.")

        # ── Visit each link and scrape products ────────────────────────────
        all_products: list[dict] = []
        visited: set[str] = set()

        def scrape_products(pg: Page, source_url: str, source_label: str):
            """Extract product cards from the current page."""
            # Try multiple selector strategies for product cards
            candidates = pg.eval_on_selector_all(
                # Common e-commerce product card patterns
                "[class*='product-item'], [class*='product_item'], "
                "[class*='ProductItem'], [class*='product-card'], "
                "[class*='ProductCard'], [class*='article-item'], "
                "article, [data-product], [data-item-id], "
                "[class*='catalog-item'], [class*='item-box']",
                """els => els.map(e => {
                    const link = e.querySelector('a');
                    const img  = e.querySelector('img');
                    const nameEl = e.querySelector(
                        '[class*="name"], [class*="title"], [class*="Name"], [class*="Title"], h2, h3, h4'
                    );
                    const priceEl = e.querySelector(
                        '[class*="price"], [class*="Price"]'
                    );
                    const codeEl = e.querySelector(
                        '[class*="sku"], [class*="code"], [class*="Code"], [class*="SKU"], [class*="ref"]'
                    );
                    return {
                        name:  (nameEl  || {}).innerText || e.innerText.slice(0, 80),
                        code:  (codeEl  || {}).innerText || '',
                        price: (priceEl || {}).innerText || '',
                        url:   link ? link.href : '',
                        img:   img  ? img.src   : '',
                    };
                })"""
            )
            found = []
            for c in candidates:
                name = (c.get("name") or "").strip().replace("\n", " ")
                if not name or len(name) < 2:
                    continue
                found.append({
                    "Category":  source_label,
                    "Name":      name,
                    "Code":      (c.get("code") or "").strip(),
                    "Price":     (c.get("price") or "").strip().replace("\n", " "),
                    "URL":       c.get("url") or source_url,
                    "Our SKU":   "",   # user fills this in the sheet
                    "Qty":       "",   # user fills this in the sheet
                })
            return found

        # Always scrape the landing page first
        found = scrape_products(page, SITE_URL, "Home")
        if found:
            print(f"  Home page: {len(found)} product(s)")
            all_products.extend(found)
        visited.add(page.url)

        for lnk in unique_links:
            url = lnk["href"]
            if url in visited:
                continue
            visited.add(url)

            label = lnk["text"][:50]
            print(f"  → {label!r}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass  # timeout ok — page may have long-running requests
            except Exception as e:
                print(f"    [skip] {e}")
                continue

            found = scrape_products(page, url, label)
            if found:
                print(f"    {len(found)} product(s)")
                all_products.extend(found)

            # If this page itself has more links (e.g. subcategories), queue them
            sub_links = page.eval_on_selector_all(
                "a[href]",
                "els => els.map(e => ({text: e.innerText.trim(), href: e.href}))"
            )
            for sl in sub_links:
                if sl["href"] not in seen_urls and is_product_link(sl):
                    seen_urls.add(sl["href"])
                    unique_links.append(sl)   # extend in-place for the loop

        # Deduplicate by URL
        seen_product_urls: set[str] = set()
        deduped: list[dict] = []
        for p in all_products:
            key = p["URL"]
            if key not in seen_product_urls:
                seen_product_urls.add(key)
                deduped.append(p)

        print(f"\n  Total: {len(deduped)} unique product(s) found.")

        input("Browser still open for inspection. Press Enter to close and write to sheet ... ")
        browser.close()

    if not deduped:
        print("[warn] No products found — nothing written to sheet or cache.")
        return

    # ── Save to cache ───────────────────────────────────────────────────────
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(deduped, f, ensure_ascii=False, indent=2)
    print(f"  Catalog cached to {CACHE_FILE}")

    _write_catalog_to_sheet(deduped)


# ---------------------------------------------------------------------------
# Order command  (skeleton — completed after explore + mapping)
# ---------------------------------------------------------------------------

def cmd_order(dry_run: bool):
    from playwright.sync_api import sync_playwright

    if not SESSION_FILE.exists():
        print(f"[error] No session file — run 'setup' first.")
        sys.exit(1)

    print(f"Loading product mapping from sheet '{SHEET_NAME}' / tab '{CATALOG_TAB}' ...")
    from google_sheets_client import read_tab
    ss   = open_sheet_by_name(SHEET_NAME)
    rows = read_tab(ss, CATALOG_TAB)

    # Only rows where both Our SKU and Qty are filled
    to_order = [
        r for r in rows
        if str(r.get("Our SKU") or "").strip() and str(r.get("Qty") or "").strip()
    ]
    print(f"  {len(to_order)} item(s) to order.")

    if not to_order:
        print("No items have both 'Our SKU' and 'Qty' filled in the sheet. Nothing to do.")
        return

    if dry_run:
        print("\n[DRY-RUN] Would order:")
        for r in to_order:
            print(f"  {r.get('Name','')[:50]:50s}  qty={r['Qty']}  url={r['URL'][:60]}")
        return

    print("\n[NOTE] The 'order' command is not yet fully implemented.")
    print("       Run 'explore' and fill in the mapping sheet first,")
    print("       then the add-to-cart selectors can be added here.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Trixie B2B cart automation via Playwright.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup",   help="Log in and save session (run once).")

    explore_p = sub.add_parser("explore", help="Write catalog to sheet (uses cache if available).")
    explore_p.add_argument("--refresh", action="store_true",
                           help="Re-crawl the site even if a cache exists.")

    order_p = sub.add_parser("order", help="Fill the cart from the mapping sheet.")
    order_p.add_argument("--dry-run", action="store_true",
                         help="Print what would be ordered without touching the site.")

    args = parser.parse_args()

    if args.command == "setup":
        cmd_setup()
    elif args.command == "explore":
        cmd_explore(refresh=args.refresh)
    elif args.command == "order":
        cmd_order(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
