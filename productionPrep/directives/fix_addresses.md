# Directive: Fix Billbee Delivery Addresses

## Purpose
Detect and correct incorrectly entered delivery addresses in Billbee customer orders.
Run regularly (daily or before each fulfillment batch) to catch address issues before packages ship.

## Inputs
- Billbee API (orders since last run)
- OpenCage Geocoding API (address validation)
- Anthropic API (Claude Haiku for fix suggestions)
- `data/last_run.json` (timestamp of previous run)
- `data/feedback.jsonl` (historical fix decisions for few-shot learning)

## Outputs
- Updated order addresses in Billbee (for accepted fixes)
- New entries appended to `data/feedback.jsonl`
- Updated `data/last_run.json`

## How to Run

```bash
cd ~/code/fixBillbeeAdresses
.venv/bin/python main.py                     # normal run
.venv/bin/python main.py --dry-run           # preview without changing Billbee
.venv/bin/python main.py --since 2026-01-01  # reprocess from a specific date
.venv/bin/python main.py --skip-geocode      # skip OpenCage (faster, no API cost)
```

## Required Environment Variables (.env)
```
BILLBEE_API_KEY=...
BILLBEE_API_USERNAME=...
BILLBEE_API_PASSWORD=...
OPENCAGE_API_KEY=...      # sign up free at https://opencagedata.com/
ANTHROPIC_API_KEY=...
```

## Setup (first time)
```bash
cd ~/code/fixBillbeeAdresses
python3.14 -m venv .venv
.venv/bin/pip install -r requirements.txt
pip install -e ~/code/billbee-python-client/   # shared Billbee client
cp .env.example .env
# Fill in .env with real credentials
```

## Rule-Based Checks (execution/check_address.py)

These run on every order, no API required:

| Code | Description |
|------|-------------|
| `HOUSE_NUMBER_IN_STREET` | Street field ends with digits (e.g. "Hauptstraße 42") |
| `HOUSE_NUMBER_IN_ADDITION` | AddressAddition contains only a number |
| `STREET_IN_COMPANY` | Company field contains a street suffix while Street is empty |
| `STREET_IN_COMPANY_WITH_STREET_FILLED` | Company looks like a street but Street is also filled |
| `MISSING_ZIP` | Zip field empty |
| `INVALID_ZIP_FORMAT` | Zip format doesn't match country (DE=5 digits, AT/CH=4 digits, etc.) |
| `MISSING_CITY` | City field empty |
| `MISSING_STREET` | Street and Company both empty |
| `MISSING_HOUSE_NUMBER` | Street filled but no number found anywhere |
| `LOW_GEOCODE_CONFIDENCE` | OpenCage returned confidence < 5/10 |

## Fix Suggestion Logic (execution/suggest_fix.py)
1. Rule checker identifies issues
2. OpenCage geocodes the address and returns a parsed result + confidence score
3. Claude Haiku is asked to suggest corrections, given:
   - The original address
   - The detected issues
   - The OpenCage result
   - Up to 20 recent accepted fixes as few-shot examples
4. Claude returns only the fields that need to change

## User Interaction Options
- `y` Accept suggested fix → applied to Billbee + logged as accepted
- `n` Reject → user prompted for optional note → logged as rejected
- `e` Edit manually → field-by-field prompt → applied + logged as accepted
- `s` Skip → no action, no log entry

## Feedback Loop (data/feedback.jsonl)
Every accepted fix is logged and fed back into future Claude prompts as a few-shot example.
Over time this teaches the model the specific patterns in your order data.

After several months of feedback, you may be able to run with `--auto-accept` (not yet implemented)
if confidence thresholds are met.

## Known Edge Cases

### PO Boxes (Postfach)
"Postfach" entries look like missing street numbers. Rules will flag them.
**Currently:** Skip these manually with `s`.
**Future:** Add a `POSTFACH` rule to detect and exempt them.

### Austrian and Swiss Addresses
ZIP codes are 4 digits (not 5). This is handled in the ZIP validation rule.
Street suffixes (Gasse, Steig) are in the suffix list.

### Names in Street Field
Some customers enter their name in the street field. This won't be caught by
the current rules. Claude may still detect it if the geocode fails.

### Apartment Numbers in AddressAddition
`AddressAddition` can legitimately contain things like "Wohnung 3" or "2. OG".
The `HOUSE_NUMBER_IN_ADDITION` rule only triggers for values that are PURELY
a number (e.g. "42"). Combined values like "Apt. 5" are not flagged.

### International Addresses (non-EU)
ZIP format validation only covers: DE, AT, CH, NL, BE, FR, IT, ES, PL, CZ, LU.
Other countries: format check is skipped. OpenCage still validates.

## API Rate Limits
- Billbee: 2 req/sec (client throttles to 0.6 s between calls)
- OpenCage free tier: 2,500 req/day, 1 req/sec (geocode_address.py sleeps 1.1 s between calls)
- Anthropic: Standard limits apply; Haiku is fast and cheap (~$0.0002 per address)

## Geocode Cache
Results from OpenCage are cached in `.tmp/geocode_cache.json` (keyed by MD5 of address string).
Re-running the tool for the same address won't consume API quota.
Delete this file to force fresh geocoding.

## Learnings (append as discovered)
- 2026-02-26: Initial implementation. Rules cover the most common German patterns.
  OpenCage free tier is sufficient for typical B2C order volumes (<100/day).
