"""Persistent ignore list for dry-run mode.

Stores RFC-2822 Message-IDs of every email examined during a dry run so that
re-runs never re-process the same messages. This avoids burning memory/tokens
on emails that have already been reviewed.

File location: ``dry_run_ignore.json`` in the project root (next to main.py).
Delete the file to start fresh.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("email_automation.ignore_list")

DEFAULT_PATH = Path("dry_run_ignore.json")


def load(path: Path = DEFAULT_PATH) -> set[str]:
    """Return the set of already-processed Message-IDs from disk."""
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ids = set(data.get("processed_message_ids", []))
        logger.debug("Ignore list loaded: %d entries from %s", len(ids), path)
        return ids
    except Exception as exc:
        logger.warning("Could not read ignore list %s: %s — starting empty.", path, exc)
        return set()


def save(new_ids: set[str], path: Path = DEFAULT_PATH) -> None:
    """Merge *new_ids* into the ignore list file and write it back to disk."""
    existing = load(path)
    merged = existing | new_ids
    payload = {
        "description": (
            "Message-IDs examined by dry-run mode. "
            "Delete this file to reset and reprocess all emails."
        ),
        "count": len(merged),
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "processed_message_ids": sorted(merged),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "Ignore list saved: %d total entries (+%d new) → %s",
        len(merged), len(new_ids - existing), path,
    )
