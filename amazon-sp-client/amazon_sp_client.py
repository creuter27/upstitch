"""
Amazon Selling Partner API client.

Auth: LWA (Login with Amazon) OAuth2 + AWS SigV4, handled automatically
by the python-amazon-sp-api library. Credentials are loaded from the .env
file adjacent to this module.

First run will fetch a fresh access token using the refresh_token; subsequent
calls reuse it until expiry, then refresh automatically.

Credentials required in .env (see .env.template):
  AMAZON_LWA_APP_ID, AMAZON_LWA_CLIENT_SECRET, AMAZON_REFRESH_TOKEN,
  AMAZON_AWS_ACCESS_KEY, AMAZON_AWS_SECRET_KEY, AMAZON_ROLE_ARN,
  AMAZON_MARKETPLACE_ID (default: A1PA6795UKMFR9 = Amazon.de)
"""

import io
import os
import zipfile
from pathlib import Path

import requests
from dotenv import load_dotenv
from sp_api.api import Invoices, Orders
from sp_api.base import Marketplaces, SellingApiException
from sp_api.base.credential_provider import CredentialProvider

# Load credentials from the .env file next to this module file.
load_dotenv(Path(__file__).parent / ".env")

# Map marketplace ID string → sp_api.base.Marketplaces enum member.
# We do this at import time so a bad AMAZON_MARKETPLACE_ID fails early.
_MARKETPLACE_ID = os.getenv("AMAZON_MARKETPLACE_ID", "A1PA6795UKMFR9")
_MARKETPLACE_MAP: dict[str, Marketplaces] = {m.marketplace_id: m for m in Marketplaces}
if _MARKETPLACE_ID not in _MARKETPLACE_MAP:
    raise ValueError(
        f"Unknown AMAZON_MARKETPLACE_ID={_MARKETPLACE_ID!r}. "
        "Check your .env file. Example for Germany: A1PA6795UKMFR9"
    )
_MARKETPLACE = _MARKETPLACE_MAP[_MARKETPLACE_ID]


def _credentials() -> dict:
    """Return the credentials dict expected by python-amazon-sp-api."""
    required = {
        "refresh_token": "AMAZON_REFRESH_TOKEN",
        "lwa_app_id": "AMAZON_LWA_APP_ID",
        "lwa_client_secret": "AMAZON_LWA_CLIENT_SECRET",
        "aws_access_key": "AMAZON_AWS_ACCESS_KEY",
        "aws_secret_key": "AMAZON_AWS_SECRET_KEY",
        "role_arn": "AMAZON_ROLE_ARN",
    }
    creds = {}
    missing = []
    for key, env_var in required.items():
        val = os.getenv(env_var)
        if not val:
            missing.append(env_var)
        else:
            creds[key] = val
    if missing:
        raise EnvironmentError(
            f"Amazon SP-API credentials missing from .env: {', '.join(missing)}\n"
            f"See {Path(__file__).parent / '.env.template'} for the required keys."
        )
    return creds


class AmazonSpClient:
    """
    Thin wrapper around python-amazon-sp-api for the operations we need.

    Each method creates a fresh API object — the library manages token caching
    internally via CredentialProvider, so this is safe and avoids stale state.
    """

    def __init__(self):
        self._creds = _credentials()
        self._marketplace = _MARKETPLACE

    def _invoices_api(self) -> Invoices:
        return Invoices(credentials=self._creds, marketplace=self._marketplace)

    def _orders_api(self) -> Orders:
        return Orders(credentials=self._creds, marketplace=self._marketplace)

    # ------------------------------------------------------------------
    # Invoice download
    # ------------------------------------------------------------------

    def get_invoice_pdf(self, amazon_order_id: str) -> bytes | None:
        """
        Attempt to download an Amazon-generated invoice PDF for the given order.

        Flow:
          1. Call Invoices.get_invoices(orderIds=[amazon_order_id]) to find
             invoices matching this order.
          2. If found, fetch the invoicesDocumentId and call
             get_invoices_document() to obtain the download URL.
          3. Download the ZIP at that URL, extract the first PDF inside it,
             and return the raw bytes.

        Returns:
          bytes  — raw PDF content if an invoice was found and downloaded.
          None   — if no invoice exists, the marketplace is not supported,
                   or any other non-fatal error occurs (logged to stdout).

        Note:
          As of early 2026 the Invoices API is officially designated for
          Brazilian FBA inbound invoices. We call it here because the user
          has confirmed access and credentials for their German marketplace.
          If the API returns an empty list or an unsupported-marketplace error,
          this method returns None and the caller treats the invoice as still
          missing.
        """
        api = self._invoices_api()

        # Step 1: query invoices for this order ID
        try:
            resp = api.get_invoices(orderIds=[amazon_order_id])
        except SellingApiException as exc:
            print(f"    [amazon] get_invoices({amazon_order_id}) failed: {exc}")
            return None

        invoices = (resp.payload or {}).get("invoices", [])
        if not invoices:
            print(f"    [amazon] no invoice found via SP-API for order {amazon_order_id}")
            return None

        # Step 2: get the document download URL
        # Each invoice may have an invoicesDocumentId
        doc_id = invoices[0].get("invoicesDocumentId")
        if not doc_id:
            print(f"    [amazon] invoice found but no invoicesDocumentId for {amazon_order_id}")
            return None

        try:
            doc_resp = api.get_invoices_document(invoicesDocumentId=doc_id)
        except SellingApiException as exc:
            print(f"    [amazon] get_invoices_document({doc_id}) failed: {exc}")
            return None

        url = (doc_resp.payload or {}).get("documentUrl") or (doc_resp.payload or {}).get("url")
        if not url:
            print(f"    [amazon] no download URL in invoices document response for {amazon_order_id}")
            print(f"    [amazon] raw response: {doc_resp.payload}")
            return None

        # Step 3: download the ZIP and extract the first PDF
        return self._download_invoice_from_zip(url, amazon_order_id)

    def _download_invoice_from_zip(self, url: str, order_id: str) -> bytes | None:
        """Download a ZIP from url and return the bytes of the first PDF inside."""
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
        except requests.RequestException as exc:
            print(f"    [amazon] failed to download invoice ZIP for {order_id}: {exc}")
            return None

        content_type = r.headers.get("Content-Type", "")
        if "application/pdf" in content_type:
            # Some endpoints return the PDF directly (not zipped)
            return r.content

        try:
            with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                pdf_names = [n for n in zf.namelist() if n.lower().endswith(".pdf")]
                if not pdf_names:
                    print(f"    [amazon] ZIP for {order_id} contains no PDF files: {zf.namelist()}")
                    return None
                return zf.read(pdf_names[0])
        except zipfile.BadZipFile:
            # Not a ZIP — log the first 200 bytes to help diagnose
            print(f"    [amazon] response for {order_id} is not a PDF or ZIP "
                  f"(Content-Type: {content_type}). First 200 bytes: {r.content[:200]}")
            return None

    # ------------------------------------------------------------------
    # Order lookup (useful for debugging credentials and order existence)
    # ------------------------------------------------------------------

    def get_order(self, amazon_order_id: str) -> dict | None:
        """
        Fetch basic order data from SP-API.
        Useful for verifying credentials and confirming the order exists.
        Returns the order dict, or None on error.
        """
        api = self._orders_api()
        try:
            resp = api.get_order(order_id=amazon_order_id)
            return resp.payload
        except SellingApiException as exc:
            print(f"    [amazon] get_order({amazon_order_id}) failed: {exc}")
            return None
