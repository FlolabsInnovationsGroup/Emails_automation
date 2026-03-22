"""Gmail funnel label manager — creates and applies Prospect/* stage labels.

Stages match the actual FloLabs HR recruitment funnel as defined in FUNNEL.md.
The Gmail label names here must exactly match what is (or should be) in the
hr.flomad@gmail.com inbox.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("email_automation.funnel_labels")

# ── Funnel stage constants — must match FUNNEL.md exactly ───────────────────

STAGE_NEW                       = "Prospect/01-New Applicant"
STAGE_REPLIED                   = "Prospect/02-Replied"
STAGE_INTERESTED                = "Prospect/03-Interested"      # confirmed interest, Form 1 sent
STAGE_SCREENING                 = "Prospect/03-Screening"       # Form 1 submitted, reviewing
STAGE_BEFORE_MEETING            = "Prospect/04-Before Meeting"  # Form 1 approved, Form 2 sent
STAGE_SATURDAY_MEETING          = "Prospect/05-Saturday Meeting" # Form 2 approved, invited to orientation
STAGE_ATTENDED                  = "Prospect/06-Attended"        # attended orientation
STAGE_BRIEFLY_ATTENDED          = "Prospect/06-Briefly Attended"
STAGE_NOT_ATTENDED              = "Prospect/06-Not Attended"
STAGE_SECOND_MEETING            = "Prospect/07-Second Meeting"  # Form 3 approved, intro to Oliver
STAGE_AGREEMENT_SENT            = "Prospect/08-Agreement Sent"
STAGE_SIGNED_PAID               = "Prospect/09-Signed Paid"
STAGE_SIGNED_NON_PAID           = "Prospect/09-Signed Non-Paid"
STAGE_NOT_INTERESTED            = "Prospect/XX-Not Interested"
STAGE_NO_ANSWER                 = "Prospect/XX-No Answer"
STAGE_REJECTED                  = "Prospect/XX-Rejected"

ALL_STAGES: list[str] = [
    STAGE_NEW,
    STAGE_REPLIED,
    STAGE_INTERESTED,
    STAGE_SCREENING,
    STAGE_BEFORE_MEETING,
    STAGE_SATURDAY_MEETING,
    STAGE_ATTENDED,
    STAGE_BRIEFLY_ATTENDED,
    STAGE_NOT_ATTENDED,
    STAGE_SECOND_MEETING,
    STAGE_AGREEMENT_SENT,
    STAGE_SIGNED_PAID,
    STAGE_SIGNED_NON_PAID,
    STAGE_NOT_INTERESTED,
    STAGE_NO_ANSWER,
    STAGE_REJECTED,
]

# Map from form type → the stage to apply after a high-effort submission
FORM_STAGE_MAP: dict[str, str] = {
    "form_1_websites":   STAGE_BEFORE_MEETING,
    "form_2_recordings": STAGE_SATURDAY_MEETING,
    "form_3_masterclass": STAGE_SECOND_MEETING,
}

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.labels",
                "https://www.googleapis.com/auth/gmail.modify"]


# ── Auth helpers ─────────────────────────────────────────────────────────────

def _get_credentials(credentials_file: str, token_file: str):
    """Return valid OAuth2 credentials, running the browser flow if needed."""
    from google.oauth2.credentials import Credentials  # type: ignore
    from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
    from google.auth.transport.requests import Request  # type: ignore

    creds = None
    token_path = Path(token_file)
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_file, GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return creds


def _build_service(credentials_file: str, token_file: str):
    """Build and return an authenticated Gmail API service."""
    from googleapiclient.discovery import build  # type: ignore
    creds = _get_credentials(credentials_file, token_file)
    return build("gmail", "v1", credentials=creds)


# ── Label management ──────────────────────────────────────────────────────────

def _list_labels(service) -> dict[str, str]:
    """Return {label_name: label_id} for all labels in the mailbox."""
    result = service.users().labels().list(userId="me").execute()
    return {lbl["name"]: lbl["id"] for lbl in result.get("labels", [])}


def _create_label(service, name: str) -> str:
    """Create a Gmail label and return its ID."""
    body = {
        "name": name,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
    }
    result = service.users().labels().create(userId="me", body=body).execute()
    logger.info("Created Gmail label: %s (id=%s)", name, result["id"])
    return result["id"]


def _ensure_labels(service) -> dict[str, str]:
    """Ensure all funnel stage labels exist; return {name: id} mapping."""
    existing = _list_labels(service)
    label_ids: dict[str, str] = {}
    for stage in ALL_STAGES:
        if stage in existing:
            label_ids[stage] = existing[stage]
        else:
            label_ids[stage] = _create_label(service, stage)
    return label_ids


# ── Public API ────────────────────────────────────────────────────────────────

class FunnelLabels:
    """Applies Gmail funnel labels to email threads."""

    def __init__(self, credentials_file: str, token_file: str) -> None:
        self._credentials_file = credentials_file
        self._token_file = token_file
        self._service = None
        self._label_ids: dict[str, str] = {}
        self._enabled = False
        self._init()

    def _init(self) -> None:
        if not Path(self._credentials_file).exists():
            logger.warning(
                "Gmail credentials file not found: %s — funnel labels disabled.",
                self._credentials_file,
            )
            return
        try:
            self._service = _build_service(self._credentials_file, self._token_file)
            self._label_ids = _ensure_labels(self._service)
            self._enabled = True
            logger.info("Gmail funnel labels initialised (%d stages).", len(self._label_ids))
        except Exception as exc:
            logger.warning("Failed to initialise Gmail labels: %s — labels disabled.", exc)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def apply_stage(self, thread_id: str, stage: str) -> None:
        """Apply *stage* label to *thread_id*, removing any other Prospect/* labels."""
        if not self._enabled:
            return
        if stage not in self._label_ids:
            logger.warning("Unknown stage %r — skipping label.", stage)
            return

        try:
            thread = self._service.users().threads().get(
                userId="me", id=thread_id, format="minimal"
            ).execute()
        except Exception as exc:
            logger.warning("Could not fetch thread %s: %s", thread_id, exc)
            return

        # Remove existing Prospect/* labels, add the new one
        existing_ids: list[str] = []
        prospect_ids = set(self._label_ids.values())
        for msg in thread.get("messages", []):
            for lid in msg.get("labelIds", []):
                if lid in prospect_ids and lid != self._label_ids[stage]:
                    existing_ids.append(lid)

        body: dict = {
            "addLabelIds": [self._label_ids[stage]],
            "removeLabelIds": list(set(existing_ids)),
        }

        try:
            self._service.users().threads().modify(
                userId="me", id=thread_id, body=body
            ).execute()
            logger.info("Applied label %r to thread %s.", stage, thread_id)
        except Exception as exc:
            logger.warning("Failed to apply label to thread %s: %s", thread_id, exc)


# ── Module-level convenience ──────────────────────────────────────────────────

def make_funnel_labels(credentials_file: str = "", token_file: str = "") -> FunnelLabels:
    """Construct a FunnelLabels instance using env vars as fallback paths."""
    cred = credentials_file or os.getenv("GMAIL_CREDENTIALS_FILE", "credentials.json")
    tok  = token_file      or os.getenv("GMAIL_TOKEN_FILE",        "token.json")
    return FunnelLabels(cred, tok)
