# FloLabs HR Recruitment Funnel

> **Reference source:** [FloLabs HR Workflow Google Doc](https://docs.google.com/document/d/1XZXUZ6BLW0Xn9Nn38qB58IsjBrlWNvHo0gwAR1BNOvA/edit)
>
> This file is the canonical reference for the automation's expected behaviour.
> Update it whenever the workflow changes.

---

## Overview

Applicants move through a multi-stage funnel from first contact to signed intern.
Every stage has a specific email template and a corresponding HubSpot lifecycle
stage + lead status. The automation mirrors this exactly.

Emails before the Orientation Session are sent from **hr@flolabsinnovations.com**.
Emails from the Orientation Session onwards are sent from **hr@flolabsrd.com**,
and **always CC oliver.abreu@flolabsrd.com and john@flolabsrd.com**.

---

## Stage Map

```
Application submitted
        │
        ▼
[1] NEW ──────────────────────────────────────── "1 First Email" (auto, 3h delay)
        │ no reply in 3 days
        ▼
[2] REMINDER 1ST IF INTERESTED ────────────────── "Reminder 1st - Interested or Not" (auto)
        │ no reply in 3 days
        ▼
[3] REMINDER FROM PAULA ────────────────────────── "Reminder from PAULA" (auto)
        │ no reply in 3 days
        ▼
[4] NO ANSWER ──────────────────────────────────── "Last Reminder" (auto, from hr@flolabsrd.com)
                                                   + "Last Reminder Gmail" (auto, from Gmail)
        │
        │ At any stage 1–4: applicant replies "interested"
        ▼
[5] CONTACTED ──────────────────────────────────── "2 Contacted Websites Form" (MANUAL)
        │                                           → sends Form 1 link
        │ no reply in 4 days (form not submitted)
        ▼
[6] REMINDER AFTER CONTACTED ───────────────────── "Reminder after Contacted" (auto)
        │ no reply in 5 days (form not submitted)
        ▼
[7] SECOND REMINDER CONTACTED ──────────────────── "Second reminder Contacted" (auto)
        │ no reply in 5 days (form not submitted)
        ▼
[8] NO ANSWER ──────────────────────────────────── "Last Reminder" (auto, from hr@flolabsrd.com)
        │
        │ At any stage 5–8: applicant submits Form 1 (Websites & Presentations)
        │   → HubSpot automation IMMEDIATELY sets lifecycle stage to "Interested"
        │   → HR reviews answers daily by filtering HubSpot: Lifecycle Stage = "Interested"
        ▼
[8b] INTERESTED (HubSpot intermediate) ─────────── No email sent yet — awaiting HR review
        │   If answers are low-effort (yes/no/I don't know) → send low-effort reply, keep at Interested
        │   If answers are high-effort → send BEFORE MEETING
        ▼
[9] BEFORE MEETING ─────────────────────────────── "BEFORE MEETING" (AUTOMATED HERE)
        │                                           → sends Form 2 link (Meeting Recordings)
        │                                           → From: hr@flolabsinnovations.com
        │ no reply in 4 days (form not submitted)
        ▼
[10] REMINDER BEFORE MEETING ───────────────────── "REMINDER BEFORE MEETING" (auto)
        │ no reply in 5 days (form not submitted)
        ▼
[11] 2ND REMINDER BEFORE MEETING ───────────────── "Reminder 1st - Interested or Not" (auto)
        │ no reply in 3 days (form not submitted)
        ▼
[12] NO ANSWER ─────────────────────────────────── "Last Reminder" (auto, from hr@flolabsrd.com)
        │
        │ At any stage 9–12: applicant submits Form 2 (Meeting Recordings)
        │   → HR reviews answers daily (same Lifecycle Stage = "Interested" filter)
        │   If answers are low-effort → send low-effort reply, keep at Before Meeting
        │   If answers are high-effort → send Invitation Saturday
        ▼
[13] SATURDAY MEETING ──────────────────────────── "Invitation Saturday" (AUTOMATED HERE)
        │                                           → invites to Orientation Session
        │                                           → ask for 2–3 availability slots
        │                                           → From: hr@flolabsrd.com (NOT innovations)
        │
        │ After Orientation Session (Monday follow-up):
        ├─ attended full session ──────────────────── "Attended + Masterclass" (MANUAL)
        ├─ attended partial (missed Q&A) ──────────── "Briefly Attended + Q&A" (MANUAL, paste recording link)
        │   ─ not attended ─────────────────────────── "Not attended" → lifecycle: NOT ATTENDED
        │       │ no reply in 3 days
        │       └─────────────────────────────────────── "Last Reminder" (auto) → NO ANSWER
        └─ attended < 1.5h ────────────────────────── "Briefly Attended" → lifecycle: BRIEFLY ATTENDED
        │
        ▼
[14] ATTENDED ──────────────────────────────────── lifecycle/lead status set to "Attended"
        │                                           CC: oliver + john on all emails from here
        │
        │ applicant submits Form 3 (General Masterclass)
        │   Trigger: lifecycle = "Attended" OR "Interested" (post-orientation)
        │   HR reviews answers — low effort: ask to resubmit
        ▼
[15] SECOND MEETING ────────────────────────────── "Introduce Oliver" (AUTOMATED HERE)
        │                                           CC: oliver.abreu@flolabsrd.com + john@flolabsrd.com
        │
        │ Oliver meets with applicant, sends recap email
        ▼
[16] AGREEMENT SENT ────────────────────────────── "Onboarding" (MANUAL, reply to Oliver's recap)
        │                                           CC: oliver + john
        │                                           includes: flolabsrd.com email, payment link,
        │                                           DropboxSign agreements
        │
        │ applicant signs both agreements + pays (if applicable)
        ▼
[17] SIGNED PAID / SIGNED NON-PAID ─────────────── Full onboarding process (Discord, Toggl, Notion)
```

---

## The Three Fillout Forms

### Form 1 — Websites & Presentations
- **Triggered by:** applicant replying "interested" → HR sends "Contacted Websites Form" manually
- **Link sent in:** template "2 Contacted Websites Form"
- **Fillout form ID:** `besSHDQpaHus`
- **Fillout subject line:** `New submission for your "Websites & Presentations" form`
- **Quality bar:** all questions must be answered — **except the last 4** (those belong to Form 2). Answers must be specific, referencing actual projects/websites. "Yes", "No", "I don't know" = low effort.
- **HubSpot state when submission arrives:** Lifecycle = "Interested", Lead Status = "Contacted" / "Reminder after Contacted" / "Second reminder Contacted"
- **On high-effort submission → automation sends:** "BEFORE MEETING" reply with Form 2 link, change stage to **Before Meeting**
  - **From:** `hr@flolabsinnovations.com`
- **On low-effort submission → automation sends:** polite request to resubmit — stage stays **Interested**

### Form 2 — Meeting Recordings
- **Triggered by:** applicant submitting Form 1 (high effort)
- **Link sent in:** "BEFORE MEETING" template
- **Fillout form ID:** `wqyLXfXzWGus`
- **Direct link:** `https://build.fillout.com/t/wqyLXfXzWGus`
- **Fillout subject line:** `New submission for your "Meeting Recordings" form`
- **Quality bar:** **all** questions must be answered, **including the last 4** (the meeting recording questions). Answers must reference specific content from recordings (project names, discussion topics, 3 robot control modes, etc.). "Yes"/"No" = low effort.
- **HubSpot state when submission arrives:** Lifecycle = "Interested", Lead Status = "Before Meeting" / "Reminder Before Meeting" / "2nd Reminder Before Meeting"
- **On high-effort submission → automation sends:** "Invitation Saturday" reply, change stage to **Saturday Meeting**
  - **From:** `hr@flolabsrd.com` ← note: different from Form 1
- **On low-effort submission → automation sends:** request to resubmit — stage stays **Before Meeting**

### Form 3 — General Masterclass
- **Triggered by:** applicant attending Orientation Session
- **Link sent in:** "Attended + Masterclass" or "16 links Interested Masterclass" template
- **Fillout subject line:** `New submission for your "General Masterclass" form`
- **Quality bar:** answers must show genuine reflection on the content presented — not generic or copy-pasted
- **HubSpot state when submission arrives:** Lifecycle = "Attended" **or** "Interested" (post-orientation path)
- **On high-effort submission → automation sends:** "Introduce Oliver" reply, change stage to **Second Meeting**
  - **From:** `hr@flolabsrd.com`
  - **CC:** `oliver.abreu@flolabsrd.com`, `john@flolabsrd.com`
- **On low-effort submission → automation sends:** request to resubmit — stage stays **Attended**

---

## Effort Evaluation

Every Fillout form submission is evaluated by the LLM before a reply is sent.

**Low-effort signals (any = flag):**
- Random characters or keyboard mashing (`asd`, `sdf`, `fds`)
- One-word or one-sentence answers that don't engage with the question
- No reference to specific content (project names, meeting details, website sections)
- Multiple identical or near-identical answers
- Copy-pasted generic filler

**High-effort signals:**
- Specific references to projects (RoboShow, CAIPO, FlomadTravel, MoodChanger, Space Buddy, etc.)
- Personal opinion and genuine reflection
- Full sentences with reasonable length
- Shows they actually watched the recordings / visited the websites

The evaluator fails **safe**: if the LLM call errors, the submission is treated as high-effort so genuine responses are never accidentally blocked.

---

## Email Sending Rules

| Stage | Template | From address | CC |
|---|---|---|---|
| Stages 1–12 (up to and including Form 2 reminder cycle) | All early-stage templates | `hr@flolabsinnovations.com` | — |
| Stage 13 — Form 2 approved | "Invitation Saturday" | `hr@flolabsrd.com` | — |
| Stages 14+ (post-orientation) | All templates | `hr@flolabsrd.com` | `oliver.abreu@flolabsrd.com`, `john@flolabsrd.com` |

> ⚠️ **Important boundary:** The "Invitation Saturday" email (Form 2 approval) must come from
> `hr@flolabsrd.com`, not `hr@flolabsinnovations.com`. This is explicitly stated in the workflow doc.
> The automation config must expose a second `from_email` for this stage (`FROM_EMAIL_RD`).

- **All Fillout replies** are sent as a reply to the notification email (threading)
- The `To:` address is **always the lead's email** extracted from the form body — never `notifications@fillout.com`
- The lead's first name is extracted from the `First name` field in the form body

---

## Manual Reminders After Orientation (weekly, at minimum)

Go through the statistics sheet and open each contact with status "Open":

1. **Attended, received only first email, no response** → "First Reminder After Attended"
2. **Attended, received first email + first reminder, no response** → "Reminder 1st - Interested or Not"
3. **Attended, received two reminders, no response** → "Last Reminder Gmail" (from Paula's Gmail) + SMS via HubSpot + flag for Oliver to call
4. **Second Meeting, received intro email, hasn't scheduled** → "Reminder 1st - Interested or Not"
5. **Agreement Sent, received onboarding, no response** → "Reminder If they don't reply after 1-on-1 meeting"
6. **Agreement Sent, received onboarding + first reminder, no response** → "Reminder 1st - Interested or Not"
7. **Agreement Sent, received two reminders, no response** → "Phone number if not answer after interview" + SMS + flag for Oliver to call

---

## Common Email Scenarios (Manual Handling)

| Situation | Action | Change stage? |
|---|---|---|
| Contact at stages 1–4 says "first email received" | Send "First email" template | No |
| Contact at any stage says meeting link is broken | Send recording link: `https://app.read.ai/analytics/meetings/01K40HF517C9813761J0JS0CKE` | No |
| Contact at stages 1–4 says "interested" | Send "Contacted Websites Form", change to **Contacted** | Yes |
| Any contact says "not interested" (no reason) | Send "Not Interested", change to **Not Interested**, set reason "No Reason Provided" | Yes |
| Any contact says "not interested" (with reason) | Change to **Not Interested**, set reason — do NOT send reply | Yes |
| Contacted/Reminder stages need more time for form | Send "Looking forward to their answers" | No |
| Any contact asks if paid | Send "Is it paid?" | No |
| Any contact asks about duration/hours/start date | Send "DURATION OF INTERNSHIP AND WEEKLY COMMITMENT" | No |
| Any contact asks what the orientation session is | Send "What is the orientation meeting about" | No |
| Any contact asks about selection process steps | Send "Selection process steps" | No |
| Before Meeting stages: available but form not done | Send "I am available to attend the group meeting" | No |
| Attended/Interested + Masterclass form submitted | Send "Introduce Oliver", CC oliver + john, change to **Second Meeting** | Yes |

---

## Key Contacts & Credentials

| Role | Detail |
|---|---|
| Founder & CEO | John Hazlewood — `john@flolabsrd.com` |
| Head of R&D | Oliver Abreu — `oliver.abreu@flolabsrd.com` |
| HR inbox (primary) | `hr@flolabsinnovations.com` |
| HR inbox (reminders) | `hr@flolabsrd.com` |
| HR Gmail | `hr.flomad@gmail.com` |
| Paula's Gmail (last reminders) | `paula.ferry.flolabs@gmail.com` |
| HubSpot login | `hr@flomad.com` |
| Fillout login | `hr@flomadlabs.com` |
| Zoom account | `john@flomadlabs.com` |

---

## Orientation Session (Monday Follow-up Checklist)

1. Log in to Zoom (`john@flomadlabs.com`) — wait 1 hour after session ends
2. Go to **Account Management → Reports → Meeting and webinar history**
3. Select the session date, click **Unique users**
4. Add all attendees to the [statistics sheet](https://docs.google.com/spreadsheets/d/1iGEwfTDoyzzl6-TL4S6b_9Q8Hm3MCz_eq4PfuRGI9Ys/edit)
   - Include: date, name, attendance duration, comments (full / missed Q&A / brief)
   - Reference: how long John stayed = full session length
5. For each attendee, find on HubSpot → set lifecycle to **Attended**
   - Full session → send "Attended + Masterclass"
   - Partial (missed Q&A) → send "Briefly Attended + Q&A" (paste recording link)
   - < 1.5 hours → set lifecycle to **Briefly Attended**, send "Briefly Attended"
6. For all contacts still at **Saturday Meeting** (did not attend) → send "Not attended", change to **Not Attended**

**CC oliver + john on every email sent after the Orientation Session.**

---

## Orientation Meeting Links

| Purpose | Link |
|---|---|
| Orientation recording (main) | https://us06web.zoom.us/rec/share/bB2nJy3Mkl6jdqNyOg0qpfZg8Zj9thY3DmA5bJ1qimM5H-VrfKt6I3xW-JlGcmHv.9t5JLfDzF1F1gvwl |
| Read.ai meeting link | https://app.read.ai/analytics/meetings/01K40HF517C9813761J0JS0CKE |
| Meeting Recordings form recording | https://us06web.zoom.us/rec/share/0L1tyq8pFqZaU12ajsLeZ4PF4tKWVDfwTmk6m-K3MMOfIF2-mcD31W8HaBLeIvFC.rdbRNcuFICRT9KzF |
| Daily tasks recording | https://us06web.zoom.us/rec/share/i80cWWqzqS99bph3Gz7_7J38W7HKcAhbD03H61fQCum_CrOTB7bVeCF-WvqQXNmh.ejmrxPBld5KNmSVW |
| Oliver 1-on-1 recording | https://us06web.zoom.us/rec/share/2Jfw41V8qM_YYo1RkmPQHxa9M5dGESHkPzPpTRblmS1xc_HO9h96LoS6scgx5MAm.T5WAqoR7iIzQERZo |
| Oliver 1-on-1 (Read.ai) | https://app.read.ai/analytics/meetings/01KFX9YDWXTZ27434AXB5PJCBP |

---

## Statistics & Tracking Sheets

| Sheet | Purpose |
|---|---|
| [Attendance & Progress](https://docs.google.com/spreadsheets/d/1iGEwfTDoyzzl6-TL4S6b_9Q8Hm3MCz_eq4PfuRGI9Ys/edit) | Orientation attendance, masterclass submissions, Oliver meetings |
| [Signed Interns](https://docs.google.com/spreadsheets/d/1LUXgk24h7Um2AXKdWk4S8c_X5X1DaVHZ6qlesVMKe1U/edit) | All signed interns, payment tracking, Toggl workspaces |

---

## Config Requirements

The automation requires **two** from-addresses — add both to `.env`:

```
FROM_EMAIL=hr@flolabsinnovations.com        # Form 1 replies, general LLM replies
FROM_EMAIL_RD=hr@flolabsrd.com             # Form 2 replies ("Invitation Saturday"), Form 3, post-orientation
```

---

## Automation Scope (this repository)

This codebase handles **inbound email triage** for the HR Gmail inbox (`hr.flomad@gmail.com`).

| What it does | Where |
|---|---|
| Polls Gmail IMAP for unread emails | `src/email_reader.py` |
| Skips HubSpot/LinkedIn/own-address noise | `main.py → _is_processable()` |
| Detects Fillout form notifications | `src/fillout_parser.py → is_fillout_notification()` |
| Re-addresses Fillout emails to the lead | `src/fillout_parser.py → extract_lead()` |
| Identifies which form was submitted | `src/fillout_parser.py → detect_form_type()` |
| Extracts open-ended Q&A answers | `src/fillout_parser.py → extract_qa_pairs()` |
| Evaluates submission effort with LLM | `src/llm_processor.py → evaluate_effort()` |
| Sends correct workflow reply per form | `main.py → process_emails()` |
| Generates LLM reply for non-Fillout emails | `src/llm_processor.py → generate_reply()` |
| Sends/queues/previews outbound emails | `src/email_sender.py` |
| Applies Gmail funnel labels | `src/funnel_labels.py` |

**What it does NOT do (still manual or HubSpot):**
- Initial outreach sequences (stages 1–4 reminders) — handled by HubSpot workflows
- Sending "Contacted Websites Form" when someone replies "interested" — manual
- Orientation session Monday follow-up emails — manual
- Post-orientation reminders — manual (weekly)
- Onboarding (Discord, Toggl, Notion, DropboxSign, Stripe) — manual
