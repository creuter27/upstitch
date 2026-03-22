# Directive: Download Billbee Products to Google Sheet

## Purpose
Download a snapshot of the Billbee product catalog into a Google Sheet for review and processing. This is always the **first step** of any product-management session.

## When to run
- At the start of every processing session to get a fresh snapshot
- When investigating a specific manufacturer or category

## Script
`execution/download_to_sheet.py`

## Inputs / CLI Arguments

| Argument | Required | Description |
|---|---|---|
| `--manufacturer` | No | Filter by manufacturer (case-insensitive, partial match) |
| `--category` | No | Filter by product category (case-insensitive, partial match) |

Both can be combined. If neither is given, the full catalog is downloaded.

## Examples

```bash
# Full catalog
python execution/download_to_sheet.py

# Single manufacturer
python execution/download_to_sheet.py --manufacturer TRX

# Manufacturer + category
python execution/download_to_sheet.py --manufacturer TRX --category BP
```

## Outputs
- A new Google Sheet titled `Billbee Artikelmanager YYYY-MM-DD`
- Tab `downloaded`: one row per product, one column per field (native fields + custom fields expanded)
- The sheet URL is printed to stdout

## Credential Setup

### Billbee (`.env`)
```
BILLBEE_API_KEY=<your API key>
BILLBEE_API_USERNAME=<your Billbee username>
BILLBEE_API_PASSWORD=<your API password (not login password)>
```

### Google OAuth (`credentials.json`)
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project → Enable Google Sheets API + Google Drive API
3. Create OAuth credentials → Desktop app → Download as `credentials.json`
4. Place `credentials.json` in the project root
5. First run opens a browser for authorization → `token.json` is saved and reused

## Filtering Strategy

**Path A (fast — filtered run only):**
Uses `POST /api/v1/search` to find matching product IDs via SKU/title text search,
then fetches each matched product individually via `GET /api/v1/products/{id}`.
Falls back to Path B if > 100 results (too many individual fetches).

**Path B (full download — unfiltered or fallback):**
Paginates all products (250/page, ~40 API calls), filters locally.

**Important:** Manufacturer and category are currently only encoded in the SKU.
The native `Manufacturer` field is often empty. Custom fields (`Produktkategorie` etc.)
are defined but not yet populated. Filtering is therefore a substring match on the SKU.

## Product Types
- `Type: 1` = physical product (ships to customer; `BillOfMaterial` is null)
- `Type: 2` = listing/BOM product (marketplace listing; has `BillOfMaterial` array)

## Rate Limits
- 2 requests/sec per API-key + user combo
- Script enforces a 0.5 s minimum between calls automatically
- HTTP 429 responses are raised as exceptions — retry after the `Retry-After` header value

## Known Edge Cases
- Products with no custom fields: expand to empty strings automatically
- Custom field IDs may change if fields are renamed in Billbee — re-fetch definitions
- Listing SKUs that don't follow the `listingID-MFR-CATEGORY-...` schema: included as-is; normalization is a separate step
- Search response shape: `{"Products": [...], "Orders": [...], "Customers": [...]}` — each hit is `{Id, ShortText, SKU, Tags}` only (not full product data)

## Self-Anneal Log

| Date | Issue | Fix |
|---|---|---|
| 2026-02-23 | `search_products` was parsing `data["Data"]["Products"]` but actual path is `data["Products"]` | Fixed in `billbee_client.py` |
| 2026-02-23 | `Manufacturer` native field is empty for most products; custom fields unpopulated | Filter now matches against SKU substring instead |
| 2026-02-23 | `Type: 1` = physical, `Type: 2` = listing/BOM (confirmed from API data) | Documented above |
| 2026-02-23 | 429 rate-limit hit at page 13 with 0.5 s throttle | Increased to 0.6 s + added retry loop with `Retry-After` header respect |
