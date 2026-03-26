"""Delivery health monitor — alerts Discord and throttles EventBridge on failures.

When the email automation fails due to a delivery problem (IMAP/SMTP auth,
connection errors, etc.), this module:

1. Sends a Discord embed describing the failure
2. Doubles the EventBridge interval (backoff: 1h → 2h → 4h → 8h … up to 24h)
3. Persists the backoff state to S3

When the automation succeeds, it resets the interval back to 1 hour.
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

BASE_INTERVAL_HOURS = 1
MAX_INTERVAL_HOURS = 24  # cap backoff at 24h


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
        "current_interval_hours": BASE_INTERVAL_HOURS,
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


# ── EventBridge schedule management ─────────────────────────────────────────

def _update_eventbridge_rate(interval_hours: int) -> bool:
    """Update the EventBridge rule schedule to `rate(N hours)`.

    Returns True if the update succeeded.
    """
    try:
        client = boto3.client("events", region_name=AWS_REGION)
        # EventBridge requires "hour" (singular) for rate(1 hour)
        unit = "hour" if interval_hours == 1 else "hours"
        new_schedule = f"rate({interval_hours} {unit})"

        client.put_rule(
            Name=EVENTBRIDGE_RULE_NAME,
            ScheduleExpression=new_schedule,
            State="ENABLED",
            Description=f"Trigger flolabs-email-automation every {interval_hours} {unit}",
        )
        logger.info("EventBridge schedule updated to: %s", new_schedule)
        return True
    except Exception as exc:
        logger.error("Failed to update EventBridge schedule: %s", exc)
        return False


# ── Discord alerts ───────────────────────────────────────────────────────────

def _send_discord_alert(
    error_msg: str,
    consecutive_failures: int,
    new_interval_hours: int,
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
        {"name": "New Interval", "value": f"Every {new_interval_hours}h", "inline": True},
        {"name": "Timestamp", "value": now, "inline": True},
    ]

    if new_interval_hours > BASE_INTERVAL_HOURS:
        fields.append({
            "name": "⚠️ Throttled",
            "value": (
                f"Frequency reduced from 1h to **{new_interval_hours}h** to avoid "
                f"hammering broken credentials. Will restore to 1h when the issue is fixed."
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
                        "Lambda env vars, and redeploy. The schedule will auto-restore on success."
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
                    "Schedule has been restored to **every 1 hour**."
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
    - Doubles the EventBridge interval (up to MAX_INTERVAL_HOURS)
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

    # Double the interval (backoff), capped at MAX_INTERVAL_HOURS
    new_interval = min(
        state["current_interval_hours"] * 2,
        MAX_INTERVAL_HOURS,
    )
    # On first failure, go from 1h to 2h
    if state["current_interval_hours"] < 2:
        new_interval = 2

    old_interval = state["current_interval_hours"]
    state["current_interval_hours"] = new_interval

    # Update EventBridge schedule
    _update_eventbridge_rate(new_interval)

    # Alert Discord
    _send_discord_alert(error_msg, state["consecutive_failures"], new_interval)

    # Persist
    _save_state(s3, state)

    logger.info(
        "Delivery failure #%d — interval changed from %dh to %dh.",
        state["consecutive_failures"], old_interval, new_interval,
    )


def report_success() -> None:
    """Call this when the automation succeeds.

    - Resets the failure counter
    - Restores EventBridge to the base interval (1h) if it was throttled
    - Sends a recovery Discord notification if recovering from failures
    """
    s3 = boto3.client("s3", region_name=AWS_REGION)
    state = _load_state(s3)

    was_throttled = state["consecutive_failures"] > 0

    state["consecutive_failures"] = 0
    state["last_success"] = datetime.now(timezone.utc).isoformat()

    if was_throttled:
        # Restore original schedule
        state["current_interval_hours"] = BASE_INTERVAL_HOURS
        _update_eventbridge_rate(BASE_INTERVAL_HOURS)
        _send_discord_recovery()
        logger.info("Recovered from delivery failures — schedule restored to 1h.")

    _save_state(s3, state)
