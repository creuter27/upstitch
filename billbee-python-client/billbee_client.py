"""
Billbee API client.
Auth: X-Billbee-Api-Key header + HTTP Basic Auth (username + API password).
Rate limit: 2 req/sec per API-key+user combo — enforced via 0.5 s sleep between calls.
"""

import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

# Load credentials from this file's own directory so all consumer projects get
# the Billbee creds automatically, regardless of their working directory.
load_dotenv(Path(__file__).parent / ".env")

BASE_URL = "https://api.billbee.io/api/v1"


class BillbeeClient:
    def __init__(self):
        api_key = os.environ["BILLBEE_API_KEY"]
        username = os.environ["BILLBEE_API_USERNAME"]
        password = os.environ["BILLBEE_API_PASSWORD"]

        self.session = requests.Session()
        self.session.headers.update({"X-Billbee-Api-Key": api_key})
        self.session.auth = (username, password)
        self._last_call = 0.0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, allow_error_codes: tuple = (),
                 **kwargs) -> dict:
        """
        Make a request with throttling and automatic 429 retry.

        allow_error_codes: HTTP status codes that should NOT raise — their JSON body
        is returned as-is instead (useful for endpoints that return structured errors).
        """
        max_retries = 5
        for attempt in range(max_retries):
            self._throttle()
            resp = self.session.request(method, f"{BASE_URL}{path}", **kwargs)
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", 5))
                wait = retry_after + 1  # add 1 s buffer
                print(f"[rate-limit] 429 received — waiting {wait:.0f} s before retry (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait)
                continue
            if resp.status_code in allow_error_codes:
                return resp.json()
            if not resp.ok:
                try:
                    body = resp.json()
                except Exception:
                    body = resp.text
                raise requests.HTTPError(
                    f"{resp.status_code} {resp.reason} for url: {resp.url}\nResponse body: {body}",
                    response=resp,
                )
            return {} if not resp.content else resp.json()
        raise RuntimeError(f"Exceeded {max_retries} retries due to rate limiting on {path}")

    def _get(self, path: str, params: dict = None) -> dict:
        return self._request("GET", path, params=params)

    def _post(self, path: str, body: dict) -> dict:
        return self._request("POST", path, json=body)

    def _throttle(self):
        """Ensure at least 0.6 s between calls (conservative stay under 2 req/sec limit)."""
        elapsed = time.monotonic() - self._last_call
        if elapsed < 0.6:
            time.sleep(0.6 - elapsed)
        self._last_call = time.monotonic()

    # ------------------------------------------------------------------
    # Custom field definitions
    # ------------------------------------------------------------------

    def get_custom_field_definitions(self) -> dict[int, str]:
        """Return {field_id: field_name} for all product custom fields."""
        data = self._get("/products/custom-fields")
        definitions = {}
        for item in data.get("Data", []):
            definitions[item["Id"]] = item["Name"]
        return definitions

    # ------------------------------------------------------------------
    # Product retrieval
    # ------------------------------------------------------------------

    def get_product_by_id(self, product_id: int) -> dict:
        """Fetch a single product by Billbee article ID. Returns the product dict."""
        data = self._get(f"/products/{product_id}")
        return data.get("Data", {})

    def get_all_products(self, page_size: int = 250):
        """
        Generator — yields one product dict at a time, paging through the
        full catalog. pageSize capped at 250 (API max).
        """
        page = 1
        while True:
            data = self._get("/products", params={"page": page, "pageSize": page_size})
            items = data.get("Data", [])
            if not items:
                break
            for item in items:
                yield item
            # Stop when we've received the last page
            total = data.get("Paging", {}).get("TotalRows", 0)
            if page * page_size >= total:
                break
            page += 1

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def get_orders(self, order_state_id: int = None, min_date: str = None,
                   page_size: int = 100):
        """
        Generator — yields one order dict at a time.

        Parameters
        ----------
        order_state_id : int, optional
            Filter by Billbee order state (1=Received, 3=Ready to ship, …).
        min_date : str, optional
            ISO-8601 date/datetime string, e.g. "2024-01-15" or
            "2024-01-15T00:00:00". Only orders with CreatedAt >= min_date.
        page_size : int
            Number of orders per page (max 250).
        """
        page = 1
        while True:
            params = {"page": page, "pageSize": page_size}
            if order_state_id is not None:
                params["orderStateId"] = order_state_id
            if min_date:
                params["minOrderDate"] = min_date
            data = self._get("/orders", params=params)
            items = data.get("Data", [])
            if not items:
                break
            for item in items:
                yield item
            total = data.get("Paging", {}).get("TotalRows", 0)
            if page * page_size >= total:
                break
            page += 1

    # ------------------------------------------------------------------
    # Documents (invoice / delivery note)
    # ------------------------------------------------------------------

    def create_invoice(self, billbee_order_id: int) -> dict | None:
        """
        Attempt to render an invoice for an order (without returning the PDF).

        IMPORTANT: Despite the name, this endpoint does NOT create a new invoice
        record. It only renders/retrieves an invoice that already exists in Billbee.
        If no invoice has been assigned yet (ErrorCode 3), this returns None.

        To ensure invoices exist before calling this, configure a Billbee automation
        rule: Settings → Automation → "when order enters state X → create invoice".

        On success returns the invoice data dict which may include:
          - InvoiceNumber: the assigned invoice number (e.g. "RE-2026-001")
          - PdfDownloadUrl: direct URL to download the PDF (use download_pdf_from_url())
          - InvoiceDate, TotalGross, TotalNet

        Returns None if ErrorCode 3 ("no invoice created yet for this order").

        Billbee endpoint: POST /orders/CreateInvoice/{billbee_order_id}
        ID required: BillBeeOrderId (integer), NOT the external order Id.
        """
        resp = self._request("POST", f"/orders/CreateInvoice/{billbee_order_id}",
                             allow_error_codes=(400,))
        if resp.get("ErrorCode") == 3:  # InvoiceNotCreated
            error_msg = resp.get("ErrorMessage") or resp.get("ErrorDescription") or "(no message)"
            print(f"    [Billbee] CreateInvoice ErrorCode=3: {error_msg}")
            return None
        return resp.get("Data") or {}

    def download_pdf_from_url(self, url: str) -> bytes:
        """
        Download a PDF from a URL returned by Billbee (e.g. PdfDownloadUrl from create_invoice).
        Uses the same authenticated session as all other API calls.
        This is NOT subject to the 5-minute rate limit on CreateInvoice.
        """
        self._throttle()
        resp = self.session.get(url)
        resp.raise_for_status()
        return resp.content

    def get_invoice_pdf(self, billbee_order_id: int) -> bytes | None:
        """
        Retrieve the invoice PDF for an order as raw bytes.
        Returns None if no invoice has been generated yet in Billbee (ErrorCode 3).

        Call create_invoice() first to ensure the invoice exists before calling this.

        Billbee endpoint: POST /orders/CreateInvoice/{billbee_order_id}?includeInvoicePdf=true
        ID required: BillBeeOrderId field from the order (integer), NOT the external order Id.
        Response: JSON {"Data": {"PDFData": "<base64>", "InvoiceNumber": "...", ...}}

        Rate limit: extra throttled to max 1 per 5 minutes per order+api-key.
        """
        import base64
        resp = self._request("POST", f"/orders/CreateInvoice/{billbee_order_id}",
                             allow_error_codes=(400,),
                             params={"includeInvoicePdf": "true"})
        if resp.get("ErrorCode") == 3:  # InvoiceNotCreated
            return None
        pdf_b64 = (resp.get("Data") or {}).get("PDFData", "")
        if not pdf_b64:
            raise ValueError(f"No PDFData in CreateInvoice response for order {billbee_order_id}. "
                             f"Response: {resp}")
        return base64.b64decode(pdf_b64)

    def get_delivery_note_pdf(self, billbee_order_id: int) -> bytes:
        """
        Create (or retrieve existing) delivery note for an order and return the PDF as raw bytes.

        Billbee endpoint: POST /orders/CreateDeliveryNote/{billbee_order_id}?includePdf=true
        ID required: BillBeeOrderId field from the order (integer), NOT the external order Id.
        Response: JSON {"Data": {"PDFData": "<base64>", "DeliveryNoteNumber": "...", ...}}

        Rate limit: extra throttled to max 1 per 5 minutes per order+api-key.
        Note: calling this multiple times for the same order may create duplicate delivery notes
        in Billbee. The fetch script prevents this by skipping already-saved files.
        """
        import base64
        data = self._request("POST", f"/orders/CreateDeliveryNote/{billbee_order_id}",
                             params={"includePdf": "true"})
        pdf_b64 = data.get("Data", {}).get("PDFData", "")
        if not pdf_b64:
            raise ValueError(f"No PDFData in CreateDeliveryNote response for order {billbee_order_id}")
        return base64.b64decode(pdf_b64)

    def create_product(self, data: dict) -> dict:
        """
        POST /products — create a new product.
        data must be a complete product dict WITHOUT an Id field.
        Returns the created product dict (including the new Billbee-assigned Id).
        """
        resp = self._request("POST", "/products", json=data)
        # Response shape: {"Data": {...product...}, ...} or the product dict directly
        return resp.get("Data", resp) if isinstance(resp, dict) else resp

    def patch_product(self, product_id: int, fields: dict) -> dict:
        """
        PATCH /products/{id} — partial product update.

        Only these fields are patchable (confirmed Feb 2026):
          SKU, ShortText, ShortDescription, EAN, Description,
          Manufacturer, Weight (gross), WeightNet.

        NOT patchable via the public API:
          CustomFields, BillOfMaterial, TaricNumber, LengthCm,
          WidthCm, HeightCm, CostPrice, Price, CountryOfOrigin.

        fields must be a dict containing ONLY the keys to update.
        Returns the updated product dict (Data field of the response).
        Raises RuntimeError if the API reports an error in the response body.
        """
        resp = self._request("PATCH", f"/products/{product_id}", json=fields)
        # Billbee returns HTTP 200 even for invalid fields, embedding the error
        # in ErrorMessage while ErrorDescription stays "NoError". Check explicitly.
        err = resp.get("ErrorMessage") if isinstance(resp, dict) else None
        if err:
            raise RuntimeError(f"Billbee PATCH error for product {product_id}: {err}")
        return resp.get("Data", resp) if isinstance(resp, dict) else resp

    def update_product(self, product_id: int, data: dict) -> dict:
        """
        BROKEN — Billbee's API has no PUT /products/{id} endpoint; returns 500.
        Use patch_product() for partial field updates instead.
        Kept only so restore_from_backup.py continues to compile.
        """
        return self._request("PUT", f"/products/{product_id}", json=data)

    def delete_product(self, product_id: int) -> None:
        """
        DELETE /products/{id}.
        Uses session.delete directly (not _request) because DELETE may return
        204 No Content, which would cause resp.json() to fail.
        """
        self._throttle()
        resp = self.session.delete(f"{BASE_URL}/products/{product_id}")
        resp.raise_for_status()

    def update_stock(self, sku: str, new_quantity: float,
                     stock_id: int = 0, reason: str = "") -> dict:
        """
        POST /products/updatestock — set absolute stock quantity by SKU.
        new_quantity is the desired absolute level (not a delta).
        stock_id is the stock location Id (0 = default single-stock).
        """
        body: dict = {"Sku": sku, "NewQuantity": new_quantity}
        if stock_id:
            body["StockId"] = stock_id
        if reason:
            body["Reason"] = reason
        return self._post("/products/updatestock", body)

    def search_products(self, term: str, debug_dir: Path = None):
        """
        Search products via POST /api/v1/search using Lucene query syntax.
        On the very first call, saves the raw response to debug_dir/search_debug.json
        so we can inspect the shape and self-anneal the query format.

        Returns a list of product dicts (or empty list if search yields no results).
        NOTE: The exact Lucene field syntax for custom fields is undocumented.
              Inspect search_debug.json after the first run and update this method
              + the directive accordingly.
        """
        body = {
            "Term": term,
            "Type": ["product"],
        }
        data = self._post("/search", body)

        # Save debug output once so we can inspect the response shape
        if debug_dir:
            debug_path = Path(debug_dir) / "search_debug.json"
            if not debug_path.exists():
                debug_path.parent.mkdir(parents=True, exist_ok=True)
                with open(debug_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f"[debug] First search response saved to {debug_path}")

        # Response shape (confirmed): {"Products": [...], "Orders": [...], "Customers": [...]}
        # Each product entry contains only: {Id, ShortText, SKU, Tags}
        # Use these IDs to fetch full product data if needed.
        return data.get("Products", [])
