"""
Fillout notification email parser.

Fillout sends notification emails to the HR inbox whenever a form is submitted.
The email arrives FROM notifications@fillout.com, but the actual lead's name and
email are embedded in the body as form field answers.

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
_FILLOUT_SUBJECT_RE = re.compile(
    r"new submission for your",
    re.IGNORECASE,
)

# ── Field-label patterns ──────────────────────────────────────────────────────
# Fillout notification bodies typically look like:
#
#   Full Name: Jane Doe
#   Email: jane@example.com
#   ...
#
# or with a separator line before each field.  We try broad patterns first and
# fall back to narrower ones.

_EMAIL_PATTERNS: list[re.Pattern] = [
    # "Email: user@example.com"  (label on same line)
    re.compile(r"(?:e[\s-]?mail|email address)\s*[:\-]\s*([\w.+\-]+@[\w.\-]+\.\w+)", re.IGNORECASE),
    # Bare email anywhere in the body (last resort)
    re.compile(r"\b([\w.+\-]+@(?!fillout\.com|hubspot\.com|notifications\.)[\w.\-]+\.\w+)\b"),
]

_NAME_PATTERNS: list[re.Pattern] = [
    # "Full Name: Jane Doe" / "Name: Jane Doe" / "First Name: Jane"
    re.compile(r"(?:full\s+name|first\s+name|your\s+name|name)\s*[:\-]\s*([A-Za-z][A-Za-z '\-\.]{1,60})", re.IGNORECASE),
]


def is_fillout_notification(sender_address: str, subject: str) -> bool:
    """Return True if this email is a Fillout form submission notification."""
    return (
        sender_address.lower().strip() in FILLOUT_SENDERS
        or bool(_FILLOUT_SUBJECT_RE.search(subject))
    )


def extract_lead(body_text: str) -> tuple[str, str] | tuple[None, None]:
    """Extract (name, email) of the actual lead from a Fillout notification body.

    Returns (None, None) if extraction fails.
    """
    email = _extract_email(body_text)
    name = _extract_name(body_text)
    if not email:
        return None, None
    return name, email


def _extract_email(body: str) -> str | None:
    for pattern in _EMAIL_PATTERNS:
        match = pattern.search(body)
        if match:
            candidate = match.group(1).lower().strip()
            # Sanity check: skip obviously invalid or platform addresses
            if "@" in candidate and "." in candidate.split("@")[-1]:
                return candidate
    return None


def _extract_name(body: str) -> str | None:
    for pattern in _NAME_PATTERNS:
        match = pattern.search(body)
        if match:
            raw = match.group(1).strip().rstrip(".,;")
            # Reject if it looks like a URL, sentence, or is too short
            if len(raw) >= 2 and " " not in raw or len(raw.split()) <= 5:
                return raw
    return None
