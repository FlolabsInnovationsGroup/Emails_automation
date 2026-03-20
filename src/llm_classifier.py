"""LLM-based funnel stage classifier."""

from __future__ import annotations

import logging

from .config import Config
from .email_reader import IncomingEmail
from .funnel_labels import (
    ALL_STAGES,
    STAGE_REPLIED,
    STAGE_NEW_APPLICANT,
    STAGE_SCREENING,
    STAGE_INTERVIEW_SCHEDULED,
    STAGE_INTERVIEW_DONE,
    STAGE_OFFER_SENT,
    STAGE_HIRED,
    STAGE_REJECTED,
)

logger = logging.getLogger("email_automation.classifier")

_CLASSIFY_PROMPT = """\
You are an HR recruiting assistant. Classify the following email conversation into \
exactly one of these recruiting funnel stages and reply with ONLY the stage string — \
nothing else, no punctuation, no explanation.

Stages (in order):
{stages}

Definitions:
- Prospect/01-New Applicant: application or first contact received, no reply from us yet
- Prospect/02-Replied: we replied acknowledging the application, candidate hasn't confirmed next steps
- Prospect/03-Screening: candidate is engaged, screening call or availability being discussed
- Prospect/04-Interview Scheduled: interview or call has been confirmed with a specific date/time
- Prospect/05-Interview Done: interview completed, follow-up or decision pending
- Prospect/06-Offer Sent: a job offer has been formally extended
- Prospect/07-Hired: candidate accepted the offer and is joining
- Prospect/08-Rejected: candidate declined, withdrew, was rejected, or went silent

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
) -> str:
    """Return the funnel stage string that best fits this conversation.

    Falls back to STAGE_REPLIED on any error.
    """
    prompt = _CLASSIFY_PROMPT.format(
        stages="\n".join(f"  {s}" for s in ALL_STAGES),
        sender=f"{incoming.sender} <{incoming.sender_address}>",
        subject=incoming.subject,
        body=(incoming.body_text or incoming.body_html or "")[:2000],
        reply=reply_body[:500],
    )

    try:
        raw = _call_llm(prompt, config).strip()
        # Accept the response if it exactly matches a known stage
        if raw in ALL_STAGES:
            return raw
        # Fuzzy match — try a case-insensitive substring search
        raw_lower = raw.lower()
        for stage in ALL_STAGES:
            if stage.lower() in raw_lower or raw_lower in stage.lower():
                return stage
        logger.warning("LLM returned unrecognised stage %r — defaulting.", raw)
    except Exception as exc:
        logger.warning("Stage classification failed: %s — defaulting to %s.", exc, STAGE_REPLIED)

    return STAGE_REPLIED


# ── LLM dispatch (mirrors llm_processor.py pattern) ─────────────────────────

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
