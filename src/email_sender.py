"""Email sender — dispatches replies via SMTP or writes them to the outbox/ queue."""

from __future__ import annotations

import json
import logging
import re
import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from .config import Config
from .email_reader import IncomingEmail

logger = logging.getLogger("email_automation.sender")

OUTBOX_DIR = Path("outbox")


_SIGN_OFF_RE = re.compile(
    r"\n+(?:best regards|warm regards|kind regards|sincerely|thanks|thank you|"
    r"cheers|yours(?: truly| sincerely)?|with regards)"
    r"[^\n]*(?:\n.*)*$",
    re.IGNORECASE,
)


def _strip_sign_off(body: str) -> str:
    """Remove any sign-off the LLM may have appended despite instructions."""
    return _SIGN_OFF_RE.sub("", body).rstrip()


def _build_mime(
    config: Config,
    incoming: IncomingEmail,
    reply_body: str,
) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")

    sender_display = config.sender_name or config.email_address
    msg["From"] = f"{sender_display} <{config.email_address}>"
    msg["To"] = incoming.sender_address
    msg["Subject"] = (
        incoming.subject
        if incoming.subject.lower().startswith("re:")
        else f"Re: {incoming.subject}"
    )
    if incoming.message_id:
        msg["In-Reply-To"] = incoming.message_id
        msg["References"] = incoming.message_id

    first_name = incoming.sender.split()[0] if incoming.sender else ""
    salutation = f"Hi {first_name}," if first_name else "Hi,"
    clean_body = _strip_sign_off(reply_body)
    sign_off = f"\n\nBest regards,\n{sender_display}"
    full_body = f"{salutation}\n\n{clean_body}{sign_off}"

    msg.attach(MIMEText(full_body, "plain", "utf-8"))
    return msg


class EmailSender:
    """Sends (or queues) reply emails."""

    def __init__(self, config: Config) -> None:
        self.config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_reply(
        self,
        incoming: IncomingEmail,
        reply_body: str,
        dry_run: bool = False,
    ) -> None:
        """Send or queue a reply. In dry_run mode, only logs preview."""
        mime = _build_mime(self.config, incoming, reply_body)

        if dry_run:
            self._preview(incoming, mime)
            return

        if self.config.reply_mode == "queue":
            self._queue(incoming, mime, reply_body)
        else:
            self._send_smtp(incoming, mime)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _preview(self, incoming: IncomingEmail, mime: MIMEMultipart) -> None:
        separator = "─" * 60
        logger.info(
            "\n%s\n[DRY RUN] Reply preview for UID %s\nTo: %s\nSubject: %s\n\n%s\n%s",
            separator,
            incoming.uid,
            mime["To"],
            mime["Subject"],
            mime.get_payload(0).get_payload(decode=True).decode("utf-8", errors="replace"),  # type: ignore[union-attr]
            separator,
        )

    def _queue(self, incoming: IncomingEmail, mime: MIMEMultipart, reply_body: str) -> None:
        OUTBOX_DIR.mkdir(exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        stem = f"{ts}_{incoming.uid}"

        # Save the human-readable email file
        eml_path = OUTBOX_DIR / f"{stem}.eml"
        eml_path.write_bytes(mime.as_bytes())

        # Save a metadata sidecar for easy scripting
        meta = {
            "uid": incoming.uid,
            "message_id": incoming.message_id,
            "to": incoming.sender_address,
            "subject": mime["Subject"],
            "reply_body": reply_body,
            "queued_at": datetime.utcnow().isoformat() + "Z",
        }
        (OUTBOX_DIR / f"{stem}.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("Queued reply for UID %s → %s", incoming.uid, eml_path)

    def _send_smtp(self, incoming: IncomingEmail, mime: MIMEMultipart) -> None:
        cfg = self.config
        last_exc: Exception | None = None

        for attempt in range(1, cfg.retry_attempts + 1):
            try:
                if cfg.smtp_use_tls:
                    server = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30)
                    server.ehlo()
                    server.starttls()
                    server.ehlo()
                else:
                    server = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=30)

                with server:
                    server.login(cfg.email_address, cfg.email_password)
                    server.sendmail(
                        cfg.email_address,
                        [incoming.sender_address],
                        mime.as_bytes(),
                    )

                logger.info(
                    "Reply sent to %s (subject: %s).",
                    incoming.sender_address,
                    mime["Subject"],
                )
                return

            except Exception as exc:
                last_exc = exc
                logger.warning("SMTP attempt %d/%d failed: %s", attempt, cfg.retry_attempts, exc)
                if attempt < cfg.retry_attempts:
                    time.sleep(cfg.retry_delay_seconds)

        raise RuntimeError(f"SMTP failed after {cfg.retry_attempts} attempts.") from last_exc


def send_queued_emails(config: Config) -> None:
    """Scan outbox/ and send all queued .eml files via SMTP.

    Each sent file is moved to outbox/sent/.
    """
    if not OUTBOX_DIR.exists():
        logger.info("Outbox is empty — nothing to send.")
        return

    eml_files = list(OUTBOX_DIR.glob("*.eml"))
    if not eml_files:
        logger.info("No queued emails found.")
        return

    sent_dir = OUTBOX_DIR / "sent"
    sent_dir.mkdir(exist_ok=True)
    sender = EmailSender(config)

    for eml_path in sorted(eml_files):
        import email as email_mod
        raw = eml_path.read_bytes()
        mime = email_mod.message_from_bytes(raw)

        # Reconstruct a minimal IncomingEmail just to hold metadata
        meta_path = eml_path.with_suffix(".json")
        meta: dict = {}
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))

        fake_incoming = IncomingEmail(
            uid=meta.get("uid", eml_path.stem),
            message_id=meta.get("message_id", ""),
            subject=meta.get("subject", ""),
            sender="",
            sender_address=meta.get("to", mime.get("To", "")),
            recipients=[],
            cc=[],
            body_text="",
            body_html="",
            date="",
        )

        try:
            from email.mime.multipart import MIMEMultipart as _MMP
            # Re-use the already-built MIME message directly via SMTP
            cfg = config
            if cfg.smtp_use_tls:
                server = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30)
                server.ehlo()
                server.starttls()
                server.ehlo()
            else:
                server = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=30)

            with server:
                server.login(cfg.email_address, cfg.email_password)
                server.sendmail(cfg.email_address, [fake_incoming.sender_address], raw)

            logger.info("Sent queued email: %s", eml_path.name)
            eml_path.rename(sent_dir / eml_path.name)
            if meta_path.exists():
                meta_path.rename(sent_dir / meta_path.name)

        except Exception as exc:
            logger.error("Failed to send %s: %s", eml_path.name, exc)
