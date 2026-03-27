"""Persistent tracker of Message-IDs and threads we've already replied to.

Prevents double-replies across runs by maintaining a set of:
1. Message-IDs we've sent replies to
2. Thread roots we've participated in (so we don't reply to multiple
   emails in the same thread across different runs)

State is persisted to S3 in Lambda or to a local JSON file otherwise.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("email_automation.replied_tracker")

# S3 config (Lambda only)
S3_BUCKET = os.environ.get("S3_BUCKET", "")
S3_PREFIX = os.environ.get("S3_PREFIX", "email-automation/")
S3_KEY = S3_PREFIX + "replied_tracker.json"
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

# Local fallback file (non-Lambda)
LOCAL_FILE = Path("replied_tracker.json")

# Keep entries for 30 days to avoid unbounded growth, but long enough
# to cover any realistic thread lifetime.
RETENTION_SECONDS = 30 * 24 * 3600


class RepliedTracker:
    """Thread-safe tracker of replied Message-IDs and thread roots."""

    def __init__(self) -> None:
        # { message_id: timestamp } — messages we've sent replies to
        self._replied_ids: dict[str, float] = {}
        # { thread_root: timestamp } — thread roots we've participated in
        self._replied_threads: dict[str, float] = {}
        self._dirty = False
        self._load()

    # ── Public API ───────────────────────────────────────────────────────

    def was_replied(self, message_id: str) -> bool:
        """Return True if we've already replied to this exact Message-ID."""
        return message_id.strip() in self._replied_ids

    def thread_was_replied(self, thread_root: str) -> bool:
        """Return True if we've already replied to any email in this thread."""
        return thread_root.strip() in self._replied_threads

    def record_reply(
        self,
        message_id: str,
        thread_root: str | None = None,
    ) -> None:
        """Record that we sent a reply to this message (and thread)."""
        now = time.time()
        mid = message_id.strip()
        if mid:
            self._replied_ids[mid] = now
        if thread_root:
            self._replied_threads[thread_root.strip()] = now
        self._dirty = True

    def save(self) -> None:
        """Persist state (S3 in Lambda, local file otherwise)."""
        if not self._dirty:
            return
        self._prune()
        data = {
            "replied_ids": self._replied_ids,
            "replied_threads": self._replied_threads,
        }
        payload = json.dumps(data, indent=2).encode("utf-8")

        if S3_BUCKET and os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
            self._save_s3(payload)
        else:
            self._save_local(payload)
        self._dirty = False

    @property
    def stats(self) -> dict:
        return {
            "tracked_messages": len(self._replied_ids),
            "tracked_threads": len(self._replied_threads),
        }

    # ── Persistence ──────────────────────────────────────────────────────

    def _load(self) -> None:
        data = {}
        if S3_BUCKET and os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
            data = self._load_s3()
        elif LOCAL_FILE.exists():
            try:
                data = json.loads(LOCAL_FILE.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Could not load local replied tracker: %s", exc)

        self._replied_ids = data.get("replied_ids", {})
        self._replied_threads = data.get("replied_threads", {})
        # Ensure values are floats (timestamps)
        self._replied_ids = {k: float(v) for k, v in self._replied_ids.items()}
        self._replied_threads = {k: float(v) for k, v in self._replied_threads.items()}
        self._prune()
        logger.info(
            "Replied tracker loaded: %d messages, %d threads.",
            len(self._replied_ids), len(self._replied_threads),
        )

    def _prune(self) -> None:
        """Remove entries older than RETENTION_SECONDS."""
        cutoff = time.time() - RETENTION_SECONDS
        self._replied_ids = {k: v for k, v in self._replied_ids.items() if v > cutoff}
        self._replied_threads = {k: v for k, v in self._replied_threads.items() if v > cutoff}

    def _load_s3(self) -> dict:
        try:
            import boto3
            s3 = boto3.client("s3", region_name=AWS_REGION)
            resp = s3.get_object(Bucket=S3_BUCKET, Key=S3_KEY)
            return json.loads(resp["Body"].read().decode("utf-8"))
        except Exception as exc:
            # NoSuchKey on first run is normal
            if "NoSuchKey" not in str(type(exc).__name__):
                logger.warning("Could not load replied tracker from S3: %s", exc)
            return {}

    def _save_s3(self, payload: bytes) -> None:
        try:
            import boto3
            s3 = boto3.client("s3", region_name=AWS_REGION)
            s3.put_object(
                Bucket=S3_BUCKET,
                Key=S3_KEY,
                Body=payload,
                ContentType="application/json",
            )
            logger.info("Replied tracker saved to S3 (%d bytes).", len(payload))
        except Exception as exc:
            logger.warning("Could not save replied tracker to S3: %s", exc)

    def _save_local(self, payload: bytes) -> None:
        try:
            LOCAL_FILE.write_bytes(payload)
            logger.info("Replied tracker saved locally (%d bytes).", len(payload))
        except Exception as exc:
            logger.warning("Could not save local replied tracker: %s", exc)
