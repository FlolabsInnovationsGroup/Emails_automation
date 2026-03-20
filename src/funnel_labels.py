"""Gmail funnel label manager — creates and applies Prospect/* stage labels."""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("email_automation.funnel_labels")

# ── Funnel stage constants (ordered) ────────────────────────────────────────

STAGE_NEW_APPLICANT        = "Prospect/01-New Applicant"
STAGE_REPLIED              = "Prospect/02-Replied"
STAGE_SCREENING            = "Prospect/03-Screening"
STAGE_INTERVIEW_SCHEDULED  = "Prospect/04-Interview Scheduled"
STAGE_INTERVIEW_DONE       = "Prospect/05-Interview Done"
STAGE_OFFER_SENT           = "Prospect/06-Offer Sent"
STAGE_HIRED                = "Prospect/07-Hired"
STAGE_REJECTED             = "Prospect/08-Rejected"

ALL_STAGES: list[str] = [
    STAGE_NEW_APPLICANT,
    STAGE_REPLIED,
    STAGE_SCREENING,
    STAGE_INTERVIEW_SCHEDULED,
    STAGE_INTERVIEW_DONE,
    STAGE_OFFER_SENT,
    STAGE_HIRED,
    STAGE_REJECTED,
]

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

        # Determine which Prospect/* labels are currently on the thread
        try:
            thread = self._service.users().threads().get(
                userId="me", id=thread_id, format="minimal"
            ).execute()
        except Exception as exc:
            logger.warning("Could not fetch thread %s: %s", thread_id, exc)
            return

        # Collect existing Prospect/* label IDs to remove
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
