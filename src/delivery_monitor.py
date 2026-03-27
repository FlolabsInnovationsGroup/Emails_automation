"""Delivery health monitor — alerts Discord and throttles batch size on failures.

When the email automation fails due to a delivery problem (IMAP/SMTP auth,
connection errors, etc.), this module:

1. Sends a Discord embed describing the failure
2. Halves the MAX_EMAILS batch size (backoff: e.g. 10 → 5 → 2 → 1 min)
   while keeping the EventBridge hourly schedule unchanged
3. Persists the backoff state to S3

When the automation succeeds, it resets the batch size to the original value.

The hourly schedule is NEVER changed — only the number of emails processed
per run is reduced. This prevents a growing backlog of unprocessed emails.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import boto3
import requests

logger = logging.getLogger("email_automation.delivery_monitor")

# ── Constants ────────────────────────────────────────────────────────────────
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "")
S3_PREFIX = os.environ.get("S3_PREFIX", "email-automation/")
S3_STATE_KEY = S3_PREFIX + "delivery_state.json"

EVENTBRIDGE_RULE_NAME = "flolabs-email-automation-hourly"
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

# Batch-size throttling: on errors, halve MAX_EMAILS instead of slowing the schedule.
# The base value is read from the Lambda env var MAX_EMAILS (default 10).
BASE_MAX_EMAILS = int(os.environ.get("MAX_EMAILS", "10"))
MIN_MAX_EMAILS = 1  # floor — always process at least 1 email per run


# ── Delivery failure classification ─────────────────────────────────────────

# Error strings that indicate a delivery/auth problem (worth throttling for)
_DELIVERY_ERROR_PATTERNS = [
    "AUTHENTICATIONFAILED",
    "Invalid credentials",
    "Authentication failed",
    "SMTP failed after",
    "Connection refused",
    "Connection timed out",
    "Token has been expired or revoked",
    "invalid_grant",
    "Login failed",
    "socket error",
    "WinError 10054",
    "EOF occurred",
    "Connection unexpectedly closed",
]


def is_delivery_error(error_msg: str) -> bool:
    """Return True if the error message indicates a delivery/auth problem."""
    error_lower = error_msg.lower()
    return any(pattern.lower() in error_lower for pattern in _DELIVERY_ERROR_PATTERNS)


# ── State management (S3) ───────────────────────────────────────────────────

def _load_state(s3_client) -> dict:
    """Load the delivery state from S3. Returns default state if not found."""
    default = {
        "consecutive_failures": 0,
        "current_max_emails": BASE_MAX_EMAILS,
        "last_failure": None,
        "last_success": None,
        "last_error": None,
    }
    if not S3_BUCKET:
        return default

    try:
        resp = s3_client.get_object(Bucket=S3_BUCKET, Key=S3_STATE_KEY)
        return json.loads(resp["Body"].read().decode("utf-8"))
    except s3_client.exceptions.NoSuchKey:
        return default
    except Exception as exc:
        logger.warning("Could not load delivery state from S3: %s", exc)
        return default


def _save_state(s3_client, state: dict) -> None:
    """Persist the delivery state to S3."""
    if not S3_BUCKET:
        return
    try:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=S3_STATE_KEY,
            Body=json.dumps(state, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
    except Exception as exc:
        logger.warning("Could not save delivery state to S3: %s", exc)


# ── Batch-size throttle (replaces old EventBridge schedule management) ──────

def get_throttled_max_emails() -> int:
    """Return the current throttled MAX_EMAILS value from S3 state.

    Call this at the start of each Lambda run to determine how many emails
    to process. Returns BASE_MAX_EMAILS if no throttle is active.
    """
    try:
        s3 = boto3.client("s3", region_name=AWS_REGION)
        state = _load_state(s3)
        return state.get("current_max_emails", BASE_MAX_EMAILS)
    except Exception as exc:
        logger.warning("Could not read throttle state: %s — using default %d", exc, BASE_MAX_EMAILS)
        return BASE_MAX_EMAILS


# ── Discord alerts ───────────────────────────────────────────────────────────

def _send_discord_alert(
    error_msg: str,
    consecutive_failures: int,
    new_max_emails: int,
) -> None:
    """Send a Discord embed alerting about the delivery failure."""
    webhook_url = DISCORD_WEBHOOK_URL
    if not webhook_url:
        logger.warning("No DISCORD_WEBHOOK_URL set — cannot send delivery alert.")
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Truncate error for embed field limits
    error_preview = (error_msg or "Unknown error")[:1000]

    fields = [
        {"name": "Error", "value": f"```\n{error_preview}\n```", "inline": False},
        {"name": "Consecutive Failures", "value": str(consecutive_failures), "inline": True},
        {"name": "Batch Size", "value": f"{new_max_emails} emails/run", "inline": True},
        {"name": "Timestamp", "value": now, "inline": True},
    ]

    if new_max_emails < BASE_MAX_EMAILS:
        fields.append({
            "name": "⚠️ Throttled",
            "value": (
                f"Batch size reduced from {BASE_MAX_EMAILS} to **{new_max_emails}** emails per run "
                f"to reduce load while errors persist. Schedule stays at every 1h. "
                f"Will restore to {BASE_MAX_EMAILS} when the issue is fixed."
            ),
            "inline": False,
        })

    payload = {
        "embeds": [
            {
                "title": "🚨 Email Automation — Delivery Failure",
                "color": 0xE74C3C,  # red
                "description": (
                    "The FloLabs email automation failed to send/receive emails. "
                    "This is likely an IMAP/SMTP authentication or connectivity issue."
                ),
                "fields": fields,
                "footer": {
                    "text": (
                        "Fix: regenerate the Gmail App Password, update it in the "
                        "Lambda env vars, and redeploy. Batch size will auto-restore on success."
                    ),
                },
            }
        ],
    }

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            logger.info("Discord delivery alert sent (failures: %d).", consecutive_failures)
        else:
            logger.warning("Discord webhook returned %d: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("Discord delivery alert failed: %s", exc)


def _send_discord_recovery() -> None:
    """Send a Discord embed announcing recovery."""
    webhook_url = DISCORD_WEBHOOK_URL
    if not webhook_url:
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    payload = {
        "embeds": [
            {
                "title": "✅ Email Automation — Recovered",
                "color": 0x2ECC71,  # green
                "description": (
                    "The FloLabs email automation is working again. "
                    f"Batch size restored to **{BASE_MAX_EMAILS} emails/run**."
                ),
                "fields": [
                    {"name": "Timestamp", "value": now, "inline": True},
                ],
            }
        ],
    }

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            logger.info("Discord recovery notification sent.")
        else:
            logger.warning("Discord webhook returned %d: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("Discord recovery notification failed: %s", exc)


# ── Public API ───────────────────────────────────────────────────────────────

def report_failure(error_msg: str) -> None:
    """Call this when the automation fails with a delivery error.

    - Increments the failure counter
    - Halves the MAX_EMAILS batch size (down to MIN_MAX_EMAILS)
    - EventBridge schedule stays at 1h (never changed)
    - Sends a Discord alert
    - Persists state to S3
    """
    if not is_delivery_error(error_msg):
        logger.info("Error is not a delivery problem — skipping throttle: %s", error_msg[:200])
        return

    s3 = boto3.client("s3", region_name=AWS_REGION)
    state = _load_state(s3)

    state["consecutive_failures"] += 1
    state["last_failure"] = datetime.now(timezone.utc).isoformat()
    state["last_error"] = error_msg[:2000]

    # Halve the batch size (backoff), floored at MIN_MAX_EMAILS
    old_max = state.get("current_max_emails", BASE_MAX_EMAILS)
    new_max = max(old_max // 2, MIN_MAX_EMAILS)
    state["current_max_emails"] = new_max

    # Alert Discord
    _send_discord_alert(error_msg, state["consecutive_failures"], new_max)

    # Persist
    _save_state(s3, state)

    logger.info(
        "Delivery failure #%d — batch size changed from %d to %d emails/run.",
        state["consecutive_failures"], old_max, new_max,
    )


def report_success() -> None:
    """Call this when the automation succeeds.

    - Resets the failure counter
    - Restores MAX_EMAILS to the base value if it was throttled
    - Sends a recovery Discord notification if recovering from failures
    """
    s3 = boto3.client("s3", region_name=AWS_REGION)
    state = _load_state(s3)

    was_throttled = state["consecutive_failures"] > 0

    state["consecutive_failures"] = 0
    state["last_success"] = datetime.now(timezone.utc).isoformat()

    if was_throttled:
        # Restore original batch size
        state["current_max_emails"] = BASE_MAX_EMAILS
        _send_discord_recovery()
        logger.info("Recovered from delivery failures — batch size restored to %d.", BASE_MAX_EMAILS)

    _save_state(s3, state)
