"""
Evaluate email conditions defined in rules.yaml.
"""


def matches_conditions(conditions: dict, msg_meta: dict) -> bool:
    """
    Return True if msg_meta satisfies all conditions in the rule.

    Supported condition keys:
      from_contains           str   — sender address must contain this substring (case-insensitive)
      subject_or_body_contains list[str] — at least one keyword must appear in subject or body
      has_attachments         bool  — email must have at least 1 attachment
      min_attachments         int   — email must have at least N attachments

    msg_meta keys expected:
      from_addr, subject, body_text, attachment_count
    """
    # from_contains
    if fc := conditions.get("from_contains"):
        if fc.lower() not in msg_meta.get("from_addr", "").lower():
            return False

    # subject_or_body_contains — any keyword is sufficient
    if keywords := conditions.get("subject_or_body_contains"):
        combined = (
            msg_meta.get("subject", "") + " " + msg_meta.get("body_text", "")
        ).lower()
        if not any(kw.lower() in combined for kw in keywords):
            return False

    # has_attachments
    if conditions.get("has_attachments"):
        if msg_meta.get("attachment_count", 0) < 1:
            return False

    # min_attachments (more specific than has_attachments)
    if min_att := conditions.get("min_attachments"):
        if msg_meta.get("attachment_count", 0) < min_att:
            return False

    return True
