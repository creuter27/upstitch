"""
Fresk B2B cart automation.

Uses Playwright (bundled Chromium — no system browser required).

Commands
--------
  python execution/fresk_cart.py setup
      Opens a visible browser to the Fresk B2B login page.
      Log in manually, then press Enter in the terminal.
      Your session is saved to fresk_session.json for all subsequent runs.

  python execution/fresk_cart.py explore
      Loads the saved session and crawls the product catalog.
      Dumps all product names/URLs it finds to fresk_products.json and
      a raw HTML snapshot to fresk_explore.html for selector development.
      Use this to understand the site structure before running 'order'.

  python execution/fresk_cart.py order [--dry-run]
      Reads the reorder list from stdin or --reorder-file (CSV/JSON),
      maps products using the built-in naming table, and fills the cart.
      --dry-run prints what would be added without touching the site.

Session file:  .tmp/fresk_session.json   (auto-created by 'setup')
Products dump: .tmp/fresk_products.json  (auto-created by 'explore')
"""

import argparse
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SITE_URL     = "https://www.b2bkukcompany.com/en/"
LOGIN_URL    = "https://www.b2bkukcompany.com/en/account/login/"
SESSION_FILE = Path(__file__).parent.parent / ".tmp" / "fresk_session.json"
PRODUCTS_FILE = Path(__file__).parent.parent / ".tmp" / "fresk_products.json"
EXPLORE_HTML  = Path(__file__).parent.parent / ".tmp" / "fresk_explore.html"

# ---------------------------------------------------------------------------
# Naming map: (our_Produktkategorie, our_size_hint) → Fresk site search term
#
# The site's English naming is used.  'size_hint' comes from the SKU suffix
# (small → "s", big/large → "l"/"xl", etc.) — leave None to match any size.
#
# Extend this table as you discover more products via 'explore'.
# ---------------------------------------------------------------------------
CATEGORY_MAP: list[dict] = [
    # our_category     our_size   site_search_term
    {"category": "Rucksack",  "size": "small",  "site_term": "backpack small"},
    {"category": "Rucksack",  "size": "big",    "site_term": "backpack"},
    {"category": "Rucksack",  "size": "large",  "site_term": "backpack"},
    {"category": "Rucksack",  "size": None,     "site_term": "backpack"},
]


def _resolve_site_term(produktkategorie: str, sku: str) -> str | None:
    """
    Map our Produktkategorie + SKU to a search term for the Fresk site.
    Returns None if no mapping is found.
    """
    sku_lower = sku.lower()
    size_hint = None
    if "-s-" in sku_lower or sku_lower.endswith("-s"):
        size_hint = "small"
    elif any(x in sku_lower for x in ["-l-", "-xl-", "-xxl-"]) or sku_lower.endswith(("-l", "-xl")):
        size_hint = "big"

    cat_lower = produktkategorie.lower().strip()
    for entry in CATEGORY_MAP:
        if entry["category"].lower() == cat_lower:
            if entry["size"] is None or entry["size"] == size_hint:
                return entry["site_term"]
    return None


# ---------------------------------------------------------------------------
# Setup command
# ---------------------------------------------------------------------------

def cmd_setup():
    """Open a visible browser, let the user log in, save session."""
    from playwright.sync_api import sync_playwright

    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)

    print(f"Opening Fresk B2B login page in Chromium ...")
    print(f"Please log in manually in the browser window that opens.")
    print(f"When you are fully logged in, come back here and press Enter.")
    print()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=50)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(LOGIN_URL)

        input(">> Logged in? Press Enter to save session and close browser ... ")

        ctx.storage_state(path=str(SESSION_FILE))
        browser.close()

    print(f"Session saved to {SESSION_FILE}")
    print(f"You can now run:  python execution/fresk_cart.py explore")


# ---------------------------------------------------------------------------
# Explore command
# ---------------------------------------------------------------------------

def cmd_explore():
    """Load saved session, crawl product pages, dump structure."""
    from playwright.sync_api import sync_playwright

    if not SESSION_FILE.exists():
        print(f"[error] No session file found at {SESSION_FILE}")
        print(f"        Run 'setup' first.")
        sys.exit(1)

    PRODUCTS_FILE.parent.mkdir(parents=True, exist_ok=True)

    print(f"Loading session from {SESSION_FILE} ...")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=30)
        ctx = browser.new_context(storage_state=str(SESSION_FILE))
        page = ctx.new_page()

        print(f"Navigating to {SITE_URL} ...")
        page.goto(SITE_URL)
        page.wait_for_load_state("networkidle")

        # --- Try to find all category/subcategory links ---
        print("Collecting category links ...")
        links = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => ({text: e.innerText.trim(), href: e.href}))"
        )
        category_links = [
            lnk for lnk in links
            if "b2bkukcompany.com/en/" in lnk["href"]
            and lnk["text"]
            and not any(skip in lnk["href"] for skip in ["/account/", "/cart", "/checkout", "#"])
        ]
        seen = set()
        unique_links = []
        for lnk in category_links:
            if lnk["href"] not in seen:
                seen.add(lnk["href"])
                unique_links.append(lnk)

        print(f"  {len(unique_links)} unique links found.")

        # --- Visit each likely product-list page and collect products ---
        all_products = []
        visited = set()

        for lnk in unique_links:
            url = lnk["href"]
            if url in visited:
                continue
            visited.add(url)

            print(f"  → {lnk['text'][:50]!r}  {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception as e:
                print(f"    [skip] {e}")
                continue

            # Try common product card selectors
            products_on_page = page.eval_on_selector_all(
                "[class*='product'], article, [data-product-id], [class*='item']",
                """els => els.map(e => ({
                    text: e.innerText.slice(0, 200).trim(),
                    id: e.getAttribute('data-product-id') || e.getAttribute('id') || '',
                    href: (e.querySelector('a') || {}).href || '',
                    class: e.className,
                }))"""
            )
            if products_on_page:
                print(f"    {len(products_on_page)} product element(s) found.")
                for p in products_on_page:
                    p["source_url"] = url
                    all_products.append(p)

        # Save HTML of last page for selector inspection
        html = page.content()
        EXPLORE_HTML.write_text(html, encoding="utf-8")
        print(f"\nHTML snapshot saved to {EXPLORE_HTML}")

        # Save all products
        with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
            json.dump({"links": unique_links, "products": all_products}, f,
                      ensure_ascii=False, indent=2)
        print(f"Products dump saved to {PRODUCTS_FILE}")
        print(f"  {len(all_products)} product elements collected across all pages.")

        input("\nBrowser still open for manual inspection. Press Enter to close ... ")
        browser.close()


# ---------------------------------------------------------------------------
# Order command  (skeleton — filled in after explore reveals page structure)
# ---------------------------------------------------------------------------

def cmd_order(reorder_file: str | None, dry_run: bool):
    """Fill the Fresk cart based on a reorder list."""
    from playwright.sync_api import sync_playwright

    if not SESSION_FILE.exists():
        print(f"[error] No session file found. Run 'setup' first.")
        sys.exit(1)

    # Load reorder list
    if reorder_file:
        with open(reorder_file, encoding="utf-8") as f:
            reorder = json.load(f)
    else:
        print("Reading reorder list from stdin (JSON) ...")
        reorder = json.load(sys.stdin)

    print(f"Loaded {len(reorder)} reorder item(s).")

    # Resolve site search terms
    to_order = []
    unresolved = []
    for item in reorder:
        term = _resolve_site_term(item.get("Produktkategorie", ""), item.get("SKU", ""))
        if term:
            to_order.append({**item, "site_term": term})
        else:
            unresolved.append(item)

    if unresolved:
        print(f"\n[warn] {len(unresolved)} item(s) have no site mapping and will be skipped:")
        for item in unresolved:
            print(f"  SKU={item.get('SKU')}  Kategorie={item.get('Produktkategorie')}")

    if not to_order:
        print("Nothing to order after mapping. Exiting.")
        return

    if dry_run:
        print("\n[DRY-RUN] Would order:")
        for item in to_order:
            print(f"  {item['site_term']!r:30s}  qty={item['Reorder qty']}  "
                  f"SKU={item.get('SKU')}  Name={item.get('Name','')[:40]}")
        return

    print("\n[NOTE] The 'order' command is not yet implemented.")
    print("       Run 'explore' first so we can inspect the page structure,")
    print("       then the add-to-cart selectors can be added here.")
    print()
    print("Items that would be ordered once implemented:")
    for item in to_order:
        print(f"  {item['site_term']!r:30s}  qty={item['Reorder qty']}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Fresk B2B cart automation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup",   help="Log in and save session (run once).")
    sub.add_parser("explore", help="Crawl the product catalog and dump structure.")

    order_p = sub.add_parser("order", help="Fill the cart from a reorder list.")
    order_p.add_argument("--reorder-file", help="Path to reorder JSON file (default: stdin).")
    order_p.add_argument("--dry-run", action="store_true",
                         help="Show what would be ordered without touching the site.")

    args = parser.parse_args()

    if args.command == "setup":
        cmd_setup()
    elif args.command == "explore":
        cmd_explore()
    elif args.command == "order":
        cmd_order(
            reorder_file=args.reorder_file,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    main()
