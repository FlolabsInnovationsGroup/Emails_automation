# Emails Automation — Prospect Email Reply Bot

Automatically reads unread prospect emails from any IMAP inbox, generates personalised replies with an LLM (Anthropic Claude or OpenAI GPT), and either sends them or saves them to an `outbox/` folder for human review before sending.

---

## Features

- **IMAP email reading** — works with Gmail, Outlook, or any IMAP provider
- **LLM-powered replies** — uses Anthropic Claude (default) or OpenAI GPT
- **Full thread analysis** — reads the ENTIRE email conversation before replying (see below)
- **Double-reply prevention** — persistent tracker ensures no email or thread gets replied to twice
- **Notion KB templates** — matches incoming emails against an approved template database
- **Two reply modes** — `queue` (save for review) or `auto` (send immediately)
- **`--dry-run` flag** — preview generated replies without touching your inbox or outbox
- **Retry logic** — configurable retries for IMAP / SMTP / LLM failures
- **Delivery monitor** — auto-throttles batch size on errors, alerts Discord, auto-recovers
- **Fully configurable** via a `.env` file — no hard-coded secrets
- **Structured logging** — console + optional file output
- **Custom system prompt** — drop in your own prompt file to change the reply style

---

## ⚠️ Critical: Full Conversation Thread Analysis

**Every reply is generated based on the ENTIRE email thread, not just the latest message.**

When an applicant replies, their email body includes quoted previous messages (lines starting with `>`). The bot passes the complete thread to the LLM, which is instructed to:

1. **Read the full thread** top-to-bottom before generating any reply
2. **Determine the applicant's current stage** from what has already been discussed
3. **Never repeat** form links, instructions, or steps already visible in the thread
4. **Never contradict** information from previous HR replies
5. **Flow naturally** as the next message in an ongoing conversation

This is enforced in the system prompt (`src/llm_processor.py`) and is the single most important guarantee for reply quality. Without it, the bot would treat every email as a first-contact message and re-send information the applicant already received.

### Double-Reply Prevention

A persistent replied tracker (`src/replied_tracker.py`) prevents the bot from ever replying twice to the same email or thread:

- **Message-ID tracking** — every replied-to Message-ID is recorded
- **Thread-root tracking** — every thread we've participated in is recorded
- **Same-run dedup** — within a single run, only the newest email per thread gets a reply
- **Internal sender detection** — if a FloLabs staff member already replied in the thread, the entire thread is skipped

State is persisted to S3 (Lambda) or a local JSON file. Entries auto-expire after 30 days.

---

## Project Structure

```
Emails_automation/
├── main.py               # CLI entry point
├── requirements.txt
├── .env.example          # Template — copy to .env and fill in values
├── outbox/               # Created automatically (queue mode)
│   └── sent/             # Sent emails are moved here
└── src/
    ├── config.py         # All configuration (loaded from .env)
    ├── email_reader.py   # IMAP reader
    ├── email_sender.py   # SMTP sender + queue writer
    ├── llm_processor.py  # Anthropic / OpenAI wrapper
    └── logger.py         # Logging setup
```

---

## Quick Start

### 1. Prerequisites

- Python 3.10+
- An email account with IMAP/SMTP access enabled
  - **Gmail**: enable IMAP in Settings → Forwarding and POP/IMAP, then create an [App Password](https://myaccount.google.com/apppasswords) if you use 2-Step Verification
  - **Outlook / Office 365**: enable IMAP in Account Settings
- An API key for [Anthropic](https://console.anthropic.com) or [OpenAI](https://platform.openai.com)

### 2. Install dependencies

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
# Now edit .env with your real credentials
```

Minimum required variables:

| Variable | Description |
|---|---|
| `EMAIL_ADDRESS` | Your email address |
| `EMAIL_PASSWORD` | Your email password / App Password |
| `ANTHROPIC_API_KEY` | Anthropic API key (if using Claude) |
| `OPENAI_API_KEY` | OpenAI API key (if using GPT) |

### 4. Run

```bash
# Preview replies without sending anything
python main.py --dry-run

# Process inbox and queue replies for review (default REPLY_MODE=queue)
python main.py

# Review files in outbox/, then send them all
python main.py --send-queued

# Process only the first 3 emails
python main.py --limit 3

# Send immediately without queuing (override .env)
# Set REPLY_MODE=auto in .env, then:
python main.py
```

---

## Configuration Reference

All settings live in `.env`. See `.env.example` for a documented template.

| Variable | Default | Description |
|---|---|---|
| `EMAIL_ADDRESS` | — | Inbox address (required) |
| `EMAIL_PASSWORD` | — | Password or App Password (required) |
| `SENDER_NAME` | _(EMAIL_ADDRESS)_ | Display name in outgoing emails |
| `IMAP_HOST` | `imap.gmail.com` | IMAP server |
| `IMAP_PORT` | `993` | IMAP port |
| `IMAP_USE_SSL` | `true` | Use SSL for IMAP |
| `IMAP_MAILBOX` | `INBOX` | Mailbox/folder to monitor |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server |
| `SMTP_PORT` | `587` | SMTP port (587 = STARTTLS, 465 = SSL) |
| `SMTP_USE_TLS` | `true` | Use STARTTLS for SMTP |
| `LLM_PROVIDER` | `anthropic` | `anthropic` or `openai` |
| `ANTHROPIC_API_KEY` | — | Required when provider is anthropic |
| `OPENAI_API_KEY` | — | Required when provider is openai |
| `LLM_MODEL` | _(provider default)_ | Override model name |
| `LLM_MAX_TOKENS` | `1024` | Max reply length in tokens |
| `LLM_TEMPERATURE` | `0.7` | Reply creativity (0 = deterministic) |
| `REPLY_MODE` | `queue` | `queue` or `auto` |
| `MAX_EMAILS` | `10` | Max emails per run (0 = unlimited) |
| `MARK_AS_READ` | `true` | Mark processed emails as read |
| `RETRY_ATTEMPTS` | `3` | Retry count for transient failures |
| `RETRY_DELAY_SECONDS` | `2.0` | Seconds between retries |
| `SYSTEM_PROMPT_FILE` | — | Path to a custom prompt `.txt` file |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `LOG_FILE` | — | Optional log file path |

---

## Provider Defaults

| Provider | Default model |
|---|---|
| Anthropic | `claude-sonnet-4-6` |
| OpenAI | `gpt-4o` |

Override with `LLM_MODEL=<model-id>` in `.env`.

---

## Reply Mode Details

### `REPLY_MODE=queue` (recommended to start)

Each generated reply is saved as two files in `outbox/`:

- `outbox/<timestamp>_<uid>.eml` — the ready-to-send email file
- `outbox/<timestamp>_<uid>.json` — metadata (to, subject, body, timestamp)

Review them, then run `python main.py --send-queued` to dispatch all queued emails. Sent files are moved to `outbox/sent/`.

### `REPLY_MODE=auto`

Replies are sent immediately via SMTP after generation. Use with care.

---

## Custom System Prompt

To change how the LLM writes replies, create a plain-text file (e.g. `my_prompt.txt`) and set:

```
SYSTEM_PROMPT_FILE=my_prompt.txt
```

The file replaces the built-in prompt entirely, so include all the instructions you need.

---

## Outlook / Office 365 Setup

```
IMAP_HOST=outlook.office365.com
IMAP_PORT=993
SMTP_HOST=smtp.office365.com
SMTP_PORT=587
SMTP_USE_TLS=true
```

---

## Automating with a Scheduler

To run the bot every 15 minutes:

**Linux/macOS (cron):**
```bash
*/15 * * * * cd /path/to/Emails_automation && .venv/bin/python main.py >> logs/cron.log 2>&1
```

**Windows (Task Scheduler):**
Create a Basic Task that runs `.venv\Scripts\python.exe main.py` in the project directory on a schedule.

---

## Funnel Labels (Gmail Stage Tracking)

The bot can automatically apply a `Prospect/*` Gmail label to each processed email thread, so you can track where every prospect is in your sales funnel directly from Gmail.

### The 8 Stages

| Label | Meaning |
|---|---|
| `Prospect/01-New Lead` | First contact — no reply from us yet |
| `Prospect/02-Replied` | We replied, but the prospect hasn't shown strong interest |
| `Prospect/03-Interested` | Prospect expressed genuine interest or asked follow-up questions |
| `Prospect/04-Demo Requested` | Prospect asked for a demo or product walkthrough |
| `Prospect/05-Proposal Sent` | A formal proposal or pricing has been shared |
| `Prospect/06-Negotiating` | Prospect is negotiating terms, pricing, or contract details |
| `Prospect/07-Won` | Deal closed — prospect confirmed they want to proceed |
| `Prospect/08-Lost` | Prospect declined, unsubscribed, or went with a competitor |

The LLM reads each incoming email + our generated reply and picks the most appropriate stage. Only one `Prospect/*` label is ever active on a thread at a time — the old one is removed before the new one is applied.

### Gmail API Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a project (or use an existing one).
2. Enable the **Gmail API** for that project (*APIs & Services → Library → Gmail API → Enable*).
3. Create OAuth 2.0 credentials (*APIs & Services → Credentials → Create Credentials → OAuth client ID → Desktop app*).
4. Download the JSON file and save it as `credentials.json` in the project root.
5. Set the following in your `.env`:

```
GMAIL_CREDENTIALS_FILE=credentials.json
GMAIL_TOKEN_FILE=token.json
GMAIL_LABELS_ENABLED=true
```

6. Run the bot once — a browser window will open asking you to grant Gmail access:

```bash
python main.py --dry-run
```

After you approve, a `token.json` file is saved automatically. Subsequent runs use it without prompting.

> **Note:** If `credentials.json` is missing or `GMAIL_LABELS_ENABLED=false`, funnel labels are silently skipped — the rest of the automation continues normally.

---

## test_send_email.py — Send Test Scenarios

Use this script to drop realistic fake prospect emails into your own inbox so you can test the full pipeline without waiting for real inbound leads.

```bash
# List available scenarios
python test_send_email.py --list

# Send the default scenario (new_lead)
python test_send_email.py

# Send a specific scenario
python test_send_email.py --scenario interested
python test_send_email.py --scenario demo_request
python test_send_email.py --scenario negotiating
```

### Available Scenarios

| Scenario | Expected Stage |
|---|---|
| `new_lead` | `Prospect/01-New Lead` |
| `interested` | `Prospect/03-Interested` |
| `demo_request` | `Prospect/04-Demo Requested` |
| `negotiating` | `Prospect/06-Negotiating` |

The email is sent FROM your `EMAIL_ADDRESS` TO your `EMAIL_ADDRESS`, so it lands straight in your inbox ready to be processed by `python main.py`.

---

## Security Notes

- Never commit `.env`, `credentials.json`, or `token.json` — they are already in `.gitignore` by convention.
- Use App Passwords instead of your main account password when possible.
- `REPLY_MODE=queue` is the safest default: no emails are sent without manual review.
