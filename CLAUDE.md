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

### 5. NO_REPLY emails now left UNREAD (FIXED 2026-03-28)
Previously, when the LLM returned `NO_REPLY` (email should be handled by a
human), the bot still marked the email as read — effectively hiding it from
the inbox. Fix: NO_REPLY emails are now left **unread** so humans can see
and handle them. The message ID is recorded in the replied tracker to prevent
re-processing on the next run.

### 6. Bounce notifications wasting LLM tokens (FIXED 2026-03-28)
`mailer-daemon@googlemail.com` delivery failure notifications were passing
through to the LLM, which couldn't match a Notion template and triggered
Discord "unmatched template" alerts. Fix: `googlemail.com` added to
`_SKIP_DOMAINS`, and a dedicated bounce detection block catches any
`mailer-daemon@*` sender early in the processing loop. Bounces trigger a
red Discord alert (`notify_bounce`) with error details instead of wasting
LLM tokens.

---

## Notion Knowledge Base Integration (added 2026-03-25)

Non-Fillout emails are matched against a **Notion database of response
templates** before the LLM generates a reply. Flow:

1. On startup, `NotionKB` fetches all active templates (checkbox
   `Template_Status` = True) from Notion database `2d3a1576…`.
2. For each non-Fillout email, the LLM is asked to pick the best matching
   template from a compact summary list (template name + first 100 chars).
3. If a template matches, its full body is prepended to the system prompt so
   the LLM personalises the approved template rather than free-forming.
4. If **no** template matches, a normal LLM reply is still generated, and a
   **Discord webhook** notification is sent asking admins to add a template.

### Config keys (`.env`)

| Key | Purpose |
|---|---|
| `NOTION_API_TOKEN` | Notion internal integration token |
| `NOTION_DATABASE_ID` | Notion database ID for templates |
| `NOTION_KB_ENABLED` | `true`/`false` — master switch (default `true`) |
| `DISCORD_WEBHOOK_URL` | Discord webhook for unmatched-question alerts |

### Failsafe

If the Notion API is unreachable or template matching fails, the system
silently falls back to the original LLM-only reply path. Fillout form
handling is completely unaffected — the KB lookup only runs in the `else`
branch.

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
  notion_kb.py           Notion database template matcher
  discord_notifier.py    Discord webhook for unmatched emails
  delivery_monitor.py    Delivery failure alerting + EventBridge throttle
  funnel_labels.py       Gmail API label manager (Prospect/* labels)
  ignore_list.py         Persistent dry-run ignore list (dry_run_ignore.json)
  logger.py              Logging setup
credentials.json         Google OAuth2 client secrets (gitignored)
token.json               Google OAuth2 token, auto-refreshed (gitignored)
dry_run_ignore.json      Message-IDs already examined (gitignored, auto-created)
outbox/                  Queued replies (gitignored, auto-created)
```

---

## AWS Lambda Deployment

The bot runs as `flolabs-email-automation` on AWS Lambda (us-east-1), triggered
hourly by EventBridge.

### Architecture

```
EventBridge (hourly) → Lambda (handler.handler)
                           ↓
                     S3: download token.json + credentials.json to /tmp/
                           ↓
                     Run main.main() with REPLY_MODE=auto
                           ↓
                     S3: upload refreshed token.json
```

### Key Files

| File | Purpose |
|---|---|
| `handler.py` | Lambda entry point — S3 sync + run main |
| `deploy/deploy.ps1` | Full deployment script (create/update) |

### Deploy / Update

```powershell
cd Emails_automation
.\deploy\deploy.ps1
```

### Test Manually

```bash
aws lambda invoke --function-name flolabs-email-automation --region us-east-1 --log-type Tail out.json
```

### Check Logs

```bash
aws logs tail /aws/lambda/flolabs-email-automation --region us-east-1 --since 1h
```

### S3 Bucket

`flolabs-email-automation-975049954782` in us-east-1:
- `email-automation/token.json` — Gmail OAuth token (auto-refreshed)
- `email-automation/credentials.json` — Google OAuth client secrets

### Environment Variables

All `.env` values are set as Lambda env vars, with these overrides:
- `REPLY_MODE=auto` (unattended)
- `GMAIL_TOKEN_FILE=/tmp/token.json`
- `GMAIL_CREDENTIALS_FILE=/tmp/credentials.json`
- `S3_BUCKET=flolabs-email-automation-975049954782`
- `S3_PREFIX=email-automation/`

---

## Running the Bot (Local)

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

## Access Credentials & Services

| Service | Detail |
|---|---|
| **Gmail inbox (IMAP polled)** | `hr.flomad@gmail.com` — App Password: `oydy mdvv kfhy maxv` |
| **Gmail OAuth** | `credentials.json` + `token.json` (for funnel labels) |
| **Notion API Token** | `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` |
| **Notion Database** | ID: `2d3a1576955180d4875cef9df8db1dd7` — "Email templates" (73+ templates) |
| **Discord Webhook** | `https://discord.com/api/webhooks/1486244838109352018/VgNdYeQncgcSvXdUzC2qWyWwm9bpoMQ91JmAVC8g58laY9SyEIvmcEJLnG05ymt2YIHF` |
| **HubSpot login** | `hr@flomad.com` |
| **Fillout login** | `hr@flomadlabs.com` |
| **Zoom account** | `john@flomadlabs.com` |

All secrets are also in `.env` (gitignored).

---

## Skipped Sender Domains (`_SKIP_DOMAINS`)

These domains are blocked in `_is_processable()` and never get replies:

| Domain | Reason |
|---|---|
| `hubspot.com`, `notifications.hubspot.com` | Platform notifications |
| `linkedin.com` | Platform notifications |
| `mail.hellosign.com`, `hellosign.com` | DropboxSign agreement notifications |
| `zoom.us` | Zoom meeting confirmations |
| `calendar.google.com` | Google Calendar invitations |
| `google.com` | Google Calendar notifications |
| `calendly.com` | Calendly scheduling notifications |
| `googlemail.com` | Bounce notifications (mailer-daemon) |

**Blocked addresses (`_SKIP_ADDRESSES`):**

| Address | Reason |
|---|---|
| `oliver.belliardabreu@gmail.com` | Oliver Abreu's personal Gmail (Head of R&D) |

**Subject-based filters:** Emails with subjects starting with `Invitation:`,
`Accepted:`, `Declined:`, `Updated invitation:`, `Canceled event:`, or
`Cancelled:` are skipped (calendar events from any sender).

**⚠️ `fillout.com` must NOT be in `_SKIP_DOMAINS`** — Fillout emails need to pass through
`_is_processable()` to reach the Fillout parser which handles form submissions.

Internal FloLabs domains (`flolabsrd.com`, `flolabsinnovations.com`, `flomadlabs.com`, `flomad.com`)
are separately blocked via `_SKIP_INTERNAL_DOMAINS`.

---

### 2026-03-26: Oliver 1-on-1 missed → Calendly reschedule link
When an applicant at the Second Meeting stage misses their 1-on-1 with Oliver,
they should be sent a reschedule link:
`https://calendly.com/oliver-belliardabreu/new-meeting?guests=hr@flolabsrd.com,john@flolabsrd.com`
The `?guests=` parameter auto-CCs HR and John on the calendar invite.

### 2026-03-26: Gmail App Password expired — IMAP auth failure
The Gmail App Password for `hr.flomad@gmail.com` was revoked/expired between
March 25 and March 26. Every Lambda invocation crashed with
`[AUTHENTICATIONFAILED] Invalid credentials (Failure)`. The OAuth token for
Gmail labels was also expired. No Discord alerts were sent because the crash
happened before any email processing.

Fix required: generate a new App Password in Google Account → Security →
2-Step Verification → App passwords, then update `EMAIL_PASSWORD` in the
Lambda environment variables and redeploy.

Also deployed the delivery monitor (`src/delivery_monitor.py`) to prevent
this from happening silently again. See "Delivery Monitor" section above.

---

## Lessons Learned (Session Log)

### 2026-03-20: `_is_processable()` missing `hr.flomad@gmail.com`
All 2565 emails were skipped because the Gmail inbox address was not in `_HR_ADDRESSES`.
Added it + `hr@flomadlabs.com`. Critical: never remove these.

### 2026-03-25: HelloSign/Zoom notifications getting LLM replies
`noreply@mail.hellosign.com` and `no-reply@zoom.us` were passing through filters and
the LLM was generating confused replies (e.g., talking about agreements before the Oliver
meeting). Fix: added to `_SKIP_DOMAINS`. Lesson: block system notifications at the filter
level — don't rely on LLM guardrails for sender types that should never get replies.

### 2026-03-25: Notion KB integration
Added Notion database as knowledge base. 73 active templates. LLM matches incoming emails
to best template, uses it as context for on-message replies. Unmatched emails trigger
Discord webhook to admin channel. Template-based replies enforce 150-word limit to prevent
verbatim dumps.

### 2026-03-25: Planned AWS deployment
Oliver wants hourly unattended runs. Needs: AWS account details, decision on REPLY_MODE
(auto vs queue), persistent storage for ignore list, transfer of `.env`/`credentials.json`/`token.json`.

### 2026-03-25: CC Oliver + John from orientation onwards (REQUIREMENT)
All emails from the orientation invitation (Stage 13 / Saturday Meeting) onwards
must CC `oliver.abreu@flolabsrd.com` and `john@flolabsrd.com`, and be sent from
`hr@flolabsrd.com`. This applies to **every** email at this stage or later:
- Fillout Form 2 approved → orientation invite (high-effort)
- Fillout Form 2 low-effort → resubmit request (CC'd)
- Fillout Form 3 approved → Oliver 1-on-1 intro (high-effort)
- Fillout Form 3 low-effort → resubmit request (CC'd)
- ALL non-Fillout LLM replies where the classified stage is Saturday Meeting or later
The `_CC_POST_ORIENTATION` constant and `_CC_STAGES` set in `main.py` control this.

### 2026-03-25: Additional sender filters
- Added `calendar.google.com`, `google.com`, `calendly.com` to `_SKIP_DOMAINS`
- Added `oliver.belliardabreu@gmail.com` to new `_SKIP_ADDRESSES` set (Oliver Abreu's
  personal Gmail — same person as Oliver Belliard Abreu / Oliver Belliard)
- Added subject-based filter: skip emails starting with "Invitation:", "Accepted:",
  "Declined:", "Updated invitation:", "Canceled event:", "Cancelled:" (calendar events)
- Lesson: internal domain filter already blocked `@flolabsrd.com` etc., but Oliver also
  sends from a personal Gmail that needed an explicit block.

### 2026-03-25: Email thread preservation (REQUIREMENT)
All outgoing replies MUST include the quoted original message below the reply
to preserve the email thread. The `_build_mime()` function in `email_sender.py`
appends a standard quoted block (`On <date>, <sender> wrote: > ...`) after the
sign-off. The `In-Reply-To` and `References` headers were already set correctly.
**Never remove the quoted message block — it is a hard requirement.**

### 2026-07-23: AWS Lambda deployment built
Deployed as `flolabs-email-automation` on Lambda (us-east-1), matching the existing
`flolabs-*` function naming convention. Key decisions:
- **Zip packaging** (not Docker) — same as all other flolabs functions
- **REPLY_MODE=auto** — unattended, sends replies immediately
- **S3 bucket** `flolabs-email-automation-975049954782` stores `token.json` + `credentials.json`
- **EventBridge** `flolabs-email-automation-hourly` triggers every hour
- **Reuses `flolabs-lambda-role`** with added S3 policy for the token bucket
- Discord webhook fires only when no Notion template matches (existing behaviour)
- `lxml` and `beautifulsoup4` dropped from Lambda package (not imported anywhere)

---

## Delivery Monitor (added 2026-03-26, updated 2026-03-27)

When the Lambda fails due to a delivery/auth problem (IMAP auth failure,
SMTP failure, connection errors, expired tokens), the **delivery monitor**
(`src/delivery_monitor.py`) automatically:

1. **Alerts Discord** — sends a red embed to the webhook with error details,
   failure count, and new batch size.
2. **Reduces batch size** — halves MAX_EMAILS on each consecutive failure
   (e.g. 10 → 5 → 2 → 1 min) while the **hourly schedule stays unchanged**.
   This prevents backlog accumulation — emails keep being processed every hour,
   just fewer per run.
3. **Persists state to S3** — `delivery_state.json` tracks consecutive failures
   and current batch size.
4. **Auto-recovers** — when the automation succeeds again, the batch size resets
   to the original MAX_EMAILS value and a green "Recovered" Discord notification
   is sent.

Only delivery-related errors trigger throttling. Other errors (LLM failures,
Notion API issues) do not affect the batch size.

**Why batch-size throttle instead of schedule throttle:** Increasing the interval
between runs (old approach) caused emails to pile up in the inbox, creating a
growing backlog. Reducing the batch size instead means the bot still runs every
hour and keeps chipping away at the inbox, just processing fewer emails per run
when things are unstable.

### How `main.py` interacts with the monitor

When running in Lambda (`AWS_LAMBDA_FUNCTION_NAME` env var is set), `main.py`
re-raises exceptions instead of catching them — this lets `handler.py` detect
the error type and call the delivery monitor.

`handler.py` calls `get_throttled_max_emails()` before running `main.main()`
to apply the current throttled batch size via the `MAX_EMAILS` env var.

---

## ⚠️ CRITICAL: Full Thread Analysis (added 2026-03-27)

Every email reply MUST be based on analysis of the ENTIRE conversation thread,
not just the latest message. The email body passed to the LLM includes all
quoted previous messages (lines starting with `>`). The system prompt
explicitly instructs the LLM to:

1. Read the full thread top-to-bottom before generating any reply
2. Determine the applicant's current stage from thread history
3. Never repeat form links, instructions, or steps already in the thread
4. Never contradict information from previous HR replies
5. Flow naturally as the next message in the conversation

**Why this matters:** Without thread analysis, the bot will:
- Re-send form links the applicant already received
- Repeat steps the applicant has already completed
- Contradict what was said in earlier replies
- Treat returning applicants like first-time contacts

This is enforced in `src/llm_processor.py` (system prompt + user message
formatting) and documented in the GitHub README.

---

## Replied Tracker (added 2026-03-27)

A persistent tracker (`src/replied_tracker.py`) prevents double-replies
across runs. It records every Message-ID and thread root we've replied to,
persisted to S3 (`replied_tracker.json`) in Lambda or a local file otherwise.

**Guardrails implemented:**

1. **Message-ID check** — if we already replied to this exact email (e.g.
   crash after SMTP send but before `mark_as_read`), skip it.
2. **Thread-root check** — if we already replied to *any* email in this
   thread (via Union-Find on References/In-Reply-To), skip all other unread
   emails in the same thread. Prevents replying to old messages when a new
   reply triggers re-examination of the thread.
3. **Same-run thread dedup** — existing Union-Find logic shadows older emails
   in the same thread, only replying to the newest.
4. **Internal sender detection** — if the newest email in a thread is from
   an internal FloLabs sender, the entire thread is skipped (human is handling it).
5. **Fillout exemption** — Fillout form notifications are standalone events
   and bypass thread-level dedup (each form submission gets its own reply).

Entries auto-expire after 30 days to prevent unbounded growth.

---

## Session Checklist

Before starting work each session:
1. Read this file (CLAUDE.md)
2. Read `FUNNEL.md` for the canonical recruitment funnel reference
3. Check `dry_run_ignore.json` exists and has entries (means dry runs have run)
4. Check `outbox/` for any queued replies pending human review
5. Confirm `_HR_ADDRESSES` in `main.py` includes `hr.flomad@gmail.com`
6. Confirm `_SKIP_DOMAINS` includes `mail.hellosign.com`, `hellosign.com`, `zoom.us`
