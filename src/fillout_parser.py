"""
Fillout notification email parser.

Fillout sends notification emails to the HR inbox whenever a form is submitted.
The email arrives FROM notifications@fillout.com, but the actual lead's name and
email are embedded in the body as form field answers.

Fillout body format (label on one line, value on the next):

    First name
    Jane

    Last name
    Doe

    Email
    jane@example.com

This module detects those notification emails and extracts the real lead contact
so the rest of the pipeline (salutation, To: address, LLM context) works
correctly.
"""

from __future__ import annotations

import re

# Sender addresses used by Fillout notification emails.
FILLOUT_SENDERS: set[str] = {
    "notifications@fillout.com",
    "noreply@fillout.com",
}

# Subject prefix that Fillout uses for all form submission notifications.
_FILLOUT_SUBJECT_RE = re.compile(r"new submission for your", re.IGNORECASE)


def is_fillout_notification(sender_address: str, subject: str) -> bool:
    """Return True if this email is a Fillout form submission notification."""
    return (
        sender_address.lower().strip() in FILLOUT_SENDERS
        or bool(_FILLOUT_SUBJECT_RE.search(subject))
    )


def extract_lead(body_text: str) -> tuple[str | None, str | None]:
    """Extract (first_name, email) of the actual lead from a Fillout body.

    Fillout puts the field label on one line and the value on the next line,
    with optional blank lines in between.  We match that pattern specifically.

    Returns (None, None) if extraction fails.
    """
    first_name = _extract_labelled_value(body_text, r"first\s+name")
    email = _extract_labelled_value(body_text, r"e[\s-]?mail(?:\s+address)?")

    # Sanity-check email
    if email and ("@" not in email or "." not in email.split("@")[-1]):
        email = None

    # Reject platform email addresses leaking through
    if email and any(d in email for d in ("fillout.com", "hubspot.com")):
        email = None

    return first_name or None, email or None


def _extract_labelled_value(body: str, label_pattern: str) -> str | None:
    """
    Find a Fillout-style label and return the value on the following non-blank line.

    Pattern:
        <blank lines>
        <label>          ← matched by label_pattern (case-insensitive, full line)
        <blank lines>
        <value>          ← the line we want
    """
    # Match: label on its own line, then skip blank lines, then capture the value line
    pattern = re.compile(
        r"^[ \t]*" + label_pattern + r"[ \t]*$"   # label line
        r"(?:\s*\n)+"                               # one or more blank/whitespace-only lines
        r"[ \t]*([^\n\r]+)",                        # value line (captured)
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(body)
    if match:
        return match.group(1).strip()
    return None
