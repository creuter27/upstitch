"""
Use Claude API (claude-haiku) to suggest address fixes.

Builds a prompt that includes:
  - The original address fields
  - Rule-based issues detected
  - OpenCage geocoding result (if available)
  - Up to 20 recent accepted fixes from feedback.jsonl (few-shot examples)

Returns a dict of {field_name: corrected_value} for fields that should change.
Fields not in the returned dict remain unchanged.

Usage:
    from execution.suggest_fix import suggest_fix
    fix = suggest_fix(addr, issues, geocode_result, recent_feedback)
    # fix = {"Street": "Hauptstraße", "HouseNumber": "42"}
"""

import json
import os

import anthropic

# Address fields we deal with
ADDRESS_FIELDS = [
    "FirstName", "LastName", "Company",
    "Street", "HouseNumber", "AddressAddition",
    "Zip", "City", "State", "CountryISO2",
]

# Use Haiku for cost efficiency — this is a simple structured-output task
MODEL = "claude-haiku-4-5-20251001"


def _build_few_shot_block(recent_accepted: list[dict]) -> str:
    """Format recent accepted fixes as few-shot examples."""
    if not recent_accepted:
        return ""
    lines = ["\n## Examples of previously accepted fixes:\n"]
    _ANON = {"Company", "Street", "HouseNumber", "AddressAddition", "Zip", "City", "State", "CountryISO2"}
    for entry in recent_accepted[-20:]:
        orig = {k: v for k, v in entry.get("original", {}).items() if k in _ANON}
        fix = entry.get("user_edit") or entry.get("suggested", {})
        if not fix:
            continue
        lines.append(f"Original: {json.dumps(orig, ensure_ascii=False)}")
        lines.append(f"Fix applied: {json.dumps(fix, ensure_ascii=False)}")
        if entry.get("user_note"):
            lines.append(f"User note: {entry['user_note']}")
        lines.append("")
    return "\n".join(lines)


def suggest_fix(
    addr: dict,
    issues: list,
    geocode_result: dict | None,
    recent_feedback: list[dict],
    api_key: str | None = None,
) -> dict:
    """
    Ask Claude to suggest a corrected address given issues and geocoding info.

    Parameters
    ----------
    addr : dict
        Original Billbee ShippingAddress dict.
    issues : list[Issue]
        Issues detected by check_address.check().
    geocode_result : dict | None
        Result from geocode_address.geocode(), or None if skipped.
    recent_feedback : list[dict]
        Recent accepted feedback entries for few-shot prompting.
    api_key : str, optional
        Anthropic API key. Falls back to ANTHROPIC_API_KEY env var.

    Returns
    -------
    dict
        {field_name: new_value} — only fields that should change.
        Returns {} if Claude cannot determine a fix.
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key or key.startswith("your-"):
        print("[suggest] ANTHROPIC_API_KEY not configured — returning empty suggestion")
        return {}

    # Build issues description
    issues_text = "\n".join(f"  - [{i.code}] {i.description}" for i in issues)

    # Build geocode block
    geocode_text = ""
    if geocode_result:
        geocode_text = (
            f"\nOpenCage geocoding result (confidence {geocode_result.get('confidence', '?')}/10):\n"
            f"  Formatted: {geocode_result.get('formatted', 'N/A')}\n"
            f"  Components: {json.dumps(geocode_result.get('components', {}), ensure_ascii=False)}"
        )

    few_shot = _build_few_shot_block(recent_feedback)

    system_prompt = (
        "You are an address correction assistant for a German e-commerce business. "
        "Your job is to fix incorrectly entered delivery addresses. "
        "You respond ONLY with a valid JSON object containing the corrected fields. "
        "Only include fields that need to change. Do not include unchanged fields. "
        "If you cannot determine a reliable fix, return an empty JSON object {}. "
        "Never guess. Only suggest corrections you are confident about."
    )

    # DSGVO/GDPR: send only address fields to the external API — no personal identifiers.
    # Company is kept because it often contains the street name in malformed addresses.
    ANONYMIZED_FIELDS = [
        "Company", "Street", "HouseNumber", "AddressAddition",
        "Zip", "City", "State", "CountryISO2",
    ]

    user_prompt = f"""Please fix the following delivery address.

## Original address:
{json.dumps({k: addr.get(k, "") for k in ANONYMIZED_FIELDS}, ensure_ascii=False, indent=2)}

## Issues detected:
{issues_text}
{geocode_text}
{few_shot}

Respond ONLY with a JSON object of fields to change, e.g.:
{{"Street": "Hauptstraße", "HouseNumber": "42"}}

Or {{}} if you cannot determine a reliable fix."""

    try:
        client = anthropic.Anthropic(api_key=key)
        message = client.messages.create(
            model=MODEL,
            max_tokens=256,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = message.content[0].text.strip()

        # Extract JSON — Claude sometimes wraps in ```json blocks
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        fix = json.loads(raw)
        # Only keep known address fields and non-empty values
        fix = {k: v for k, v in fix.items() if k in ADDRESS_FIELDS and v is not None}
        return fix

    except (json.JSONDecodeError, KeyError, IndexError) as e:
        print(f"[suggest] Could not parse Claude response: {e}")
        return {}
    except Exception as e:
        err = str(e)
        if "credit balance is too low" in err or "insufficient_quota" in err:
            # Disable Claude suggestions for the rest of this session
            os.environ["ANTHROPIC_API_KEY"] = ""
            print("[suggest] Anthropic account has no credits — suggestions disabled for this session.\n"
                  "          Add credits at console.anthropic.com → Plans & Billing.")
        else:
            print(f"[suggest] Claude API error: {e}")
        return {}
