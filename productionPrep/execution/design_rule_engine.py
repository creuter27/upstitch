"""
design_rule_engine.py

Evaluates the row-transformation rules defined in
config/design_import_rules.yaml against each CSV data row.

Public API
----------
    load_rules(path)    → list[dict]
    process_rows(...)   → (processed_rows, notes, yellow_cells, red_cells)

Rule schema is documented in config/design_import_rules.yaml.
"""

import re
import unicodedata
from copy import deepcopy
from difflib import get_close_matches
from pathlib import Path
from typing import Any

import yaml

_RULES_FILE = Path(__file__).parent.parent / "config" / "design_import_rules.yaml"

# Colours used for cell highlighting (passed back to the caller as metadata)
YELLOW    = {"red": 1.0,   "green": 1.0, "blue": 0.0}
RED       = {"red": 0.867, "green": 0.0, "blue": 0.0}
WHITE     = {"red": 1.0,   "green": 1.0, "blue": 1.0}


# ---------------------------------------------------------------------------
# Public: load rules
# ---------------------------------------------------------------------------

def load_rules(path: Path = _RULES_FILE) -> list[dict]:
    """Load and return the ordered list of rule definitions from YAML."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    rules = data.get("rules", [])
    if not rules:
        print(f"[warn] No rules found in {path}")
    return rules


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _col_idx(col_name: str, col_map: dict[str, int]) -> int | None:
    return col_map.get(col_name.lower())


def _get_cell(row: list[str], col_name: str, col_map: dict[str, int]) -> str:
    idx = _col_idx(col_name, col_map)
    if idx is None or idx >= len(row):
        return ""
    return row[idx]


def _set_cell(row: list[str], col_name: str, col_map: dict[str, int], value: str) -> None:
    idx = _col_idx(col_name, col_map)
    if idx is None:
        return
    while len(row) <= idx:
        row.append("")
    row[idx] = value


def _re_flags(flags_str: str) -> int:
    result = 0
    for ch in (flags_str or "").lower():
        if ch == "i": result |= re.IGNORECASE
        if ch == "m": result |= re.MULTILINE
        if ch == "s": result |= re.DOTALL
    return result


# ---------------------------------------------------------------------------
# Condition evaluation
# ---------------------------------------------------------------------------

def _eval_condition(cond: Any, row: list[str], col_map: dict[str, int],
                    ctx: dict) -> bool:
    """
    Recursively evaluate one condition definition.

    ctx keys: stitched_categories (set|None), valid_colors (set),
              color_alias_map (dict).
    """
    if cond is None:
        return True
    if not isinstance(cond, dict):
        return False

    # Boolean combinators
    if "all_of" in cond:
        return all(_eval_condition(c, row, col_map, ctx) for c in cond["all_of"])
    if "any_of" in cond:
        return any(_eval_condition(c, row, col_map, ctx) for c in cond["any_of"])
    if "not" in cond:
        return not _eval_condition(cond["not"], row, col_map, ctx)

    t   = cond.get("type", "")
    col = cond.get("column", "")
    ci  = cond.get("case_insensitive", True)
    raw = _nfc(_get_cell(row, col, col_map))
    val = raw.lower() if ci else raw

    if t == "equals":
        targets = [str(v) for v in cond.get("values", [])]
        if ci:
            return raw in targets or val in [v.lower() for v in targets]
        return raw in targets

    if t == "contains":
        targets = [str(v) for v in cond.get("values", [])]
        return any((t_.lower() in val if ci else t_ in raw) for t_ in targets)

    if t == "not_contains":
        targets = [str(v) for v in cond.get("values", [])]
        return not any((t_.lower() in val if ci else t_ in raw) for t_ in targets)

    if t == "matches":
        pattern = cond.get("pattern", "")
        flags   = _re_flags(cond.get("flags", ""))
        return bool(re.search(pattern, raw, flags))

    if t == "is_empty":
        return not raw.strip()

    if t == "not_empty":
        return bool(raw.strip())

    if t == "category_stitched":
        stitched = ctx.get("stitched_categories")
        if stitched is None:
            return True  # load failed → apply rule to all rows (safe fallback)
        cat = _nfc(_get_cell(row, "category", col_map).strip().lower())
        return cat in stitched

    print(f"[warn] Unknown condition type '{t}' — treated as False")
    return False


# ---------------------------------------------------------------------------
# Color resolution (used by the resolve_color action)
# ---------------------------------------------------------------------------

def _resolve_color(value: str, valid_names: set[str],
                   alias_map: dict[str, str]) -> str | None:
    """
    Map raw textColor value to canonical 'Name intern'.
    Returns None if no reasonable match is found.

    Priority: exact canonical → exact alias → fuzzy match.
    """
    if not value.strip():
        return value

    if value in valid_names:
        return value

    lower = value.lower()
    if lower in alias_map:
        return alias_map[lower]

    intern_lower = {n.lower(): n for n in valid_names}
    candidates   = list(alias_map.keys()) + list(intern_lower.keys())
    matches      = get_close_matches(lower, candidates, n=1, cutoff=0.7)
    if matches:
        hit = matches[0]
        if hit in alias_map:
            return alias_map[hit]
        if hit in intern_lower:
            return intern_lower[hit]

    return None


# ---------------------------------------------------------------------------
# Action execution
# ---------------------------------------------------------------------------

def _exec_actions(
    actions:      list[dict],
    rule_id:      str,
    row:          list[str],
    row_idx:      int,
    col_map:      dict[str, int],
    ctx:          dict,
    prev_values:  dict[str, str],
    notes:        list,
    yellow_cells: list,
    red_cells:    list,
) -> None:
    """
    Execute all actions for a single matched rule.

    prev_values: column values captured *before* this rule's actions started.
    changed:     columns actually modified by earlier actions in this rule —
                 used by skip_if_unchanged.
    """
    changed: set[str] = set()

    for action in actions:
        t   = action.get("type", "")
        col = action.get("column", "")

        # ── clear ──────────────────────────────────────────────────────────
        if t == "clear":
            _set_cell(row, col, col_map, "")
            changed.add(col)

        # ── extract ────────────────────────────────────────────────────────
        elif t == "extract":
            from_col    = action.get("from_column", col)
            pattern     = action.get("pattern", "")
            flags       = _re_flags(action.get("flags", ""))
            group       = action.get("group", 0)
            do_strip    = action.get("strip", True)
            case        = action.get("case")
            skip_vals   = [str(v).lower() for v in action.get("skip_values", [])]
            to_col      = action.get("to_column", col)
            fallback    = action.get("to_column_fallback")
            skip_same   = action.get("skip_if_same_case_insensitive", False)
            skip_unch   = action.get("skip_if_unchanged", False)

            if skip_unch and to_col not in changed:
                continue

            source = _nfc(_get_cell(row, from_col, col_map))
            m = re.search(pattern, source, flags)
            if not m:
                continue

            extracted = m.group(group)
            if do_strip:
                extracted = extracted.strip()
            if case == "upper":
                extracted = extracted.upper()
            elif case == "lower":
                extracted = extracted.lower()

            if extracted.lower() in skip_vals:
                continue

            # Decide the actual target column
            actual_col = to_col
            if fallback and _get_cell(row, to_col, col_map).strip():
                actual_col = fallback

            current = _get_cell(row, actual_col, col_map)
            if skip_same and extracted.upper() == current.upper().strip():
                continue

            _set_cell(row, actual_col, col_map, extracted)
            changed.add(actual_col)

        # ── highlight ──────────────────────────────────────────────────────
        elif t == "highlight":
            if action.get("skip_if_unchanged") and col not in changed:
                continue
            col_i = _col_idx(col, col_map)
            if col_i is not None:
                color = action.get("color", "yellow")
                if color == "yellow":
                    yellow_cells.append((row_idx, col_i))
                elif color == "red_white":
                    red_cells.append((row_idx, col_i))

        # ── note ───────────────────────────────────────────────────────────
        elif t == "note":
            if action.get("skip_if_unchanged") and col not in changed:
                continue
            col_i = _col_idx(col, col_map)
            if col_i is None:
                continue
            tmpl = action.get("text", "")
            # {prev_value} → value of this action's column before the rule
            prev = prev_values.get(col, "")
            note_text = tmpl.replace("{prev_value}", prev)
            # {prev:<colname>} for cross-column references
            for c, v in prev_values.items():
                note_text = note_text.replace(f"{{prev:{c}}}", v)
            notes.append((row_idx, col_i, note_text))

        # ── resolve_color ──────────────────────────────────────────────────
        elif t == "resolve_color":
            col_i = _col_idx(col, col_map)
            if col_i is None:
                continue
            value = _get_cell(row, col, col_map)
            if not value.strip():
                continue
            resolved = _resolve_color(
                value,
                ctx.get("valid_colors", set()),
                ctx.get("color_alias_map", {}),
            )
            if resolved is None:
                red_cells.append((row_idx, col_i))
            elif resolved != value:
                _set_cell(row, col, col_map, resolved)
                changed.add(col)

        else:
            print(f"[warn] Rule '{rule_id}': unknown action type '{t}' — skipped")


# ---------------------------------------------------------------------------
# Rule expansion: for_each_column
# ---------------------------------------------------------------------------

def _expand_rule(rule: dict) -> list[dict]:
    """If the rule has for_each_column, return one copy per column."""
    cols = rule.get("for_each_column")
    if not cols:
        return [rule]
    expanded = []
    for col in cols:
        copy = deepcopy(rule)
        copy.pop("for_each_column", None)
        copy = _substitute_placeholder(copy, "{col}", col)
        expanded.append(copy)
    return expanded


def _substitute_placeholder(obj: Any, placeholder: str, value: str) -> Any:
    if isinstance(obj, str):
        return obj.replace(placeholder, value)
    if isinstance(obj, dict):
        return {k: _substitute_placeholder(v, placeholder, value) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute_placeholder(item, placeholder, value) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# Public: process all rows
# ---------------------------------------------------------------------------

def process_rows(
    rows:                list[list[str]],
    col_map:             dict[str, int],
    rules:               list[dict],
    valid_colors:        set[str]  | None = None,
    color_alias_map:     dict[str, str] | None = None,
    stitched_categories: set[str]  | None = None,
) -> tuple[list[list[str]], list[tuple], list[tuple], list[tuple]]:
    """
    Apply all rules to every data row (header row is passed through unchanged).

    Returns
    -------
    processed_rows : list[list[str]]
    notes          : list of (row_0based, col_0based, note_text)
    yellow_cells   : list of (row_0based, col_0based)
    red_cells      : list of (row_0based, col_0based)
    """
    if len(rows) < 2:
        return rows, [], [], []

    ctx = {
        "valid_colors":        valid_colors        or set(),
        "color_alias_map":     color_alias_map     or {},
        "stitched_categories": stitched_categories,
    }

    # Pre-expand for_each_column rules once, not per row
    expanded_rules: list[dict] = []
    for rule in rules:
        expanded_rules.extend(_expand_rule(rule))

    notes:        list[tuple] = []
    yellow_cells: list[tuple] = []
    red_cells:    list[tuple] = []
    processed = [list(rows[0])]  # header passes through unchanged

    max_col = max(col_map.values(), default=0)

    for row_idx, raw in enumerate(rows[1:], start=1):
        row = list(raw)
        while len(row) <= max_col:
            row.append("")

        for rule in expanded_rules:
            rule_id  = rule.get("id", "?")
            cond     = rule.get("condition")
            actions  = rule.get("actions", [])

            # Capture column values before this rule runs (for {prev_value} in notes)
            prev_values: dict[str, str] = {}
            for action in actions:
                for key in ("column", "to_column", "to_column_fallback", "from_column"):
                    c = action.get(key)
                    if c and c not in prev_values:
                        prev_values[c] = _get_cell(row, c, col_map)

            if _eval_condition(cond, row, col_map, ctx):
                _exec_actions(
                    actions, rule_id, row, row_idx,
                    col_map, ctx, prev_values,
                    notes, yellow_cells, red_cells,
                )

        processed.append(row)

    return processed, notes, yellow_cells, red_cells
