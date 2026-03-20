#!/usr/bin/env python3
"""
Prospect Emails Reply Automation
Entry point — run with `python main.py [options]`
"""

from __future__ import annotations

import argparse
import sys

from src.config import Config
from src.email_reader import EmailReader
from src.email_sender import EmailSender, send_queued_emails
from src.funnel_labels import make_funnel_labels
from src import ignore_list as ignore_list_mod
from src.llm_classifier import classify_stage
from src.llm_processor import LLMProcessor
from src.logger import setup_logger

# Only process emails addressed to (or CC-ing) these HR addresses.
_HR_ADDRESSES: set[str] = {
    "hr@flolabsinnovations.com",
    "hr@flolabsrd.com",
}

# Domains / addresses that should never receive an automated reply.
_SKIP_DOMAINS: set[str] = {"hubspot.com"}
_SKIP_ADDRESSES: set[str] = set()  # add specific sender addresses here if needed


def _is_processable(incoming) -> bool:
    """Return True if this email should be processed and replied to.

    Conditions:
    - Sent TO or CC-ing one of the HR addresses
    - NOT from ourselves or a known platform/bot
    """
    from src.email_reader import IncomingEmail as _IE  # local import to avoid circular

    all_recipients = {a.strip().lower() for a in (incoming.recipients + incoming.cc)}

    # Must be addressed to or CC-ing at least one HR address
    if not all_recipients & _HR_ADDRESSES:
        return False

    # Never reply to ourselves
    sender = incoming.sender_address.strip().lower()
    if sender in _HR_ADDRESSES:
        return False

    # Skip known platform / bot senders
    domain = sender.split("@")[-1] if "@" in sender else ""
    if domain in _SKIP_DOMAINS:
        return False
    if sender in _SKIP_ADDRESSES:
        return False

    return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automatically reply to prospect emails using an LLM.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                          # Process unread emails (respects REPLY_MODE)
  python main.py --dry-run                # Preview replies until MAX_EMAILS replies reached
  python main.py --dry-run --reply-target 10  # Keep going until 10 replies would be sent
  python main.py --send-queued            # Send emails queued in outbox/
  python main.py --dry-run --limit 5     # Preview up to 5 emails examined (old behaviour)

Dry-run ignore list:
  Every email examined in dry-run mode is saved to dry_run_ignore.json.
  Re-runs automatically skip those messages. Delete the file to start fresh.
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate and preview replies without sending or queuing them.",
    )
    parser.add_argument(
        "--send-queued",
        action="store_true",
        help="Send all emails that were previously queued in outbox/.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Override MAX_EMAILS from .env for this run.",
    )
    parser.add_argument(
        "--reply-target",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Dry-run only: keep processing emails until N of them would "
            "receive a reply (default: value of MAX_EMAILS in .env). "
            "Every examined email is added to dry_run_ignore.json so "
            "re-runs never re-examine the same messages."
        ),
    )
    parser.add_argument(
        "--mailbox",
        type=str,
        default=None,
        metavar="FOLDER",
        help="Override IMAP_MAILBOX from .env for this run.",
    )
    return parser.parse_args(argv)


def process_emails(config: Config, dry_run: bool, reply_target: int | None = None) -> None:
    """Process unread emails and send (or preview) replies.

    In dry-run mode with a ``reply_target``, the function keeps pulling emails
    from the inbox until that many would have received a reply — ignoring any
    emails already listed in ``dry_run_ignore.json``.  Every examined message
    (whether it gets a reply or not) is added to the ignore list so future
    runs never waste time on the same messages.
    """
    log = __import__("logging").getLogger("email_automation")
    llm = LLMProcessor(config)
    sender = EmailSender(config)
    funnel = make_funnel_labels(config.gmail_credentials_file, config.gmail_token_file) \
        if config.gmail_labels_enabled else None

    # --- Dry-run ignore-list bookkeeping ------------------------------------
    ignore_ids: set[str] = set()
    new_ignore_ids: set[str] = set()   # message IDs seen in *this* run
    if dry_run:
        ignore_ids = ignore_list_mod.load()
        log.info("Ignore list: %d previously examined message IDs.", len(ignore_ids))

    # In dry-run mode with a reply_target we need to scan the whole inbox
    # (without the normal cap) and count replies ourselves.
    # In normal (non-dry-run) mode, honour config.max_emails as usual.
    fetch_limit = 0 if (dry_run and reply_target) else None  # 0 = unlimited for dry-run

    replies_sent = 0
    examined = 0
    errors = 0

    if dry_run and reply_target:
        log.info("Dry-run reply target: %d replies.", reply_target)

    with EmailReader(config) as reader:
        for incoming in reader.fetch_unread(
            ignore_message_ids=ignore_ids if dry_run else None,
            override_limit=fetch_limit,
        ):
            # Stop once we've hit the reply target (dry-run only)
            if dry_run and reply_target and replies_sent >= reply_target:
                break

            examined += 1

            # Track this message in the ignore list regardless of outcome
            if dry_run and incoming.message_id:
                new_ignore_ids.add(incoming.message_id)

            # Skip emails not addressed to an HR address, or from bots/ourselves
            if not _is_processable(incoming):
                log.info(
                    "Skipping UID %s | From: %s | To: %s | CC: %s",
                    incoming.uid,
                    incoming.sender_address,
                    incoming.recipients,
                    incoming.cc,
                )
                continue

            log.info(
                "Processing email UID %s | From: %s | Subject: %s",
                incoming.uid,
                incoming.sender_address,
                incoming.subject,
            )
            try:
                reply_body = llm.generate_reply(incoming)

                # Classify funnel stage before sending
                stage = classify_stage(incoming, reply_body, config)
                log.info("Stage: %s for UID %s", stage, incoming.uid)

                sender.send_reply(incoming, reply_body, dry_run=dry_run)

                # Apply Gmail label (non-dry-run only)
                if not dry_run and funnel and funnel.enabled and incoming.message_id:
                    thread_id = incoming.message_id.strip("<>").split("@")[0]
                    funnel.apply_stage(thread_id, stage)

                if not dry_run and config.mark_as_read:
                    reader.mark_as_read(incoming.uid)

                replies_sent += 1

            except Exception as exc:
                log.error("Failed to process UID %s: %s", incoming.uid, exc)
                errors += 1

    # Save updated ignore list after the run
    if dry_run and new_ignore_ids:
        ignore_list_mod.save(new_ignore_ids)

    log.info(
        "Done. Examined: %d | Replies: %d | Errors: %d",
        examined, replies_sent, errors,
    )
    if dry_run and reply_target and replies_sent < reply_target:
        log.warning(
            "Inbox exhausted — only %d/%d replyable emails found.",
            replies_sent, reply_target,
        )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = Config()

    if args.limit is not None:
        config.max_emails = args.limit
    if args.mailbox is not None:
        config.imap_mailbox = args.mailbox

    logger = setup_logger(config.log_level, config.log_file)

    try:
        config.validate()
    except ValueError as exc:
        logger.error("Configuration error: %s", exc)
        return 1

    if args.send_queued:
        logger.info("Sending queued emails from outbox/ …")
        send_queued_emails(config)
        return 0

    # Resolve the reply target for dry-run mode:
    # --reply-target N  → explicit target
    # --dry-run alone   → fall back to MAX_EMAILS (same as before)
    reply_target: int | None = None
    if args.dry_run:
        reply_target = args.reply_target if args.reply_target is not None else config.max_emails

    mode = "DRY RUN" if args.dry_run else config.reply_mode.upper()
    if args.dry_run and reply_target:
        mode = f"DRY RUN (target: {reply_target} replies)"
    logger.info("Starting email automation (mode: %s) …", mode)

    try:
        process_emails(config, dry_run=args.dry_run, reply_target=reply_target)
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
        return 130
    except Exception as exc:
        logger.error("Unhandled error: %s", exc, exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
