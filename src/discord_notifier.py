"""Discord webhook notifier — alerts admins when no template matches an email."""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger("email_automation.discord")


def notify_unmatched_question(
    sender: str,
    subject: str,
    body_summary: str,
    webhook_url: str,
) -> None:
    """POST a Discord embed asking admins to add a response template.

    Fire-and-forget: logs errors but never raises.
    """
    if not webhook_url:
        return

    body_summary = (body_summary or "").strip()[:500] or "(empty)"

    payload = {
        "embeds": [
            {
                "title": "📬 Unmatched Email — Template Needed",
                "color": 0xFF9800,  # orange
                "fields": [
                    {"name": "From", "value": sender[:256], "inline": False},
                    {"name": "Subject", "value": subject[:256] or "(no subject)", "inline": False},
                    {"name": "Body Preview", "value": body_summary, "inline": False},
                ],
                "footer": {
                    "text": (
                        "No matching Notion template was found for this email. "
                        "Please add an appropriate response template to the Notion KB."
                    )
                },
            }
        ],
    }

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            logger.info("Discord notification sent for unmatched email: %s", subject)
        else:
            logger.warning(
                "Discord webhook returned %d: %s", resp.status_code, resp.text[:200]
            )
    except Exception as exc:
        logger.warning("Discord notification failed: %s", exc)
