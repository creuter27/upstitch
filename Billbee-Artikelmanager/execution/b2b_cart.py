"""
Generic B2B webshop cart automation via Playwright (bundled Chromium).

Works for any manufacturer's B2B website — no hardcoded URLs or names.
Run on Mac or Windows; no system browser required.

Commands
--------
  python execution/b2b_cart.py setup --manufacturer TRX --url https://b2b.trixie-baby.com/
      Opens Chromium at the login page. Log in manually, press Enter.
      Saves session + config for all subsequent commands.

  python execution/b2b_cart.py explore --manufacturer TRX
      Loads cached catalog → writes to Google Sheet (fast).
      Add --refresh to re-crawl the site.

  python execution/b2b_cart.py order --manufacturer TRX [--dry-run]
      Reads the mapping from the sheet and fills the cart.

Files (all in .tmp/, gitignored)
---------------------------------
  .tmp/{mfr}_config.json    URL + domain saved during setup
  .tmp/{mfr}_session.json   Browser session (cookies/storage)
  .tmp/{mfr}_catalog.json   Crawled product catalog (cache)

Google Sheet: "Billbee Artikelmanager {MFR}"
Catalog tab:  "{MFR} B2B Catalog"
"""

import argparse
import json
import os
import re
import signal as _signal
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

_LANG_RE = re.compile(r"^/([a-z]{2})(?:/|$)")

sys.path.insert(0, str(Path(__file__).parent.parent))

from google_sheets_client import open_sheet_by_name, open_sheet, create_sheet, write_tab


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wait(prompt: str) -> None:
    """Pause and wait for Enter. Typing 'q' + Enter quits immediately."""
    # Flush buffered keypresses so they don't accidentally skip this prompt.
    try:
        import termios
        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    except Exception:
        pass
    ans = input(f"{prompt}\n   (or type q + Enter to quit) ")
    if ans.strip().lower() == "q":
        print("Quitting.")
        sys.exit(0)


def _bring_browser_to_front(page) -> None:
    """Bring the Playwright browser window to the OS foreground.

    page.bring_to_front() activates the tab within the browser but does NOT
    raise the browser window itself at the OS level on Windows.  The ctypes
    block below handles that by enumerating top-level windows and calling
    SetForegroundWindow on the first visible Chromium window.
    """
    try:
        page.bring_to_front()
    except Exception:
        pass
    if sys.platform != "win32":
        return
    try:
        import ctypes
        import ctypes.wintypes
        user32 = ctypes.windll.user32
        found = [0]

        EnumCB = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
        )

        def _cb(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                n = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if "chromium" in buf.value.lower():
                    found[0] = hwnd
                    return False  # stop enumeration
            return True

        user32.EnumWindows(EnumCB(_cb), 0)
        hwnd = found[0]
        if hwnd:
            user32.ShowWindow(hwnd, 9)          # SW_RESTORE (un-minimise)
            # Pressing+releasing Alt lets a background thread steal the foreground
            user32.keybd_event(0x12, 0, 0, 0)   # VK_MENU down
            user32.keybd_event(0x12, 0, 2, 0)   # VK_MENU up
            user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def _col_letter(n: int) -> str:
    """Convert 1-based column index to letter(s): 1→A, 26→Z, 27→AA."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


# ---------------------------------------------------------------------------
# Per-manufacturer paths and settings (all derived from --manufacturer arg)
# ---------------------------------------------------------------------------

def _tmp(mfr: str) -> Path:
    """Return (and create) the per-tool cache directory outside the Tresorit tree."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path.home() / ".local" / "share"
    d = base / "upstitch-tools" / "Billbee-Artikelmanager"
    d.mkdir(parents=True, exist_ok=True)
    return d

def session_file(mfr: str) -> Path: return _tmp(mfr) / f"{mfr}_session.json"
def cache_file(mfr: str)       -> Path: return _tmp(mfr) / f"{mfr}_catalog.json"
def stock_cache_file(mfr: str) -> Path: return _tmp(mfr) / f"{mfr}_stock.json"
def skip_file(mfr: str)    -> Path: return _tmp(mfr) / f"{mfr}_skip.txt"
def sheet_name(mfr: str)        -> str: return f"Billbee Artikelmanager {mfr}"
def orders_sheet_name(mfr: str) -> str: return f"{mfr} Orders"
CATALOG_TAB      = "B2B Catalog"
MAPPING_TAB      = "mapping"
PRODUCT_LIST_TAB = "ProductList"


def _load_config(mfr: str, url_override: str | None = None) -> dict:
    """Build config from products.yaml (reorderingURL). url_override takes precedence."""
    import yaml
    products_yaml = Path(__file__).parent.parent / "mappings" / "products.yaml"
    with open(products_yaml, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    mfr_data = data.get("manufacturers", {}).get(mfr)
    if mfr_data is None:
        print(f"[error] Manufacturer '{mfr}' not found in products.yaml.")
        sys.exit(1)
    url = url_override or mfr_data.get("reorderingURL")
    if not url:
        print(f"[error] No reorderingURL for '{mfr}' in products.yaml.")
        sys.exit(1)
    domain = urlparse(url).netloc
    no_crawl = bool(mfr_data.get("useNoCrawl", False))
    return {"manufacturer": mfr, "url": url, "domain": domain, "no_crawl": no_crawl}


# ---------------------------------------------------------------------------
# Link filter  (no hardcoded domain — uses config)
# ---------------------------------------------------------------------------

def _detect_lang(url: str) -> str | None:
    """Return 2-letter language code from URL path (e.g. 'en' from '/en/products/'), or None."""
    m = _LANG_RE.match(urlparse(url).path)
    return m.group(1) if m else None


def _make_link_filter(domain: str, lang: str | None = None):
    """
    Return a predicate that accepts only navigable HTTP(S) links on `domain`.
    If `lang` is given (e.g. 'en'), links with a different 2-letter language
    prefix (e.g. '/de/', '/nl/') are rejected, avoiding duplicate crawls.
    """
    def is_product_link(lnk: dict) -> bool:
        h = lnk.get("href", "")
        if not (h.startswith("https://") or h.startswith("http://")):
            return False
        if domain not in h:
            return False
        txt = lnk.get("text", "")
        if not txt:
            return False
        # Links whose text spans multiple lines wrap an entire product card
        # (name + EAN + price).  These lead to individual product detail pages —
        # skip them; we already scrape products from category/listing pages.
        if "\n" in txt:
            return False
        # Language filter: skip URLs whose language prefix differs from ours
        if lang:
            link_lang = _detect_lang(urlparse(h).path)
            if link_lang and link_lang != lang:
                return False
        if any(x in h.lower() for x in [
            "loginaction", "logout", "sign-out", "signout",
            "account", "cart", "checkout",
            "wishlist", "service", "imprint", "privacy", "terms", "contact",
            "cookie", "#",
        ]):
            return False
        # Also reject by link text (catches "Sign out", "Log out", non-product pages)
        if any(x in txt.lower() for x in [
            "sign out", "log out", "logout", "sign-out",
            "express buy", "image bank", "image download",
        ]):
            return False
        return True
    return is_product_link


# ---------------------------------------------------------------------------
# Setup command
# ---------------------------------------------------------------------------

def cmd_setup(mfr: str, url: str | None):
    from playwright.sync_api import sync_playwright

    cfg = _load_config(mfr, url_override=url)
    url = cfg["url"]
    print(f"Manufacturer : {mfr}")
    print(f"URL          : {url}")
    print(f"Domain       : {cfg['domain']}")
    print()
    print("Opening login page in Chromium ...")
    print("Log in manually in the browser window, then press Enter here.")
    print()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=50)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")
        _bring_browser_to_front(page)

        _wait(">> Logged in? Press Enter to save session ...")

        ctx.storage_state(path=str(session_file(mfr)))
        browser.close()

    print(f"Session saved. Next: python execution/b2b_cart.py explore --manufacturer {mfr}")


# ---------------------------------------------------------------------------
# Sheet writer helper
# ---------------------------------------------------------------------------

def _write_to_sheet(mfr: str, products: list[dict]):
    import webbrowser
    sname = sheet_name(mfr)
    tname = CATALOG_TAB
    print(f"\nOpening sheet '{sname}' ...")
    ss = open_sheet_by_name(sname)
    write_tab(ss, tname, products)
    print(f"Done! {len(products)} products written to '{tname}' tab.")
    print("Fill in 'Our SKU' and 'Qty' columns, then run 'order'.")
    webbrowser.open(ss.url)


# ---------------------------------------------------------------------------
# Explore command
# ---------------------------------------------------------------------------

_CACHE_MAX_AGE = 2 * 24 * 3600  # 2 days in seconds


def _fmt_age(seconds: float) -> str:
    """Return a human-readable age string like '3h 12m' or '1d 5h'."""
    h = int(seconds // 3600)
    if h < 24:
        return f"{h}h {int((seconds % 3600) // 60)}m"
    return f"{h // 24}d {h % 24}h"


def cmd_explore(mfr: str, refresh: bool, dump_html: bool = False,
                start_url: str | None = None, no_crawl: bool = False):
    cfg = _load_config(mfr)
    domain   = cfg["domain"]
    site_url = start_url or cfg["url"]   # --url overrides config
    no_crawl = no_crawl or cfg["no_crawl"]  # CLI flag OR products.yaml useNoCrawl
    cf = cache_file(mfr)

    # ── Offer cached results if present and fresh (≤ 2 days) ────────────────
    if not refresh and cf.exists():
        age = time.time() - cf.stat().st_mtime
        if age < _CACHE_MAX_AGE:
            with open(cf, encoding="utf-8") as fh:
                cached = json.load(fh)
            ans = input(
                f"Cached product list found ({_fmt_age(age)} old, {len(cached)} products).\n"
                f"Use cached list? [y/n] "
            ).strip().lower()
            if ans != "n":
                print(f"  Using cached list ({len(cached)} products).")
                _write_to_sheet(mfr, cached)
                return
            print("  Re-scraping from the supplier's website ...")
        else:
            print(f"  Cache is {_fmt_age(age)} old (> 2 days) — re-scraping.")

    sf = session_file(mfr)
    if not sf.exists():
        print(f"[error] No session file for '{mfr}'. Run 'setup' first.")
        sys.exit(1)

    # ── Skip list  (.tmp/{MFR}_skip.txt — one URL per line, # = comment) ────
    sf_path = skip_file(mfr)
    skip_set: set[str] = set()
    if sf_path.exists():
        for line in sf_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                skip_set.add(line)
    if skip_set:
        print(f"  Skip list loaded: {len(skip_set)} URL(s) will be ignored.")

    from playwright.sync_api import sync_playwright, Page, Error as _PWError

    print(f"Crawling {site_url} ...")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=20)
        ctx = browser.new_context(storage_state=str(sf))
        page = ctx.new_page()

        # ── Ctrl+C: close browser from signal handler so Playwright unblocks ──
        _browser_alive = [True]
        _orig_sigint = _signal.getsignal(_signal.SIGINT)

        def _sigint(sig, frame):
            if _browser_alive[0]:
                print("\n[Ctrl+C] Closing browser ...")
                _browser_alive[0] = False
                try:
                    browser.close()
                except Exception:
                    pass

        _signal.signal(_signal.SIGINT, _sigint)

        def _safe_close():
            """Close browser if not already closed; restore signal handler."""
            _signal.signal(_signal.SIGINT, _orig_sigint)
            if _browser_alive[0]:
                _browser_alive[0] = False
                try:
                    browser.close()
                except Exception:
                    pass

        page.goto(site_url, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        _bring_browser_to_front(page)

        # Bail out if session expired (login form reappeared)
        if page.query_selector("input[type=password]"):
            print("[error] Session expired — run 'setup' again.")
            _safe_close()
            sys.exit(1)

        # Detect language from the landing URL and restrict crawl to that language
        lang = _detect_lang(page.url)
        if lang:
            print(f"  Detected language prefix: '{lang}' — skipping other locales.")
        is_product_link = _make_link_filter(domain, lang)

        # ── Collect initial link set ─────────────────────────────────────────
        def collect_links(pg: Page) -> list[dict]:
            return pg.eval_on_selector_all(
                "a[href]",
                "els => els.map(e => ({text: e.innerText.trim(), href: e.href}))"
            )

        raw = collect_links(page)
        seen_urls: set[str] = set()
        queue: list[dict] = []
        if not no_crawl:
            for lnk in raw:
                h = lnk["href"]
                if h not in seen_urls and h not in skip_set and is_product_link(lnk):
                    seen_urls.add(h)
                    queue.append(lnk)

        if no_crawl:
            print(f"  --no-crawl: scraping only {site_url}")
        else:
            print(f"  {len(queue)} candidate links from home page.")

        # ── Session guard ────────────────────────────────────────────────────
        def _session_ok(pg: Page) -> bool:
            """Return False if the login form has reappeared (session expired)."""
            try:
                return pg.query_selector("input[type=password]") is None
            except Exception:
                return True  # can't tell — assume ok

        # ── Product scraper ──────────────────────────────────────────────────
        # Wide selector list — many class-name patterns across different shop platforms.
        # article[data-product-id] covers Sitecore/DynamicWeb CMS sites (e.g. Trixie B2B).
        PRODUCT_SELECTORS = (
            "article[data-product-id], "
            "[class*='product-item'i], [class*='product_item'i], "
            "[class*='product-card'i], [class*='ProductCard'i], "
            "[class*='article-item'i], [class*='catalog-item'i], "
            "[class*='item-box'i], [class*='grid-item'i], "
            "[class*='shop-item'i], [class*='product-tile'i], "
            "li[class*='product'i], "
            "[data-product], [data-item-id]"
        )

        SCRAPE_JS = """els => els.map(e => {
            const link    = e.querySelector('a[href]');
            // Name: prefer heading, then data-product-name attribute on any child
            const nameEl  = e.querySelector('h2, h3, h4, [class*="name"i], [class*="title"i]');
            const dataName = e.querySelector('[data-product-name]');
            const rawName = (nameEl && nameEl.innerText.trim())
                         || (dataName && dataName.getAttribute('data-product-name'))
                         || e.innerText.slice(0, 100);
            // Price: class containing "price", or data-product-price attribute
            const priceEl  = e.querySelector('[class*="price"i], [itemprop="price"]');
            const dataPrice = e.querySelector('[data-product-price]');
            const rawPrice = (priceEl && (priceEl.innerText.trim() || priceEl.getAttribute('content')))
                          || (dataPrice && dataPrice.getAttribute('data-product-price'))
                          || '';
            // Code: data-product-id on the element itself, then class-based selectors
            const codeEl = e.querySelector('[class*="sku"i], [class*="code"i], [class*="ref"i], [class*="art"i]');
            const rawCode = e.getAttribute('data-product-id')
                         || (codeEl && codeEl.innerText.trim())
                         || '';
            return {
                name:  rawName,
                code:  rawCode,
                price: rawPrice,
                url:   link ? link.href : '',
            };
        })"""

        # Generic selectors for "load more" / "show more" buttons
        LOAD_MORE_SEL = (
            "[id^='LoadMoreButton_'], [id*='load-more'], [id*='loadmore'], "
            "[class*='load-more'i]:not(div):not(span), "
            "[class*='loadmore'i]:not(div):not(span)"
        )

        def _scroll_until_stable(pg: Page, max_rounds: int = 30):
            """
            Scroll to the bottom and click 'load more' buttons until no new products appear.
            Handles both IntersectionObserver infinite-scroll and explicit load-more buttons.
            max_rounds=30 is enough for ~2000 products at 66 per load.
            """
            COUNT_JS = f"document.querySelectorAll({PRODUCT_SELECTORS!r}).length"
            prev = -1
            for round_num in range(max_rounds):
                try:
                    # Scroll to bottom — may trigger IntersectionObserver
                    pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    pg.wait_for_timeout(800)  # give observer time to fire + AJAX to start

                    # Explicit fallback: click any visible load-more button
                    # (catches cases where IntersectionObserver doesn't fire in Playwright)
                    try:
                        btn = pg.query_selector(LOAD_MORE_SEL)
                        if btn and btn.is_visible():
                            btn.click()
                            try:
                                pg.wait_for_load_state("networkidle", timeout=6000)
                            except Exception:
                                pg.wait_for_timeout(1500)
                    except Exception:
                        pass

                    count = pg.evaluate(COUNT_JS)
                except Exception:
                    break

                if count == prev:
                    break  # stable — no new products loaded
                if round_num > 0:
                    print(f"      [{count} products loaded]")
                prev = count

        def _follow_pagination(pg: Page, visited: set, queue: list):
            """Find 'next page' links and add them to the queue."""
            try:
                next_links = pg.eval_on_selector_all(
                    "a[href][rel='next'], a[href][aria-label*='next'i], "
                    "a[href][class*='next'i], a[href][class*='pagination'i]",
                    "els => els.map(e => ({text: e.innerText.trim(), href: e.href}))"
                )
                for lnk in next_links:
                    h = lnk.get("href", "")
                    if h and h not in visited and is_product_link(lnk):
                        visited.add(h)
                        queue.append(lnk)
                        print(f"    [pagination] → {h}")
            except Exception:
                pass

        _html_dumped: list[bool] = [False]  # dump first product page HTML once

        def scrape_page(pg: Page, source_url: str, label: str) -> list[dict]:
            _scroll_until_stable(pg)

            try:
                candidates = pg.eval_on_selector_all(PRODUCT_SELECTORS, SCRAPE_JS)
            except Exception as e:
                print(f"    [scrape error] {e}")
                return []

            found = []
            seen_names: set[str] = set()
            for c in candidates:
                name = (c.get("name") or "").strip().replace("\n", " ")
                if not name or len(name) < 2 or name in seen_names:
                    continue
                seen_names.add(name)
                found.append({
                    "Category": label,
                    "Name":     name,
                    "Code":     (c.get("code")  or "").strip(),
                    "Price":    (c.get("price") or "").strip().replace("\n", " "),
                    "URL":      c.get("url") or source_url,
                    "Our SKU":  "",
                    "Qty":      "",
                })

            # Dump HTML of the first page that actually has products
            if dump_html and found and not _html_dumped[0]:
                dump_path = Path(__file__).parent.parent / ".tmp" / f"{mfr}_page_dump.html"
                dump_path.write_text(pg.content(), encoding="utf-8")
                print(f"    [html dump] saved to {dump_path}  ({source_url})")
                _html_dumped[0] = True

            return found

        # ── Crawl queue ──────────────────────────────────────────────────────
        all_products: list[dict] = []
        visited: set[str] = set()
        barren_urls: set[str] = set()   # pages with 0 products and 0 new links
        cancelled = False

        print("  (Close the browser window at any time to stop and save partial results)")

        try:
            # Scrape home / start page
            found = scrape_page(page, site_url, "Home")
            if found:
                print(f"  Home: {len(found)} product(s)")
                all_products.extend(found)
            visited.add(page.url)

            for lnk in queue:
                url = lnk["href"]
                if url in visited:
                    continue
                visited.add(url)

                label = lnk["text"][:50]
                print(f"  → {label!r}")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=4000)
                    except Exception:
                        pass
                except Exception as e:
                    print(f"    [skip] {e}")
                    continue

                # Abort immediately if the session expired mid-crawl
                if not _session_ok(page):
                    print("\n[error] Session expired mid-crawl (login screen reappeared).")
                    print("        Saving partial results, then run 'setup' again to refresh the session.")
                    raise KeyboardInterrupt  # jump to save-and-exit block

                found = scrape_page(page, url, label)
                if found:
                    print(f"    {len(found)} product(s)")
                    all_products.extend(found)

                # Follow pagination and discover sub-links (respect skip list)
                pre_q = len(queue)
                _follow_pagination(page, seen_urls, queue)
                for sl in collect_links(page):
                    h = sl["href"]
                    if h not in seen_urls and h not in skip_set and is_product_link(sl):
                        seen_urls.add(h)
                        queue.append(sl)

                if not found and len(queue) == pre_q:
                    barren_urls.add(url)

        except (KeyboardInterrupt, _PWError):
            cancelled = True
            print(f"\nStopping — saving partial results ...")

        # Deduplicate by URL
        seen_p: set[str] = set()
        products = []
        for p in all_products:
            if p["URL"] not in seen_p:
                seen_p.add(p["URL"])
                products.append(p)

        if cancelled:
            print(f"  {len(products)} unique product(s) collected so far.")
        else:
            print(f"\n  Total: {len(products)} unique product(s).")
            _wait("Browser still open for inspection. Press Enter to close and write to sheet ...")
        _safe_close()

    if not products:
        print("[warn] No products found — nothing written.")
        return

    # Always save cache (even on partial/cancelled runs)
    with open(cf, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)
    print(f"  Cached to {cf}")

    # Persist newly discovered barren URLs to skip list
    if barren_urls:
        new_barren = barren_urls - skip_set
        if new_barren:
            with open(sf_path, "a", encoding="utf-8") as f:
                for u in sorted(new_barren):
                    f.write(u + "\n")
            print(f"  {len(new_barren)} barren URL(s) added to skip list ({sf_path.name}).")

    if cancelled:
        print(f"  Run 'explore --manufacturer {mfr}' (without --refresh) to write the cached results to the sheet.")
        return

    _write_to_sheet(mfr, products)
    cmd_map(mfr)


# ---------------------------------------------------------------------------
# Map command — resolves B2B catalog → ProductList EAN column
# ---------------------------------------------------------------------------

def cmd_map(mfr: str, replace: bool = True):
    """
    Uses the 'mapping' tab of the B2B sheet to match scraped products to SKUs
    in the 'ProductList' tab and writes the matched EAN into the EAN column.

    Mapping tab columns: Kategorie | Größe | Herstellername | Avoid

    Matching logic per ProductList row (physical products without EAN):
      1. Find mapping rows where Kategorie == our Produktkategorie
         AND (Größe == our Produktgröße  OR  Größe is empty).
      2. Filter the B2B catalog: Name contains Herstellername  AND
         does NOT contain any of the Avoid strings.
      3. Of those candidates, keep only ones whose Name contains a token
         for our Produktvariante (from products.yaml).
      4. If exactly one match → write its EAN (Code column) to ProductList.
    """
    from google_sheets_client import read_tab
    from execution.mappings_loader import Mappings
    import gspread.utils as _gu

    sname = sheet_name(mfr)
    tname = CATALOG_TAB

    print(f"\nMapping B2B catalog → ProductList EAN ...")
    ss = open_sheet_by_name(sname)

    # Check whether the mapping tab exists
    tab_names = [ws.title for ws in ss.worksheets()]
    if MAPPING_TAB not in tab_names:
        print(f"  [skip] No '{MAPPING_TAB}' tab found in '{sname}'. Create it first.")
        return
    if PRODUCT_LIST_TAB not in tab_names:
        print(f"  [skip] No '{PRODUCT_LIST_TAB}' tab found in '{sname}'.")
        return

    mapping  = read_tab(ss, MAPPING_TAB)           # [{Kategorie, Größe, Herstellername, Avoid}]
    catalog  = read_tab(ss, tname)                  # [{Name, Code, URL, ...}]
    mappings = Mappings()

    # Build a flat token→variant map for cross-variant exclusion (prevents "bear" matching "polar bear")
    _all_var_tokens: dict[str, str] = {}   # lowercase token → canonical variant name
    for _vname, _vdata in mappings.variants.items():
        _all_var_tokens[_vname.lower()] = _vname
        if isinstance(_vdata, dict):
            for _t in (_vdata.get("tokens") or []):
                _all_var_tokens[str(_t).lower()] = _vname

    if not mapping:
        print(f"  [skip] '{MAPPING_TAB}' tab is empty.")
        return
    if not catalog:
        print(f"  [skip] '{tname}' tab is empty — run 'explore' first.")
        return

    # Build a lowercase catalog list for fast searching
    cat_lower = [
        {**p, "_name_l": p.get("Name", "").lower()}
        for p in catalog
    ]

    # Open the ProductList worksheet for targeted cell updates
    pl_ws   = ss.worksheet(PRODUCT_LIST_TAB)
    headers = pl_ws.row_values(1)

    COL_KAT  = "Custom Field Produktkategorie"
    COL_GR   = "Custom Field Produktgröße"
    COL_VAR  = "Custom Field Produktvariante"
    COL_EAN  = "EAN"

    missing = [c for c in [COL_KAT, COL_GR, COL_VAR, COL_EAN] if c not in headers]
    if missing:
        print(f"  [skip] ProductList is missing columns: {missing}")
        return

    ean_col_idx = headers.index(COL_EAN) + 1   # 1-based

    pl_rows = read_tab(ss, PRODUCT_LIST_TAB)

    # ANSI colours (gracefully degrade on Windows without ANSI support)
    _G  = "\033[32m"   # green   — matched
    _Y  = "\033[33m"   # yellow  — ambiguous
    _R  = "\033[31m"   # red     — no match
    _D  = "\033[2m"    # dim     — skipped / no-category
    _B  = "\033[1m"    # bold
    _X  = "\033[0m"    # reset

    updates   = []   # [(sheet_row_1based, ean_str)]
    matched   = 0
    skipped   = 0
    ambig     = 0
    no_match  = 0
    no_cat    = 0
    total     = 0    # physical rows considered

    for row_idx, row in enumerate(pl_rows):
        # Skip listing rows entirely — no output
        is_bom = str(row.get("IsBom") or "").strip().lower() in ("true", "1", "yes")
        has_bom_skus = bool(str(row.get("BOM_SKUs") or "").strip())
        if is_bom or has_bom_skus:
            continue

        total += 1
        sku = str(row.get("SKU") or "").strip() or f"row {row_idx+2}"

        # Skip (or overwrite) physical rows that already have an EAN
        ean_existing = str(row.get(COL_EAN) or "").strip()
        if ean_existing and not replace:
            skipped += 1
            print(f"  {_D}= {sku}: EAN already set ({ean_existing}) — skipping{_X}")
            continue

        kat      = str(row.get(COL_KAT)  or "").strip().lower()
        groesse  = str(row.get(COL_GR)   or "").strip().lower()
        variante = str(row.get(COL_VAR)  or "").strip().lower()

        if not kat:
            no_cat += 1
            print(f"  {_D}- {sku}: no category{_X}")
            continue

        # Variant tokens from products.yaml
        var_entry = mappings.variants.get(variante, {})
        var_tokens: list[str] = [variante] + [
            t.lower() for t in (var_entry.get("tokens", []) if isinstance(var_entry, dict) else [])
        ]

        found_matches: list[tuple[str, str]] = []   # (ean, name)

        for m in mapping:
            m_kat   = str(m.get("Kategorie")     or "").strip().lower()
            m_gr    = str(m.get("Größe")         or "").strip().lower()
            m_name  = str(m.get("Herstellername") or "").strip().lower()
            m_avoid = [a.strip().lower() for a in str(m.get("Avoid") or "").split(",") if a.strip()]

            if m_kat != kat:
                continue
            if m_gr and m_gr != groesse:
                continue
            if not m_name:
                continue

            # Filter catalog by Herstellername + Avoid
            candidates = [
                p for p in cat_lower
                if m_name in p["_name_l"]
                and not any(av in p["_name_l"] for av in m_avoid)
            ]

            if not candidates:
                continue

            # Narrow by variant token; also exclude products that match a *different*
            # variant's tokens — e.g. "bear" should not pull in "polar bear" (eisbaer).
            # Guard: ignore a foreign token if it is itself a substring of our own variant
            # name/tokens (e.g. "affe" is inside "giraffe" — coincidental, not a conflict).
            if variante:
                var_matches = []
                for p in candidates:
                    name_l = p["_name_l"]
                    if not any(tok in name_l for tok in var_tokens if tok):
                        continue
                    other_match = any(
                        tok in name_l
                        for tok, vname in _all_var_tokens.items()
                        if vname.lower() != variante
                        and tok
                        and not any(tok in vt for vt in var_tokens)  # ignore if tok ⊂ our own token
                    )
                    if not other_match:
                        var_matches.append(p)
                if not var_matches:
                    continue  # no variant match in this mapping row — skip it
                candidates = var_matches

            for p in candidates:
                ean  = str(p.get("Code") or "").strip()
                name = str(p.get("Name") or "").strip()
                if ean and ean not in [e for e, _ in found_matches]:
                    found_matches.append((ean, name))

        if len(found_matches) == 1:
            ean, name = found_matches[0]
            updates.append((row_idx + 2, ean))
            matched += 1
            prefix = "↺" if ean_existing else "✓"
            print(f"  {_G}{prefix} {sku}: EAN={ean}  [{name}]{_X}")
        elif len(found_matches) > 1:
            ambig += 1
            names_str = ", ".join(f"{e} [{n}]" for e, n in found_matches[:3])
            more = f" (+{len(found_matches)-3} more)" if len(found_matches) > 3 else ""
            print(f"  {_Y}? {sku}: {len(found_matches)} ambiguous — {names_str}{more}{_X}")
        else:
            no_match += 1
            print(f"  {_R}- {sku}: no match  [kat={kat or '—'}, gr={groesse or '—'}, var={variante or '—'}]{_X}")

    unmapped = ambig + no_match + no_cat
    print(f"\n  {_B}Total physical: {total} | "
          f"{_G}matched: {matched}{_X}{_B} | "
          f"{_Y}ambiguous: {ambig}{_X}{_B} | "
          f"{_R}no match: {no_match}{_X}{_B} | "
          f"{_D}no category: {no_cat}{_X}{_B} | "
          f"skipped (EAN exists): {skipped}{_X}")
    if unmapped:
        print(f"  {_R}{_B}{unmapped} row(s) need attention.{_X}")

    if not updates:
        return

    # Batch-write EAN cells
    ean_letter = _col_letter(ean_col_idx)
    data = [{"range": f"{ean_letter}{r}", "values": [[e]]} for r, e in updates]
    pl_ws.batch_update(data)
    print(f"  Wrote {len(updates)} EAN(s) to '{PRODUCT_LIST_TAB}' tab.")


# ---------------------------------------------------------------------------
# Order command
# ---------------------------------------------------------------------------

def _update_billbee_stock(mfr: str, rows: list[dict]) -> None:
    """
    For each row where 'add to Billbee stock' is checked, add the ordered Qty
    to the current Billbee stock.
    Rows must have a 'Billbee Id' column. Uses per-product fetch — fast.
    """
    from billbee_client import BillbeeClient

    to_update = [
        r for r in rows
        if str(r.get("add to Billbee stock") or "").strip().upper() in ("TRUE", "1", "YES")
        and str(r.get("Qty") or "").strip() not in ("", "0")
        and str(r.get("Billbee Id") or "").strip()
    ]

    if not to_update:
        print("  No rows checked for Billbee stock update.")
        return

    client  = BillbeeClient()
    updated = 0
    for row in to_update:
        sku        = str(row.get("SKU") or "").strip()
        product_id = str(row.get("Billbee Id") or "").strip()
        try:
            qty = int(float(str(row.get("Qty") or "0")))
        except (ValueError, TypeError):
            qty = 0
        if qty <= 0 or not sku:
            continue

        # Fetch current stock via product Id (GET works with the full Id)
        try:
            product = client.get_product_by_id(int(product_id)) if product_id else {}
            stocks  = product.get("Stocks") or []
            stock_id = stocks[0].get("Id") or 0 if stocks else 0
            current  = float(stocks[0].get("StockCurrent") or 0) if stocks else 0.0
        except Exception as e:
            print(f"  [warn] {sku}: could not fetch current stock ({e}), assuming 0")
            stock_id = 0
            current  = 0.0

        new_qty = current + qty

        # Stock update uses SKU — no product Id needed in the URL
        try:
            client.update_stock(sku, new_qty, stock_id=stock_id,
                                reason=f"B2B order {mfr}")
            print(f"  {sku}: {current:.0f} + {qty} → {new_qty:.0f}")
            updated += 1
        except Exception as e:
            print(f"  [error] {sku}: {e}")

    print(f"  {updated}/{len(to_update)} product(s) updated in Billbee.")


def cmd_add_stock(mfr: str, tab: str) -> None:
    """
    Standalone command: add ordered quantities from a completed order tab to
    Billbee stock for all rows where 'add to Billbee stock' is checked.
    If 'Billbee Id' column is missing or empty, looks up IDs from ProductList
    and writes them back to the sheet first.
    """
    from google_sheets_client import read_tab

    # ── Load the order tab ───────────────────────────────────────────────────
    oname = orders_sheet_name(mfr)
    print(f"Opening '{oname}' / '{tab}' ...")
    oss  = open_sheet_by_name(oname)
    ws   = oss.worksheet(tab)
    rows = read_tab(oss, tab)

    if not rows:
        print("Tab is empty.")
        return

    # ── Backfill 'Billbee Id' only if the column is absent or completely empty ─
    headers = list(rows[0].keys())
    has_any_id = any(str(r.get("Billbee Id") or "").strip() for r in rows)
    needs_id   = "Billbee Id" not in headers or not has_any_id

    if needs_id:
        print(f"'Billbee Id' column missing/incomplete — loading from '{sheet_name(mfr)}' ...")
        ss = open_sheet_by_name(sheet_name(mfr))
        pl_rows = read_tab(ss, PRODUCT_LIST_TAB)
        sku_to_billbee_id = {
            str(r.get("SKU") or "").strip(): str(r.get("Id") or "").strip()
            for r in pl_rows
            if str(r.get("SKU") or "").strip() and str(r.get("Id") or "").strip()
        }

        if "Billbee Id" not in headers:
            # Column doesn't exist yet — insert it after EAN (or after SKU if no EAN)
            insert_after = "EAN" if "EAN" in headers else "SKU"
            insert_pos   = headers.index(insert_after) + 1  # 0-based column index in data
            # Insert header into sheet at the right column position
            col_letter = _col_letter(insert_pos + 1)
            ws.insert_cols([["Billbee Id"]], col=insert_pos + 1)
            headers.insert(insert_pos, "Billbee Id")
            for r in rows:
                r["Billbee Id"] = ""
            print(f"  Inserted 'Billbee Id' column at column {col_letter}.")

        # Write Billbee Ids for rows that are missing them
        id_col_idx = headers.index("Billbee Id") + 1  # 1-based
        updates = []
        for i, r in enumerate(rows, start=2):  # row 1 = header
            sku = str(r.get("SKU") or "").strip()
            if not sku:
                continue
            if str(r.get("Billbee Id") or "").strip():
                continue  # already has an ID
            bid = sku_to_billbee_id.get(sku, "")
            if bid:
                r["Billbee Id"] = bid
                updates.append({
                    "range":  f"{_col_letter(id_col_idx)}{i}",
                    "values": [[bid]],
                })
        if updates:
            ws.batch_update(updates, value_input_option="RAW")
            print(f"  Wrote {len(updates)} Billbee Id(s) to sheet.")

    # ── Filter to checked rows ───────────────────────────────────────────────
    to_update = [
        r for r in rows
        if str(r.get("add to Billbee stock") or "").strip().upper() in ("TRUE", "1", "YES")
        and str(r.get("Qty") or "").strip() not in ("", "0")
        and str(r.get("Billbee Id") or "").strip()
    ]

    if not to_update:
        print("No rows checked for Billbee stock update (or Billbee Ids missing).")
        return

    # ── Fetch live Billbee stock for preview table ────────────────────────────
    from billbee_client import BillbeeClient as _BBC

    print(f"\nFetching live Billbee stock for {len(to_update)} item(s) ...")
    _client = _BBC()
    preview: list[dict] = []
    for r in to_update:
        _sku      = str(r.get("SKU") or "").strip()
        _bid      = str(r.get("Billbee Id") or "").strip()
        try:
            _qty = int(float(str(r.get("Qty") or "0")))
        except (ValueError, TypeError):
            _qty = 0
        try:
            _sc = int(round(float(str(r.get("Stock current") or "0") or "0")))
        except (ValueError, TypeError):
            _sc = None
        try:
            _st = int(round(float(str(r.get("Stock target") or "0") or "0")))
        except (ValueError, TypeError):
            _st = None
        try:
            _prod   = _client.get_product_by_id(int(_bid))
            _stocks = _prod.get("Stocks") or []
            _live   = float(_stocks[0].get("StockCurrent") or 0) if _stocks else 0.0
        except Exception as _e:
            print(f"  [warn] {_sku}: could not fetch stock ({_e}), showing 0")
            _live = 0.0
        preview.append({
            "sku": _sku, "billbeeStock": _live, "sheetCurrent": _sc,
            "sheetTarget": _st, "qty": _qty, "newStock": _live + _qty,
        })

    # ── Print confirmation table ──────────────────────────────────────────────
    _w = max(len("SKU"), max(len(p["sku"]) for p in preview))
    _hdr = (f"  {'Billbee akt.':>12}  {'Sheet akt.':>10}  {'Ziel':>6}"
            f"  {'Bestellt':>8}  {'Neu':>8}  {'SKU':<{_w}}")
    print()
    print(_hdr)
    print("  " + "-" * (len(_hdr) - 2))
    for p in preview:
        _sc_s = str(p["sheetCurrent"]) if p["sheetCurrent"] is not None else "?"
        _st_s = str(p["sheetTarget"])  if p["sheetTarget"]  is not None else "?"
        print(f"  {int(p['billbeeStock']):>12}  {_sc_s:>10}  {_st_s:>6}"
              f"  {p['qty']:>8}  {int(p['newStock']):>8}  {p['sku']:<{_w}}")
    print()

    ans = input(f">> Add {len(preview)} item(s) to Billbee stock? [Enter = yes, q = cancel] ")
    if ans.strip().lower() == "q":
        print("Cancelled.")
        return

    _update_billbee_stock(mfr, to_update)
    print("Done!")


def cmd_order(mfr: str, dry_run: bool, factor: float = 1.0, cached: bool = False):
    from datetime import date
    import webbrowser
    from google_sheets_client import read_tab, read_tab_visible
    from billbee_client import BillbeeClient

    sf  = session_file(mfr)
    if not sf.exists():
        print(f"[error] No session for '{mfr}'. Run 'setup' first.")
        sys.exit(1)

    # ── 1. Billbee stock data → current stock per SKU ────────────────────────
    # Only current stock comes from Billbee. Stock target is read from
    # ProductList "Stock target Standard" column so we control it in the sheet.
    scf = stock_cache_file(mfr)
    sku_to_current: dict[str, float] = {}

    if cached:
        if not scf.exists():
            print(f"[error] No stock cache found at {scf}. Run without --cached first.")
            sys.exit(1)
        with open(scf, encoding="utf-8") as f:
            sku_to_current = json.load(f)
        print(f"Loaded stock cache ({len(sku_to_current)} products) from {scf.name}")
    else:
        print("Fetching current stock from Billbee (this may take ~30 s) ...")
        client = BillbeeClient()
        for product in client.get_all_products():
            sku_bb = str(product.get("SKU") or "").strip()
            if not sku_bb:
                continue
            stocks = product.get("Stocks") or []
            if not stocks:
                continue
            stock_current = stocks[0].get("StockCurrent")
            try:
                sku_to_current[sku_bb] = float(stock_current) if stock_current is not None else 0.0
            except (TypeError, ValueError):
                sku_to_current[sku_bb] = 0.0
        with open(scf, "w", encoding="utf-8") as f:
            json.dump(sku_to_current, f)
        print(f"  {len(sku_to_current)} products with stock data in Billbee. (cached to {scf.name})")

    # ── 2. Load sheet data ────────────────────────────────────────────────────
    sname = sheet_name(mfr)
    print(f"\nReading catalog and product list from '{sname}' ...")
    ss = open_sheet_by_name(sname)

    # B2B Catalog → EAN → {Name, Price, URL}
    catalog_rows = read_tab(ss, CATALOG_TAB)
    ean_to_cat: dict[str, dict] = {}
    for cr in catalog_rows:
        code = str(cr.get("Code") or "").strip()
        if code:
            ean_to_cat[code] = cr
    print(f"  {CATALOG_TAB}: {len(ean_to_cat)} products in current B2B catalog.")

    # ProductList physical rows
    pl_rows = read_tab(ss, PRODUCT_LIST_TAB)
    order_rows: list[dict] = []
    for row in pl_rows:
        if str(row.get("IsBom") or "").strip().lower() in ("true", "1", "yes"):
            continue
        if str(row.get("BOM_SKUs") or "").strip():
            continue
        sku = str(row.get("SKU") or "").strip()
        ean = str(row.get("EAN") or "").strip()
        if not sku or not ean:
            continue

        # Stock target from sheet; current stock from Billbee
        try:
            target = float(str(row.get("Stock target Standard") or "0").replace(",", ".") or 0)
        except (TypeError, ValueError):
            target = 0.0
        current = sku_to_current.get(sku, 0.0)
        threshold = target * factor
        reorder_qty = max(0, round(threshold - current)) if current < threshold else 0

        cat = ean_to_cat.get(ean, {})
        try:
            price = float(str(cat.get("Price") or "").replace(",", "."))
        except (TypeError, ValueError):
            price = None

        order_rows.append({
            "SKU":                  sku,
            "Name":                 str(cat.get("Name") or sku),
            "EAN":                  ean,
            "Billbee Id":           str(row.get("Id") or ""),
            "Price":                price if price is not None else "",
            "Stock current":        int(round(current)),
            "Stock target":         int(round(target)),
            "Qty":                  reorder_qty if reorder_qty > 0 else "",
            "Cost":                 None,    # formula filled in below
            "add to Billbee stock": "TRUE",  # user unchecks items delayed by supplier
            "URL":                  str(cat.get("URL") or ""),
        })

    if not order_rows:
        print("No physical rows with EAN found. Run 'map' first.")
        return

    # Fill Cost as a formula referencing Price × Qty in the same row.
    # Row 2 = first data row (row 1 is the header).
    cols       = list(order_rows[0].keys())
    price_let  = _col_letter(cols.index("Price") + 1)
    qty_let    = _col_letter(cols.index("Qty") + 1)
    for i, r in enumerate(order_rows, start=2):
        r["Cost"] = f"={price_let}{i}*{qty_let}{i}"

    n_with_qty = sum(1 for r in order_rows if r["Qty"])
    print(f"  {len(order_rows)} physical products | {n_with_qty} need reordering.")

    if not order_rows:
        print("[warn] No physical products with EAN found in ProductList.")
        print("       Run 'map' first to assign EANs from the B2B catalog, then retry.")
        return

    # ── 2. Write to {MFR} Orders sheet ───────────────────────────────────────
    oname = orders_sheet_name(mfr)
    tab   = f"Order {date.today().isoformat()}"
    print(f"\nWriting order tab '{tab}' to '{oname}' ...")
    try:
        oss = open_sheet_by_name(oname)
    except Exception as _e:
        import gspread as _gs
        if not isinstance(_e, _gs.exceptions.SpreadsheetNotFound):
            raise
        print(f"  Sheet '{oname}' not found — creating it ...")
        oss = create_sheet(oname)
    write_tab(oss, tab, order_rows)

    # Add checkbox data validation to "add to Billbee stock" column
    _cols = list(order_rows[0].keys())
    _add_stock_col = _cols.index("add to Billbee stock")  # 0-based
    _ws_order = oss.worksheet(tab)
    oss.batch_update({"requests": [{
        "setDataValidation": {
            "range": {
                "sheetId": _ws_order.id,
                "startRowIndex": 1,  # skip header row
                "endRowIndex": len(order_rows) + 1,
                "startColumnIndex": _add_stock_col,
                "endColumnIndex": _add_stock_col + 1,
            },
            "rule": {"condition": {"type": "BOOLEAN"}, "showCustomUi": True},
        }
    }]})

    orders_sheet_id = oss.id   # save ID — re-open by key after the pause to avoid
                               # stale connections and duplicate-name ambiguity
    webbrowser.open(oss.url)
    print(f"\nSheet open. Review quantities for in-stock items.")
    _wait(">> Press Enter to open the browser and fill the cart ...")

    if dry_run:
        print("[DRY-RUN] Cart filling skipped.")
        return

    _do_cart_fill(mfr, tab, orders_sheet_id, order_rows)

    # (cart filling handed off to _do_cart_fill)


# ---------------------------------------------------------------------------
# Cart-fill helper — shared by cmd_order and cmd_fill_cart
# ---------------------------------------------------------------------------

def _do_cart_fill(mfr: str, tab: str, orders_sheet_id: str,
                  order_rows: list[dict]) -> None:
    """
    Re-read the visible rows from an order tab, fill the cart via the browser,
    write actual cart quantities back to the sheet, then offer to update Billbee stock.

    Called by cmd_order (after writing the order tab) and cmd_fill_cart (standalone).
    """
    from google_sheets_client import read_tab, read_tab_visible
    from playwright.sync_api import sync_playwright, Error as _PWError

    sf = session_file(mfr)

    # ── Column indices from order_rows for sheet write-back ──────────────────
    _order_headers = list(order_rows[0].keys()) if order_rows else []
    _qty_col  = (_order_headers.index("Qty")  + 1) if "Qty"  in _order_headers else None
    _cost_col = (_order_headers.index("Cost") + 1) if "Cost" in _order_headers else None

    # EAN → (sheet_row_1based, original_row)
    _ean_to_info = {
        str(r.get("EAN") or ""): (i, r)
        for i, r in enumerate(order_rows, start=2)
        if str(r.get("EAN") or "").strip()
    }

    # ── Re-read visible rows (user may have changed Qty in the sheet) ────────
    oss = open_sheet(orders_sheet_id)
    to_order = [
        r for r in read_tab_visible(oss, tab)
        if str(r.get("Qty") or "").strip() not in ("", "0")
        and str(r.get("URL") or "").strip()
    ]
    if not to_order:
        print("No visible rows with Qty + URL. Nothing to add to cart.")
        return

    print(f"\nOpening browser to fill cart for {len(to_order)} item(s) ...")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=50)
        ctx     = browser.new_context(storage_state=str(sf))
        page    = ctx.new_page()
        _brought_to_front = False

        # ── Step 1: fill cart ─────────────────────────────────────────────────
        for item in to_order:
            ean      = str(item.get("EAN") or "")
            qty_orig = str(item.get("Qty") or "1").strip()
            url      = str(item.get("URL") or "")
            sku      = str(item.get("SKU") or ean)
            print(f"  → {sku}  qty={qty_orig}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
                if not _brought_to_front:
                    _bring_browser_to_front(page)
                    _brought_to_front = True

                qty_sel = (
                    page.query_selector(f"article[data-product-id='{ean}'] input[type='number']")
                    or page.query_selector("input[type='number'][name*='qty' i]")
                    or page.query_selector("input[type='number'][id*='qty' i]")
                    or page.query_selector("input[type='number'][class*='quantity' i]")
                    or page.query_selector("input[type='number']")
                )
                if not qty_sel:
                    print(f"    [warn] no qty input found — skipping")
                    continue

                qty_int = max(1, int(float(qty_orig)))
                js_result = page.evaluate(f"""el => {{
                    const step = Math.max(1, parseInt(el.step) || 1);
                    let qty = {qty_int};
                    if (step > 1 && qty % step !== 0) {{
                        qty = Math.ceil(qty / step) * step;
                    }}
                    el.value = String(qty);
                    el.dispatchEvent(new Event('input',  {{bubbles: true}}));
                    el.dispatchEvent(new Event('change', {{bubbles: true}}));
                    const form = el.closest('form');
                    if (!form) return 'no-form:qty=' + qty;
                    let btn = form.querySelector("button[type='submit'], input[type='submit']");
                    if (btn) {{ btn.click(); return 'ok-submit:qty=' + qty; }}
                    btn = form.querySelector("button");
                    if (btn) {{ btn.click(); return 'ok-btn:qty=' + qty; }}
                    try {{ form.submit(); return 'ok-form.submit:qty=' + qty; }} catch(e) {{}}
                    return 'no-btn:qty=' + qty;
                }}""", qty_sel)

                qty = qty_orig
                if ":qty=" in js_result:
                    qty = js_result.split(":qty=")[-1]
                if qty != qty_orig:
                    print(f"    qty adjusted: {qty_orig} → {qty}")
                if js_result.startswith("ok"):
                    try:
                        page.wait_for_load_state("networkidle", timeout=4000)
                    except Exception:
                        page.wait_for_timeout(1000)
                    print(f"    added (qty={qty})")
                else:
                    print(f"    [warn] could not submit form ({js_result})")

            except _PWError as e:
                print(f"    [error] {e}")

        # ── Step 2: navigate to cart ──────────────────────────────────────────
        print("\nNavigating to cart ...")
        cart_url = page.evaluate("""() => {
            const links = Array.from(document.querySelectorAll('a[href]'));
            const a = links.find(l => /cart/i.test(l.getAttribute('href') || ''));
            return a ? a.href : null;
        }""")
        if cart_url:
            try:
                resp = page.goto(cart_url, wait_until="domcontentloaded", timeout=10000)
                if resp and resp.status < 400:
                    try:
                        page.wait_for_load_state("networkidle", timeout=4000)
                    except Exception:
                        pass
                    print(f"  Cart: {cart_url}")
            except Exception as e:
                print(f"  [warn] Cart navigation failed: {e}")
        else:
            print("  [warn] No cart link found. Please navigate manually.")

        _wait("\n>> Review cart in browser (adjust qtys / remove items), then press Enter ...")

        # ── Step 3: scrape cart and write back to sheet ───────────────────────
        try:
            print("Reading cart ...")
            cart_items = page.evaluate("""() => {
                const items = [];
                // Pattern 1: data-product-id attribute (generic shops)
                document.querySelectorAll('article[data-product-id]').forEach(a => {
                    const ean = a.getAttribute('data-product-id');
                    const inp = a.querySelector('input[type="number"]');
                    const qty = inp ? parseInt(inp.value, 10) : 0;
                    if (ean && qty > 0) items.push({ean, qty});
                });
                if (items.length) return items;
                document.querySelectorAll('[data-product-id]').forEach(el => {
                    const ean = el.getAttribute('data-product-id');
                    const inp = el.querySelector('input[type="number"]');
                    const qty = inp ? parseInt(inp.value, 10) : 0;
                    if (ean && qty > 0) items.push({ean, qty});
                });
                if (items.length) return items;
                // Pattern 2: EAN embedded in product image filename (e.g. 5420047902269_01.jpg)
                document.querySelectorAll('article').forEach(art => {
                    const img = art.querySelector('img');
                    if (!img) return;
                    const m = (img.src || '').match(/\\/([0-9]{13})_/);
                    if (!m) return;
                    const ean = m[1];
                    const inp = art.querySelector('input[type="number"]');
                    if (!inp) return;
                    const qty = parseInt(inp.value, 10);
                    if (qty > 0) items.push({ean, qty});
                });
                if (items.length) return items;
                // Pattern 3: qty inputs with nearest data-product-id / data-id ancestor
                document.querySelectorAll('input[type="number"]').forEach(inp => {
                    const qty = parseInt(inp.value, 10);
                    if (qty > 0) {
                        const holder = inp.closest('[data-product-id]') || inp.closest('[data-id]');
                        const ean = holder
                            ? (holder.getAttribute('data-product-id') || holder.getAttribute('data-id'))
                            : null;
                        if (ean) items.push({ean, qty});
                    }
                });
                return items;
            }""")
            print(f"  {len(cart_items)} item(s) in cart.")

            if not cart_items:
                # Dump cart HTML so we can inspect the structure and fix selectors
                html = page.content()
                dump_path = _tmp(mfr) / f"{mfr}_cart_debug.html"
                dump_path.write_text(html, encoding="utf-8")
                print(f"  [debug] Cart HTML saved to {dump_path}")

            if cart_items:
                cart_by_ean = {str(ci["ean"]): int(ci["qty"]) for ci in cart_items}
                updates = []
                for ean, (sheet_row, orig_row) in _ean_to_info.items():
                    new_qty = cart_by_ean.get(ean, 0)
                    if _qty_col:
                        updates.append({
                            "range":  f"{_col_letter(_qty_col)}{sheet_row}",
                            "values": [[new_qty if new_qty else ""]],
                        })
                    if _cost_col:
                        try:
                            price = float(str(orig_row.get("Price") or "0").replace(",", "."))
                            cost  = round(price * new_qty, 2) if new_qty else ""
                        except (TypeError, ValueError):
                            cost = ""
                        updates.append({
                            "range":  f"{_col_letter(_cost_col)}{sheet_row}",
                            "values": [[cost]],
                        })
                if updates:
                    print(f"  Writing back to sheet ...")
                    # Re-open connection in case it timed out during user review
                    _oss = open_sheet(orders_sheet_id)
                    _oss.worksheet(tab).batch_update(updates, value_input_option="USER_ENTERED")
                    print(f"  Sheet updated.")
            else:
                print("  [warn] No items found in cart — sheet not updated.")

        except Exception as e:
            print(f"  [error] Cart read/write failed: {e}")
            print("  Sheet was not updated.")

        # ── Step 4: wait for user to place the order ──────────────────────────
        _wait("\n>> Place your order in the browser, then press Enter to close the browser ...")
        browser.close()

    # ── Step 5: update Billbee stock ─────────────────────────────────────────
    print("\nNext: update Billbee stock from ordered quantities.")
    print("Uncheck 'add to Billbee stock' in the sheet for items not yet available.")
    cmd_add_stock(mfr, tab)


# ---------------------------------------------------------------------------
# Fill-cart command — standalone re-run of the cart-fill step
# ---------------------------------------------------------------------------

def cmd_fill_cart(mfr: str, tab: str | None) -> None:
    """
    Open an existing order tab in '{MFR} Orders' and fill the cart — without
    re-fetching Billbee stock, re-writing the order tab, etc.

    If --tab is omitted, uses the most recent 'Order YYYY-MM-DD' tab.
    """
    import webbrowser
    from google_sheets_client import read_tab

    sf = session_file(mfr)
    if not sf.exists():
        print(f"[error] No session for '{mfr}'. Run 'setup' first.")
        sys.exit(1)

    oname = orders_sheet_name(mfr)
    print(f"Opening '{oname}' ...")
    oss = open_sheet_by_name(oname)
    orders_sheet_id = oss.id

    if not tab:
        tab_names = [ws.title for ws in oss.worksheets()]
        order_tabs = sorted(
            [t for t in tab_names if t.startswith("Order 20")],
            reverse=True,
        )
        if not order_tabs:
            print(f"[error] No 'Order YYYY-MM-DD' tabs found in '{oname}'.")
            sys.exit(1)
        tab = order_tabs[0]
        print(f"  Using most recent tab: '{tab}'")

    order_rows = read_tab(oss, tab)
    if not order_rows:
        print(f"[error] Tab '{tab}' is empty.")
        sys.exit(1)

    print(f"  {len(order_rows)} rows in '{tab}'.")
    webbrowser.open(oss.url)
    print(f"\nSheet open. Adjust quantities if needed, then press Enter.")
    _wait(">> Press Enter to open the browser and fill the cart ...")

    _do_cart_fill(mfr, tab, orders_sheet_id, order_rows)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generic B2B webshop cart automation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # setup
    sp = sub.add_parser("setup", help="Log in and save session.")
    sp.add_argument("--manufacturer", required=True, metavar="MFR",
                    help="Manufacturer code, e.g. TRX or FRE.")
    sp.add_argument("--url", default=None,
                    help="B2B webshop URL (required first time; reused from config on re-runs).")

    # explore
    ep = sub.add_parser("explore", help="Crawl catalog → Google Sheet (uses cache).")
    ep.add_argument("--manufacturer", required=True, metavar="MFR")
    ep.add_argument("--refresh", action="store_true",
                    help="Re-crawl even if a cache exists.")
    ep.add_argument("--dump-html", action="store_true",
                    help="Save the first product page HTML to .tmp/ for selector debugging.")
    ep.add_argument("--url", default=None, metavar="URL",
                    help="Override start URL (e.g. direct link to the SHOP page).")
    ep.add_argument("--no-crawl", action="store_true",
                    help="Don't follow links — only scrape the start URL (useful when --url "
                         "points directly to a page listing all products).")

    # map
    mp = sub.add_parser("map", help="Match B2B catalog → ProductList EAN column.")
    mp.add_argument("--manufacturer", required=True, metavar="MFR")
    mp.add_argument("--no-replace", action="store_true",
                    help="Skip rows that already have an EAN (default: overwrite).")

    # order
    op = sub.add_parser("order", help="Fill the cart from the mapping sheet.")
    op.add_argument("--manufacturer", required=True, metavar="MFR")
    op.add_argument("--dry-run", action="store_true")
    op.add_argument("--factor", type=float, default=1.0,
                    help="Reorder factor applied to stock target (default 1.0). "
                         "E.g. 1.5 orders up to 150%% of target.")
    op.add_argument("--cached", action="store_true",
                    help="Load Billbee stock from local cache instead of fetching live. "
                         "Cache is written automatically on each live fetch.")

    # add-stock
    asp = sub.add_parser("add-stock",
                         help="Add ordered quantities from a completed order tab to Billbee stock.")
    asp.add_argument("--manufacturer", required=True, metavar="MFR")
    asp.add_argument("--tab", required=True,
                     help="Order tab name, e.g. 'Order 2026-03-08'.")

    # fill-cart
    fcp = sub.add_parser("fill-cart",
                         help="Re-run the cart-fill step for an existing order tab.")
    fcp.add_argument("--manufacturer", required=True, metavar="MFR")
    fcp.add_argument("--tab", default=None,
                     help="Order tab name (default: most recent 'Order YYYY-MM-DD' tab).")

    args = parser.parse_args()
    mfr  = args.manufacturer.upper()

    if args.command == "setup":
        cmd_setup(mfr, args.url)
    elif args.command == "explore":
        cmd_explore(mfr, refresh=args.refresh, dump_html=args.dump_html,
                    start_url=args.url, no_crawl=args.no_crawl)
    elif args.command == "map":
        cmd_map(mfr, replace=not args.no_replace)
    elif args.command == "order":
        cmd_order(mfr, dry_run=args.dry_run, factor=args.factor, cached=args.cached)
    elif args.command == "add-stock":
        cmd_add_stock(mfr, args.tab)
    elif args.command == "fill-cart":
        cmd_fill_cart(mfr, args.tab)


if __name__ == "__main__":
    main()
