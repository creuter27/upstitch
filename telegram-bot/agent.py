"""
Claude-based intent parser.

Converts a natural language command into a structured action dict.
"""
import json
import os

import anthropic

import mappings

_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

_SYSTEM_PROMPT_BASE = """You are a warehouse assistant for an e-commerce business.
Your job is to parse user commands into structured JSON.

Use the canonical names from the reference below for manufacturer, category, variant, size, and color.
If the user says a token or synonym (e.g. "fox", "backpack", "big"), map it to the canonical name.

--- PRODUCT REFERENCE ---
{mappings}
--- END REFERENCE ---


Supported actions:
1. update_stock — change inventory level
2. get_stock — query current inventory

Return ONLY valid JSON, no prose. Schema:

For update_stock:
{
  "action": "update_stock",
  "delta": <number, negative means subtract>,
  "new_quantity": <number or null — set absolute value instead of delta>,
  "manufacturer": "<code or null>",
  "filters": {
    "category": "<string or null>",
    "size": "<string or null>",
    "variant": "<string or null>",
    "color": "<string or null>"
  }
}

For get_stock:
{
  "action": "get_stock",
  "manufacturer": "<code or null>",
  "filters": {
    "category": "<string or null>",
    "size": "<string or null>",
    "variant": "<string or null>",
    "color": "<string or null>"
  }
}

If the command is unclear or not inventory-related, return:
{
  "action": "unknown",
  "message": "<brief explanation of what was unclear>"
}

Rules:
- delta and new_quantity are mutually exclusive; use the appropriate one.
- Set unknown filter fields to null (not empty string).
- Always output canonical names (from the reference) for category, variant, size, color, and manufacturer.
- A name like "Fox", "Bear", "Tiger" maps to variant.
"""

SYSTEM_PROMPT = _SYSTEM_PROMPT_BASE.replace("{mappings}", mappings.prompt_snippet())


def parse_command(text: str) -> dict:
    """Parse a natural language command into a structured action dict."""
    response = _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    block = response.content[0] if response.content else None
    raw = (block.text if hasattr(block, "text") else "").strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"action": "unknown", "message": f"Could not parse LLM response: {raw}"}
