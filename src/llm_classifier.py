"""LLM-based funnel stage classifier.

For Fillout form submissions, the stage is determined deterministically from
the form type (see funnel_labels.FORM_STAGE_MAP) — no LLM needed.
For all other emails (direct applicant replies), we use the LLM to classify.
"""

from __future__ import annotations

import logging

from .config import Config
from .email_reader import IncomingEmail
from .funnel_labels import (
    ALL_STAGES,
    STAGE_REPLIED,
    STAGE_NEW,
    STAGE_INTERESTED,
    STAGE_SCREENING,
    STAGE_BEFORE_MEETING,
    STAGE_SATURDAY_MEETING,
    STAGE_ATTENDED,
    STAGE_SECOND_MEETING,
    STAGE_NOT_INTERESTED,
    STAGE_NO_ANSWER,
    FORM_STAGE_MAP,
)

logger = logging.getLogger("email_automation.classifier")

_CLASSIFY_PROMPT = """\
You are an HR assistant at FloLabs, classifying an applicant email into exactly one \
recruiting funnel stage. Reply with ONLY the stage string — nothing else.

Stages (in order of progression):
{stages}

Definitions:
- Prospect/01-New Applicant: first contact, no reply sent yet
- Prospect/02-Replied: we replied acknowledging interest, awaiting next action from candidate
- Prospect/03-Interested: candidate confirmed interest, Form 1 (Websites & Presentations) has been sent
- Prospect/03-Screening: Form 1 submitted, reviewing quality before sending Form 2
- Prospect/04-Before Meeting: Form 1 approved, Form 2 (Meeting Recordings) sent to candidate
- Prospect/05-Saturday Meeting: Form 2 approved, candidate invited to Orientation Session
- Prospect/06-Attended: candidate attended the Orientation Session
- Prospect/06-Briefly Attended: candidate attended only part of the Orientation Session
- Prospect/06-Not Attended: candidate did not attend the Orientation Session
- Prospect/07-Second Meeting: General Masterclass form approved, candidate introduced to Oliver
- Prospect/08-Agreement Sent: onboarding email sent, agreements and payment link shared
- Prospect/09-Signed Paid: candidate signed agreements and paid the tuition fee
- Prospect/09-Signed Non-Paid: candidate signed agreements (scholarship / no fee)
- Prospect/XX-Not Interested: candidate said they are not interested or withdrew
- Prospect/XX-No Answer: candidate stopped responding after multiple reminders
- Prospect/XX-Rejected: candidate was rejected by FloLabs

--- Incoming email ---
From: {sender}
Subject: {subject}
Body:
{body}

--- Our reply ---
{reply}

Respond with exactly one stage string from the list above."""


def classify_stage(
    incoming: IncomingEmail,
    reply_body: str,
    config: Config,
    form_type: str | None = None,
) -> str:
    """Return the funnel stage string that best fits this conversation.

    If ``form_type`` is provided (Fillout submission), returns the stage
    deterministically from FORM_STAGE_MAP — no LLM call needed.

    Falls back to STAGE_REPLIED on any error.
    """
    # Deterministic path for Fillout form submissions
    if form_type and form_type in FORM_STAGE_MAP:
        stage = FORM_STAGE_MAP[form_type]
        logger.info("Stage %r assigned deterministically for form_type=%r.", stage, form_type)
        return stage

    # LLM classification for regular emails
    prompt = _CLASSIFY_PROMPT.format(
        stages="\n".join(f"  {s}" for s in ALL_STAGES),
        sender=f"{incoming.sender} <{incoming.sender_address}>",
        subject=incoming.subject,
        body=(incoming.body_text or incoming.body_html or "")[:2000],
        reply=reply_body[:500],
    )

    try:
        raw = _call_llm(prompt, config).strip()
        if raw in ALL_STAGES:
            return raw
        raw_lower = raw.lower()
        for stage in ALL_STAGES:
            if stage.lower() in raw_lower or raw_lower in stage.lower():
                return stage
        logger.warning("LLM returned unrecognised stage %r — defaulting.", raw)
    except Exception as exc:
        logger.warning("Stage classification failed: %s — defaulting to %s.", exc, STAGE_REPLIED)

    return STAGE_REPLIED


# ── LLM dispatch ─────────────────────────────────────────────────────────────

def _call_llm(prompt: str, config: Config) -> str:
    if config.llm_provider == "anthropic":
        return _call_anthropic(prompt, config)
    return _call_openai(prompt, config)


def _call_anthropic(prompt: str, config: Config) -> str:
    import anthropic  # type: ignore
    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    response = client.messages.create(
        model=config.effective_llm_model,
        max_tokens=32,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def _call_openai(prompt: str, config: Config) -> str:
    import openai  # type: ignore
    client = openai.OpenAI(api_key=config.openai_api_key)
    response = client.chat.completions.create(
        model=config.effective_llm_model,
        max_tokens=32,
        temperature=0.0,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""
