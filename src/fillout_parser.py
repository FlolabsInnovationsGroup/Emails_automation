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

# Fixed fields and boilerplate we skip when evaluating effort.
_SKIP_LABELS_RE = re.compile(
    r"^(first\s+name|last\s+name|e[\s-]?mail(?:\s+address)?|"
    r"view\s+submission|below\s+is\s+a\s+copy|you\s+received|responses)$",
    re.IGNORECASE,
)
# Answers that are always Yes/No confirmations — not real content.
_BOILERPLATE_ANSWER_RE = re.compile(r"^(yes|no)$", re.IGNORECASE)


def is_fillout_notification(sender_address: str, subject: str) -> bool:
    """Return True if this email is a Fillout form submission notification."""
    return (
        sender_address.lower().strip() in FILLOUT_SENDERS
        or bool(_FILLOUT_SUBJECT_RE.search(subject))
    )


def detect_form_type(subject: str, body_text: str) -> str:
    """Detect which Fillout form was submitted.

    Returns one of:
      "form_1_websites"    — Websites & Presentations
      "form_2_recordings"  — Meeting Recordings
      "form_3_masterclass" — General Masterclass
      "unknown"
    """
    subject_lower = subject.lower()
    if "masterclass" in subject_lower or "general masterclass" in subject_lower:
        return "form_3_masterclass"
    if "meeting recording" in subject_lower:
        return "form_2_recordings"
    if "website" in subject_lower or "presentation" in subject_lower:
        return "form_1_websites"

    # Fall back to body scan (form title appears in the first few lines)
    body_head = body_text[:500].lower()
    if "masterclass" in body_head:
        return "form_3_masterclass"
    if "meeting recording" in body_head:
        return "form_2_recordings"
    if "website" in body_head or "presentation" in body_head:
        return "form_1_websites"

    return "unknown"


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


def extract_qa_pairs(body_text: str) -> list[tuple[str, str]]:
    """Extract open-ended question/answer pairs from a Fillout notification body.

    Returns a list of (question, answer) tuples, skipping fixed identity fields
    (First name, Last name, Email) and Yes/No confirmation answers.
    """
    # Find the RESPONSES: block
    responses_match = re.search(r"RESPONSES:\s*\n", body_text, re.IGNORECASE)
    start = responses_match.end() if responses_match else 0
    content = body_text[start:]

    # Split into non-empty lines, collapsing multiple blank lines
    lines = [ln.strip() for ln in content.splitlines()]

    pairs: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        # Skip blank lines
        if not lines[i]:
            i += 1
            continue

        label = lines[i]
        i += 1

        # Collect the value: skip blank lines, then grab until next blank line
        while i < len(lines) and not lines[i]:
            i += 1

        value_parts: list[str] = []
        while i < len(lines) and lines[i]:
            value_parts.append(lines[i])
            i += 1
        value = " ".join(value_parts).strip()

        # Skip fixed/boilerplate fields
        if _SKIP_LABELS_RE.match(label):
            continue
        if _BOILERPLATE_ANSWER_RE.match(value):
            continue
        # Skip if answer is empty or just whitespace
        if not value:
            continue

        pairs.append((label, value))

    return pairs


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
