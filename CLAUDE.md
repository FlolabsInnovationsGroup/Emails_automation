# CLAUDE.md — Persistent Memory for AI Sessions

This file is the single source of truth for any AI assistant working on this
project. Read it at the start of every session. Update it when anything
significant changes.

---

## Project Purpose

Automated email reply bot for **FloLabs / Flomad** HR recruitment.
Reads unread emails from `hr.flomad@gmail.com` via IMAP, classifies intent
with an LLM (currently OpenAI GPT-4o), generates replies using the FloLabs
email templates, and either sends them immediately (`REPLY_MODE=auto`) or
queues them in `outbox/` for human review (`REPLY_MODE=queue`).

---

## Key People

| Name | Role | Email |
|---|---|---|
| Oliver Abreu | Co-founder / PM | oliver.abreu@flolabsrd.com |
| John Hazlewood | CEO | john@flolabsrd.com |
| Paula Ferry | HR (persona used in templates) | paula.ferry.flolabs@gmail.com |

---

## Email Accounts in Use

| Account | Purpose |
|---|---|
| `hr.flomad@gmail.com` | **Primary inbox — IMAP polled by this bot** |
| `hr@flolabsinnovations.com` | Outbound sending address for most stages |
| `hr@flolabsrd.com` | Outbound for post-orientation (with CC to Oliver + John) |
| `paula.ferry.flolabs@gmail.com` | Last-resort reminder sender ("Paula Gmail") |

### ⚠️ Critical: `_is_processable()` filter in `main.py`

`_HR_ADDRESSES` controls which emails the bot will reply to. It MUST include
**all addresses that applicants send mail to** — i.e. all addresses that
forward into `hr.flomad@gmail.com`. Currently set to:

```python
_HR_ADDRESSES: set[str] = {
    "hr@flolabsinnovations.com",
    "hr@flolabsrd.com",
    "hr.flomad@gmail.com",   # ← the actual Gmail inbox; must be here
}
```

If `hr.flomad@gmail.com` is missing from this set, **every single email will
be skipped** (0 replies). This was the bug that caused the 2026-03-20 run to
examine 2565 emails with 0 replies and add them all to the ignore list.

---

## Recruitment Funnel — Lifecycle Stages

Sourced from the workflow document:
https://docs.google.com/document/d/1XZXUZ6BLW0Xn9Nn38qB58IsjBrlWNvHo0gwAR1BNOvA/

Stages in order:

| # | Stage key | HubSpot label | Trigger |
|---|---|---|---|
| 1 | `new` | New | Application received, first email sent (auto, 3h after form submit) |
| 2 | `reminder_1st` | Reminder 1st if Interested | No reply in 3 days |
| 3 | `reminder_paula` | Reminder from Paula | No reply in 3 more days |
| 4 | `no_answer` | No Answer | Last reminder sent, still no reply |
| 5 | `contacted` | Contacted | Applicant replied interested → Websites form sent (manual) |
| 6 | `reminder_after_contacted` | Reminder after Contacted | No form in 4 days |
| 7 | `second_reminder_contacted` | Second reminder Contacted | No form in 5 more days |
| 8 | `before_meeting` | Before Meeting | Submitted form 1 → Meeting Recordings form sent (manual) |
| 9 | `reminder_before_meeting` | Reminder before Meeting | No form in 4 days |
| 10 | `second_reminder_before_meeting` | 2nd Reminder Before Meeting | No form in 5 more days |
| 11 | `saturday_meeting` | Saturday meeting | Submitted form 2, invited to orientation |
| 12 | `attended` | Attended | Attended full orientation session |
| 12 | `briefly_attended` | Briefly Attended | Attended < 1.5h |
| 12 | `not_attended` | Not attended | Did not attend |
| 13 | `interested` | Interested | Submitted Masterclass form |
| 14 | `second_meeting` | Second Meeting | "Introduce Oliver" email sent |
| 15 | `agreement_sent` | Agreement Sent | Oliver met them; onboarding + agreements sent |
| 16 | `signed_paid` | Signed Paid | Signed agreements + paid |
| 16 | `signed_non_paid` | Signed Non-Paid | Signed but on scholarship |
| — | `not_interested` | Not Interested | Applicant declined |
| — | `done` | Done | Fully onboarded |
| — | `blacklisted` | Blacklisted | Blocked |

Gmail labels follow the pattern `FloLabs/NN - Stage Name` (defined in
`src/funnel_labels.py`). The classifier in `src/llm_classifier.py` uses
`Prospect/NN-Name` labels — these are separate and may need to be unified
in a future refactor.

---

## Known Issues & Fixes Applied

### 1. `_is_processable()` missing `hr.flomad@gmail.com` (FIXED 2026-03-20)
All 2565 emails were skipped because the Gmail inbox address was not in
`_HR_ADDRESSES`. Fix: add it. Also added `hr@flomadlabs.com` which is
referenced in the workflow doc as a Zoom login.

### 2. IMAP socket drops on large inbox scans (FIXED 2026-03-20)
WinError 10054 — Gmail forcibly closes the connection after fetching hundreds
of emails without a keepalive or reconnect. Fix: `email_reader.py` now
reconnects automatically in batches of 200 emails and uses IMAP NOOP to
keep the connection alive.

### 3. Dry-run scanned entire inbox (2565 emails) at once (FIXED 2026-03-20)
`fetch_limit=0` (unlimited) caused the bot to scan and add all 2565 emails
to the ignore list in a single run with no replies found. Fix: in dry-run
mode with a `reply_target`, the fetch is now still capped at a safe batch
size (500) per run — the loop reruns across sessions via the ignore list.

### 4. OpenAI API rate limits not surfaced (TODO)
OpenAI returns rate-limit headers (`x-ratelimit-remaining-requests`,
`x-ratelimit-remaining-tokens`) in API responses. The current OpenAI SDK
does not expose these easily. Workaround: check usage at
https://platform.openai.com/usage — no code solution yet.

---

## Architecture

```
main.py                  CLI entry point
src/
  config.py              All settings from .env
  email_reader.py        IMAP reader (with ignore-list + reconnect support)
  email_sender.py        SMTP sender + outbox/ queue writer
  llm_processor.py       Anthropic / OpenAI reply generator
  llm_classifier.py      Funnel stage classifier (separate LLM call)
  funnel_labels.py       Gmail API label manager (Prospect/* labels)
  ignore_list.py         Persistent dry-run ignore list (dry_run_ignore.json)
  logger.py              Logging setup
credentials.json         Google OAuth2 client secrets (gitignored)
token.json               Google OAuth2 token, auto-refreshed (gitignored)
dry_run_ignore.json      Message-IDs already examined (gitignored, auto-created)
outbox/                  Queued replies (gitignored, auto-created)
```

---

## Running the Bot

```bash
# Activate venv first
.venv\Scripts\activate   # Windows
source .venv/bin/activate  # macOS/Linux

# Dry run — preview replies until 10 would be sent
python main.py --dry-run --reply-target 10

# Normal run — queue replies in outbox/ for review
python main.py

# Send all queued replies
python main.py --send-queued

# Full send immediately (careful)
# Set REPLY_MODE=auto in .env, then:
python main.py
```

---

## Checking OpenAI API Usage

No in-code tracking yet. Check manually:
- **Dashboard**: https://platform.openai.com/usage
- **Rate limits**: https://platform.openai.com/account/limits
- **Model**: currently `gpt-4o` (set via `LLM_PROVIDER=openai` in `.env`)

Tier and rate limits depend on the account's spend history. If you hit limits,
the retry logic in `llm_processor.py` will back off (`RETRY_ATTEMPTS=3`,
`RETRY_DELAY_SECONDS=2.0`).

---

## Gmail IMAP Notes

- Gmail limits IMAP connections: **~15 concurrent connections** per account
- Gmail closes idle connections after **~30 minutes** of inactivity
- Bulk fetching large inboxes (1000+ emails) causes WinError 10054 without
  keepalive/reconnect logic
- IMAP `SEARCH UNSEEN` returns ALL unread emails — if the inbox has thousands
  of old unread messages, the dry-run can be slow until the ignore list catches up
- App Password in use: `oydy mdvv kfhy maxv` (stored in `.env`, gitignored)

---

## Session Checklist

Before starting work each session:
1. Read this file (CLAUDE.md)
2. Check `dry_run_ignore.json` exists and has entries (means dry runs have run)
3. Check `outbox/` for any queued replies pending human review
4. Confirm `_HR_ADDRESSES` in `main.py` includes `hr.flomad@gmail.com`
