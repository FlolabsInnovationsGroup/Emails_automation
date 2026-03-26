#!/usr/bin/env python3
"""
Prospect Emails Reply Automation
Entry point — run with `python main.py [options]`
"""

from __future__ import annotations

import argparse
import os
import sys

from src.config import Config
from src.discord_notifier import notify_unmatched_question
from src.email_reader import EmailReader
from src.email_sender import EmailSender, send_queued_emails
from src.fillout_parser import is_fillout_notification, extract_lead, extract_qa_pairs, detect_form_type
from src.funnel_labels import make_funnel_labels
from src import ignore_list as ignore_list_mod
from src.llm_classifier import classify_stage
from src.llm_processor import LLMProcessor
from src.notion_kb import NotionKB
from src.logger import setup_logger

# Only process emails addressed to (or CC-ing) these HR addresses.
# IMPORTANT: this must include every address that forwards into the
# polled Gmail inbox (hr.flomad@gmail.com), otherwise _all_ emails
# will be skipped. See CLAUDE.md → "_is_processable() filter".
# ── Per-form low-effort reply templates ──────────────────────────────────────
_LOW_EFFORT_REPLY_FORM1 = """\
Thank you for submitting the Websites & Presentations form — we appreciate you taking the time!

After reviewing your answers, we noticed that some responses were quite brief and didn't reflect the level of genuine engagement we were hoping for. John Hazlewood personally reviews every submission before deciding who moves forward, so it's important that your answers really show that you explored our projects and websites.

Could you please resubmit the form with more thoughtful, detailed answers? We'd love to hear your genuine impressions of our projects, websites, and the AI Travel Agent — what stood out to you, what you'd improve, and how you see yourself contributing.

You can resubmit using the same link as before. We look forward to reading your responses!\
"""

_LOW_EFFORT_REPLY_FORM2 = """\
Thank you for submitting the Meeting Recordings form — we appreciate your interest in FloLabs!

After reviewing your answers, we noticed that some responses were quite short and didn't demonstrate that you had watched and engaged with the recordings in depth. John Hazlewood personally reviews every submission, and he's looking for candidates who show genuine curiosity and attention to detail.

Could you please resubmit the form with more comprehensive answers? Specifically, we'd love to see your real thoughts on the projects and discussions in the meetings — what you found interesting, what could be improved, and how you'd contribute.

You can resubmit using the same link as before. We look forward to your more detailed responses!\
"""

_LOW_EFFORT_REPLY_FORM3 = """\
Thank you for completing the General Masterclass form — we're glad you've reached this stage!

After reviewing your answers, we noticed that some responses didn't quite reflect the depth of engagement we were hoping for. At this stage of the process, John Hazlewood reviews each submission carefully before scheduling a one-on-one with Oliver, so it's important that your answers show genuine reflection.

Could you please resubmit the form with more thoughtful, detailed responses that reflect your real impressions and insights?

You can resubmit using the same link as before. We look forward to reading your full responses!\
"""

# ── Per-form replies for high-effort submissions ──────────────────────────────
# These mirror the manual workflow templates described in the SOP.

_GOOD_REPLY_FORM1 = """\
Thank you for your thoughtful responses to the Websites & Presentations form — it's great to see your genuine engagement with our projects!

The next step is for you to watch our meeting recordings and answer a few questions about them. Please complete the form at the link below:

https://build.fillout.com/t/wqyLXfXzWGus

John Hazlewood, our Founder and CEO, will personally review your responses. We look forward to hearing your thoughts!\
"""

_GOOD_REPLY_FORM2 = """\
Thank you for completing the Meeting Recordings form — your responses show real engagement with our work, and we appreciate it!

We'd like to invite you to our next Orientation Session, where John Hazlewood, our Founder and CEO, will present FloLabs' vision and current projects in detail.

You can join using the link below — the session takes place every Saturday:
https://us06web.zoom.us/j/82809015394?pwd=eN4AWg4nT3DXZqPPE3OwqDQmjrgGQ2.1

We look forward to seeing you there!\
"""

ORIENTATION_ZOOM_LINK = "https://us06web.zoom.us/j/82809015394?pwd=eN4AWg4nT3DXZqPPE3OwqDQmjrgGQ2.1"

# CC list for all emails from the orientation invite onwards.
# Oliver and John must be copied on every email at this stage and beyond.
_CC_POST_ORIENTATION: list[str] = [
    "oliver.abreu@flolabsrd.com",
    "john@flolabsrd.com",
]

# Stages at which Oliver + John should be CC'd on all outgoing replies.
# This includes the orientation invitation itself and everything after.
from src.funnel_labels import (
    STAGE_SATURDAY_MEETING, STAGE_ATTENDED, STAGE_BRIEFLY_ATTENDED,
    STAGE_NOT_ATTENDED, STAGE_SECOND_MEETING, STAGE_AGREEMENT_SENT,
    STAGE_SIGNED_PAID, STAGE_SIGNED_NON_PAID,
)
_CC_STAGES: set[str] = {
    STAGE_SATURDAY_MEETING, STAGE_ATTENDED, STAGE_BRIEFLY_ATTENDED,
    STAGE_NOT_ATTENDED, STAGE_SECOND_MEETING, STAGE_AGREEMENT_SENT,
    STAGE_SIGNED_PAID, STAGE_SIGNED_NON_PAID,
}

_GOOD_REPLY_FORM3 = """\
Thank you so much for completing the General Masterclass form — your responses show real depth and commitment!

John Hazlewood has reviewed your submission and we'd like to move you forward to the next step: a one-on-one meeting with Oliver Abreu, our Head of R&D, who is CC'd on this email.

Please reply with 2–3 time slots when you're available in the coming week so we can schedule your call with Oliver. We're excited about the potential of working with you!\
"""

_HR_ADDRESSES: set[str] = {
    "hr@flolabsinnovations.com",
    "hr@flolabsrd.com",
    "hr.flomad@gmail.com",      # the actual Gmail inbox being polled
    "hr@flomadlabs.com",        # Fillout/Zoom login address
    "hr@flomad.com",            # HubSpot login address — Fillout notifications go here
}

# Domains / addresses that should never receive an automated reply.
_SKIP_DOMAINS: set[str] = {
    "hubspot.com", "linkedin.com", "notifications.hubspot.com",
    "mail.hellosign.com", "hellosign.com",  # DropboxSign agreement notifications
    "zoom.us",                              # Zoom meeting confirmations
    "calendar.google.com",                  # Google Calendar invitations
    "google.com",                           # Google Calendar notifications
    "calendly.com",                         # Calendly scheduling notifications
}
# Internal FloLabs domains — emails from these are John, Oliver, or other staff
# forwarded back into the inbox. Never auto-reply to them.
_SKIP_INTERNAL_DOMAINS: set[str] = {"flolabsrd.com", "flolabsinnovations.com", "flomadlabs.com", "flomad.com"}

# Specific external addresses that should never receive an automated reply.
# Oliver Abreu (Head of R&D) uses this personal Gmail alongside his internal
# addresses — he is the same person as Oliver Belliard Abreu / Oliver Belliard.
_SKIP_ADDRESSES: set[str] = {
    "oliver.belliardabreu@gmail.com",
}


def _from_address(config: Config, form_type: str | None) -> str | None:
    """Return the correct from-address for this form type.

    Form 2 (Invitation Saturday) and Form 3 (Introduce Oliver) must come from
    hr@flolabsrd.com per the workflow doc. Everything else uses the primary
    configured address (hr@flolabsinnovations.com).

    Returns None to let EmailSender use its default (config.email_address).
    """
    if form_type in ("form_2_recordings", "form_3_masterclass"):
        return config.from_email_rd or None
    return None


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

    # Skip specific blocked addresses (e.g. Oliver Abreu's personal Gmail)
    if sender in _SKIP_ADDRESSES:
        return False

    # Skip known platform / bot senders
    domain = sender.split("@")[-1] if "@" in sender else ""
    if domain in _SKIP_DOMAINS:
        return False

    # Never auto-reply to internal FloLabs staff addresses
    if domain in _SKIP_INTERNAL_DOMAINS:
        return False

    # Skip Google Calendar / Calendly invitations by subject pattern
    subject_lower = (incoming.subject or "").lower()
    if subject_lower.startswith("invitation:") or subject_lower.startswith("accepted:") \
       or subject_lower.startswith("declined:") or subject_lower.startswith("updated invitation:") \
       or subject_lower.startswith("canceled event:") or subject_lower.startswith("cancelled:"):
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
    notion = NotionKB(config) if config.notion_kb_enabled and config.notion_api_token else None

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
        # ── Pre-collect emails and deduplicate per thread ──────────────
        # Multiple unread emails in the same thread should only trigger a
        # reply to the LATEST one. Earlier messages are marked as read and
        # skipped to avoid replying mid-thread.
        all_emails: list = []
        for incoming in reader.fetch_unread(
            ignore_message_ids=ignore_ids if dry_run else None,
            override_limit=fetch_limit,
        ):
            all_emails.append(incoming)

        # Group by normalised subject (thread key).
        # Within each group, keep only the email with the highest UID
        # (newest). Mark the rest as "shadowed" — they'll be marked read
        # but not replied to.
        thread_latest: dict[str, object] = {}   # norm_subject → incoming
        shadowed_uids: set[str] = set()

        def _thread_key(subj: str) -> str:
            """Normalise subject to a thread key (strip Re:/Fwd: prefixes)."""
            import re as _re
            return _re.sub(r"^(?:re|fwd?)\s*:\s*", "", (subj or "").strip(), flags=_re.IGNORECASE).lower()

        for incoming in all_emails:
            key = _thread_key(incoming.subject)
            # Also include sender in the key so different people with
            # the same subject don't collide
            sender_key = incoming.sender_address.strip().lower()
            thread_id = f"{sender_key}::{key}"

            existing = thread_latest.get(thread_id)
            if existing is None:
                thread_latest[thread_id] = incoming
            else:
                # Higher UID = newer email
                if int(incoming.uid) > int(existing.uid):
                    shadowed_uids.add(existing.uid)
                    thread_latest[thread_id] = incoming
                else:
                    shadowed_uids.add(incoming.uid)

        if shadowed_uids:
            log.info(
                "Thread dedup: %d emails shadowed by newer messages in same thread.",
                len(shadowed_uids),
            )

        for incoming in all_emails:
            # Stop once we've hit the reply target (dry-run only)
            if dry_run and reply_target and replies_sent >= reply_target:
                break

            examined += 1

            # Track this message in the ignore list regardless of outcome
            if dry_run and incoming.message_id:
                new_ignore_ids.add(incoming.message_id)

            # Skip shadowed emails (older messages in a thread with newer ones)
            if incoming.uid in shadowed_uids:
                log.info(
                    "Skipping UID %s (shadowed by newer email in thread) | From: %s | Subject: %s",
                    incoming.uid, incoming.sender_address, incoming.subject,
                )
                if not dry_run and config.mark_as_read:
                    reader.mark_as_read(incoming.uid)
                continue

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

            # ── Fillout notification: rewrite sender to the actual lead ──────────
            # Fillout emails arrive FROM notifications@fillout.com, but the real
            # lead's name and email are embedded in the form body.  Patch the
            # IncomingEmail so salutation, To:, and LLM context are all correct.
            is_fillout = is_fillout_notification(incoming.sender_address, incoming.subject)
            if is_fillout:
                lead_name, lead_email = extract_lead(incoming.body_text)
                if lead_email:
                    log.info(
                        "Fillout notification UID %s — re-addressing to lead: %s <%s>",
                        incoming.uid, lead_name or "(unknown name)", lead_email,
                    )
                    incoming.sender_address = lead_email
                    if lead_name:
                        incoming.sender = lead_name
                else:
                    log.warning(
                        "Fillout notification UID %s — could not extract lead email; "
                        "skipping to avoid replying to Fillout.",
                        incoming.uid,
                    )
                    continue

            log.info(
                "Processing email UID %s | From: %s | Subject: %s",
                incoming.uid,
                incoming.sender_address,
                incoming.subject,
            )
            try:
                form_type: str | None = None   # set below if is_fillout
                effort: dict = {}              # set below if is_fillout
                # ── Fillout submissions: effort check + workflow-driven reply ─
                if is_fillout:
                    form_type = detect_form_type(incoming.subject, incoming.body_text)
                    qa_pairs = extract_qa_pairs(incoming.body_text)
                    effort = llm.evaluate_effort(qa_pairs)

                    rd_from = _from_address(config, form_type)

                    if effort.get("is_low_effort"):
                        log.info(
                            "UID %s [%s] flagged as low-effort (%.0f%% confidence) — %s",
                            incoming.uid, form_type,
                            effort.get("confidence", 0) * 100,
                            effort.get("reason", ""),
                        )
                        if form_type == "form_2_recordings":
                            reply_body = _LOW_EFFORT_REPLY_FORM2
                        elif form_type == "form_3_masterclass":
                            reply_body = _LOW_EFFORT_REPLY_FORM3
                        else:
                            reply_body = _LOW_EFFORT_REPLY_FORM1
                        # Low-effort: use same from_addr as the high-effort path for consistency.
                        # CC Oliver + John on Form 2 and Form 3 low-effort replies
                        # (these are at/past the orientation stage).
                        cc_low = _CC_POST_ORIENTATION if form_type in ("form_2_recordings", "form_3_masterclass") else None
                        sender.send_reply(incoming, reply_body, dry_run=dry_run,
                                          cc=cc_low, from_addr=rd_from)

                    else:
                        log.info(
                            "UID %s [%s] high-effort — sending workflow reply.",
                            incoming.uid, form_type,
                        )
                        if form_type == "form_3_masterclass":
                            reply_body = _GOOD_REPLY_FORM3
                            sender.send_reply(
                                incoming, reply_body, dry_run=dry_run,
                                cc=_CC_POST_ORIENTATION, from_addr=rd_from,
                            )
                        elif form_type == "form_2_recordings":
                            reply_body = _GOOD_REPLY_FORM2
                            sender.send_reply(
                                incoming, reply_body, dry_run=dry_run,
                                cc=_CC_POST_ORIENTATION, from_addr=rd_from,
                            )
                        else:
                            reply_body = _GOOD_REPLY_FORM1
                            sender.send_reply(incoming, reply_body, dry_run=dry_run)

                else:
                    # ── Non-Fillout email: try Notion KB match first ─────
                    template_name, template_body = (None, None)
                    if notion:
                        template_name, template_body = notion.find_matching_template(
                            incoming.subject, incoming.body_text or "", config
                        )

                    if template_body:
                        log.info("Matched Notion template %r for UID %s", template_name, incoming.uid)
                        reply_body = llm.generate_reply(incoming, template_context=template_body)
                    else:
                        reply_body = llm.generate_reply(incoming)
                        # Notify Discord about unmatched question
                        if config.discord_webhook_url:
                            notify_unmatched_question(
                                sender=f"{incoming.sender} <{incoming.sender_address}>",
                                subject=incoming.subject,
                                body_summary=(incoming.body_text or "")[:500],
                                webhook_url=config.discord_webhook_url,
                            )

                    # LLM may return NO_REPLY for emails that should be left
                    # for a human (e.g. meeting confirmations, availability slots).
                    if reply_body.strip().upper() == "NO_REPLY":
                        log.info(
                            "LLM returned NO_REPLY for UID %s — skipping (left for human).",
                            incoming.uid,
                        )
                        if not dry_run and config.mark_as_read:
                            reader.mark_as_read(incoming.uid)
                        continue

                    # Classify stage BEFORE sending so we can decide on CC.
                    # For non-Fillout emails the LLM classifies the stage.
                    stage = classify_stage(incoming, reply_body, config, form_type=None)
                    log.info("Pre-send stage: %s for UID %s", stage, incoming.uid)

                    # CC Oliver + John on orientation invite and all post-orientation emails
                    cc_list = _CC_POST_ORIENTATION if stage in _CC_STAGES else None
                    from_addr = config.from_email_rd if stage in _CC_STAGES else None

                    sender.send_reply(incoming, reply_body, dry_run=dry_run,
                                      cc=cc_list, from_addr=from_addr)

                # Classify funnel stage for Gmail labelling.
                # High-effort Fillout: advance to the next stage deterministically.
                # Low-effort Fillout: stay at Screening (asked to resubmit).
                # Non-Fillout: already classified above, reuse the result.
                if is_fillout and not effort.get("is_low_effort"):
                    _form_type_for_stage = form_type
                else:
                    _form_type_for_stage = None

                if is_fillout:
                    stage = classify_stage(incoming, reply_body, config, form_type=_form_type_for_stage)
                log.info("Stage: %s for UID %s", stage, incoming.uid)

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
        # In Lambda context, re-raise so the handler can detect delivery errors
        # and trigger the delivery monitor (throttle + Discord alert).
        if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
            raise
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
