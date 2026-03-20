"""IMAP email reader — fetches unread prospect emails from the inbox."""

from __future__ import annotations

import email
import email.utils
import imaplib
import logging
import time
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.message import Message
from typing import Generator

from .config import Config

logger = logging.getLogger("email_automation.reader")


@dataclass
class IncomingEmail:
    uid: str
    message_id: str
    subject: str
    sender: str
    sender_address: str
    recipients: list[str]
    cc: list[str]
    body_text: str
    body_html: str
    date: str
    raw: Message | None = field(default=None, repr=False)


def _decode_str(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return str(make_header(decode_header(value.decode("utf-8", errors="replace"))))
    return str(make_header(decode_header(value)))


def _extract_addresses(raw: str) -> list[str]:
    """Return a list of email addresses extracted from a header value."""
    return [addr for _, addr in email.utils.getaddresses([raw]) if addr]


def _get_body(msg: Message) -> tuple[str, str]:
    """Return (plain_text, html) from a possibly multipart message."""
    plain, html = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in disposition:
                continue
            charset = part.get_content_charset() or "utf-8"
            if ctype == "text/plain" and not plain:
                plain = part.get_payload(decode=True).decode(charset, errors="replace")  # type: ignore[union-attr]
            elif ctype == "text/html" and not html:
                html = part.get_payload(decode=True).decode(charset, errors="replace")  # type: ignore[union-attr]
    else:
        charset = msg.get_content_charset() or "utf-8"
        payload = msg.get_payload(decode=True)
        if payload:
            text = payload.decode(charset, errors="replace")  # type: ignore[union-attr]
            if msg.get_content_type() == "text/html":
                html = text
            else:
                plain = text
    return plain, html


class EmailReader:
    """Connects to an IMAP mailbox and yields unread messages."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._conn: imaplib.IMAP4 | imaplib.IMAP4_SSL | None = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> None:
        cfg = self.config
        logger.info("Connecting to IMAP %s:%d …", cfg.imap_host, cfg.imap_port)
        if cfg.imap_use_ssl:
            self._conn = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port)
        else:
            self._conn = imaplib.IMAP4(cfg.imap_host, cfg.imap_port)
        self._conn.login(cfg.email_address, cfg.email_password)
        logger.info("IMAP login successful.")

    def disconnect(self) -> None:
        if self._conn:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None

    def __enter__(self) -> "EmailReader":
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.disconnect()

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def fetch_unread(
        self,
        ignore_message_ids: set[str] | None = None,
        override_limit: int | None = None,
    ) -> Generator[IncomingEmail, None, None]:
        """Yield unread emails from the configured mailbox.

        Args:
            ignore_message_ids: Set of RFC-2822 Message-IDs to skip. Used by
                the dry-run ignore list so already-seen emails are never
                re-examined across runs.
            override_limit: If provided, caps how many emails are fetched
                regardless of config.max_emails (0 = unlimited).
        """
        conn = self._conn
        if conn is None:
            raise RuntimeError("Not connected. Call connect() first.")

        conn.select(f'"{self.config.imap_mailbox}"')
        _, data = conn.uid("search", None, "UNSEEN")  # type: ignore[call-overload]
        uids: list[bytes] = data[0].split() if data[0] else []
        logger.info("Found %d unread email(s).", len(uids))

        cap = override_limit if override_limit is not None else self.config.max_emails
        limit = cap or len(uids)
        ignore = ignore_message_ids or set()

        for uid in uids[:limit]:
            uid_str = uid.decode()
            try:
                incoming = self._fetch_one(conn, uid_str)
                if incoming.message_id and incoming.message_id in ignore:
                    logger.debug("Skipping UID %s — in ignore list (%s)", uid_str, incoming.message_id)
                    continue
                yield incoming
            except Exception as exc:
                logger.warning("Failed to fetch UID %s: %s", uid_str, exc)

    def _fetch_one(self, conn: imaplib.IMAP4 | imaplib.IMAP4_SSL, uid: str) -> IncomingEmail:
        _, raw_data = conn.uid("fetch", uid, "(RFC822)")  # type: ignore[call-overload]
        raw_bytes: bytes = raw_data[0][1]  # type: ignore[index]
        msg = email.message_from_bytes(raw_bytes)

        subject = _decode_str(msg.get("Subject", ""))
        sender_full = _decode_str(msg.get("From", ""))
        sender_name, sender_addr = email.utils.parseaddr(sender_full)
        recipients = _extract_addresses(msg.get("To", ""))
        cc = _extract_addresses(msg.get("Cc", ""))
        plain, html = _get_body(msg)

        return IncomingEmail(
            uid=uid,
            message_id=msg.get("Message-ID", ""),
            subject=subject,
            sender=sender_name or sender_addr,
            sender_address=sender_addr,
            recipients=recipients,
            cc=cc,
            body_text=plain.strip(),
            body_html=html.strip(),
            date=msg.get("Date", ""),
            raw=msg,
        )

    def mark_as_read(self, uid: str) -> None:
        if self._conn:
            self._conn.uid("store", uid, "+FLAGS", "\\Seen")  # type: ignore[call-overload]

    def _with_retry(self, fn, *args, **kwargs):
        """Call fn with retry logic per config."""
        cfg = self.config
        last_exc: Exception | None = None
        for attempt in range(1, cfg.retry_attempts + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                logger.warning("Attempt %d/%d failed: %s", attempt, cfg.retry_attempts, exc)
                if attempt < cfg.retry_attempts:
                    time.sleep(cfg.retry_delay_seconds)
        raise RuntimeError(f"All {cfg.retry_attempts} attempts failed.") from last_exc
