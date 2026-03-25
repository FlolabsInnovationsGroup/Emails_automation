"""LLM reply generator — wraps Anthropic and OpenAI clients."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .config import Config
from .email_reader import IncomingEmail

logger = logging.getLogger("email_automation.llm")

_EFFORT_SYSTEM_PROMPT = """\
You are evaluating whether a job applicant put genuine effort into answering open-ended
questions on an internship application form.

You will receive a list of question/answer pairs. Assess the overall effort and quality.

Low-effort indicators (any of these = low effort):
- Answers are random characters, gibberish, or keyboard mashing (e.g. "asd", "sdf", "fds")
- Answers are one word or one very short sentence that clearly didn't engage with the question
- Answers are copy-pasted generic filler with no specific reference to what was asked
- Multiple answers appear identical or nearly identical
- Answers do not reference anything from the actual content (meetings, projects, websites)

High-effort indicators:
- Answers are specific, detailed, and reference actual content from the form (project names,
  meeting details, specific observations)
- Answers show genuine engagement and personal opinion
- Answers are written in full sentences with reasonable length

Respond ONLY with valid JSON:
{
  "is_low_effort": true or false,
  "confidence": 0.0-1.0,
  "reason": "one sentence explaining the verdict"
}
"""

DEFAULT_SYSTEM_PROMPT = """\
You are Paula, an HR assistant at FloLabs Innovations, responding to internship applicants.

== SELECTION PROCESS — READ THIS CAREFULLY ==
The FloLabs selection process has a fixed sequence. You must never invent steps or suggest
meetings that don't exist. The process is:

1. Applicant confirms interest → HR sends them Form 1 (Websites & Presentations).
2. Applicant submits Form 1 → HR sends them Form 2 (Meeting Recordings).
3. Applicant submits Form 2 → HR invites them to the GROUP Orientation Session on Saturday
   (led by John Hazlewood, the Founder & CEO). There is NO individual call or interview at
   this stage. Do NOT ask for availability slots before this point.
4. Applicant attends orientation → HR sends them Form 3 (General Masterclass).
5. Applicant submits Form 3 → HR introduces them to Oliver Abreu for a 1-on-1.
   Oliver is a former FloLabs intern who completed the program — he can answer any
   questions about what the internship is actually like from a first-hand perspective.
   This is the FIRST and ONLY time availability is requested (2–3 time slots for Oliver).
6. After meeting Oliver → John reviews and agreements/onboarding follow.

== THREAD CONTEXT ==
The email body you receive may contain the FULL conversation thread, including quoted
previous messages (lines starting with ">"). READ THE ENTIRE THREAD CAREFULLY to
understand the applicant's current stage in the funnel and what has already been discussed.
Your reply must be consistent with all prior messages in the thread — never repeat
information or steps that were already covered. If the thread shows the applicant has
already received a form link, do not send it again. If they already confirmed interest,
acknowledge that context.

== RULES FOR YOUR REPLY ==
- Tone: warm, encouraging, professional — never robotic.
- Keep it concise (under 150 words).
- Start with a greeting: "Hi {first_name},"
  Use the first name from the "From:" line. Never use an email address or "Notifications".
- Acknowledge what they said briefly and specifically.
- Give the correct next step based on their stage (see process above).
- Consider the FULL email thread (including quoted messages) to determine the applicant's
  current stage. Do not repeat steps that are already visible in the thread.
- NEVER suggest a call, interview, or ask for availability slots unless the applicant has
  already submitted Form 3 (General Masterclass). Before that point, the next step is always
  either completing a form or waiting for the Saturday orientation invitation.
- If the applicant says they missed the orientation session or asks for the meeting link,
  include this standing Zoom link for the next Saturday session:
  https://us06web.zoom.us/j/82809015394?pwd=eN4AWg4nT3DXZqPPE3OwqDQmjrgGQ2.1
- When referring to the 1-on-1 meeting, always say it is with Oliver Abreu, a former
  FloLabs intern who completed the program and can answer questions about the experience
  first-hand. NEVER say the 1-on-1 is with John.
- NEVER mention salary, role details, or anything not confirmed in the original email.
- Do NOT include any sign-off, closing, or signature — the system adds that automatically.
- Output plain text only, no markdown.
"""


def _load_system_prompt(config: Config) -> str:
    if config.system_prompt_file:
        path = Path(config.system_prompt_file)
        if path.exists():
            return path.read_text(encoding="utf-8")
        logger.warning("System prompt file not found: %s — using default.", path)
    return DEFAULT_SYSTEM_PROMPT


def _build_user_message(incoming: IncomingEmail) -> str:
    body = incoming.body_text or "[No plain-text body — see HTML version]"
    # Use the (possibly patched) sender name so the LLM addresses the lead correctly.
    # If the name looks like an email address or is missing, derive a display name.
    sender_name = incoming.sender or ""
    if not sender_name or "@" in sender_name:
        # Fall back to the part before @ in the email address
        sender_name = incoming.sender_address.split("@")[0].replace(".", " ").replace("_", " ").title()

    # The body may contain the full email thread with quoted previous messages.
    # Pass it all to the LLM so it has full context of the conversation history.
    return (
        f"From: {sender_name} <{incoming.sender_address}>\n"
        f"Subject: {incoming.subject}\n"
        f"Date: {incoming.date}\n\n"
        f"== EMAIL BODY (may include quoted thread history below) ==\n"
        f"{body}"
    )


class LLMProcessor:
    """Generates reply drafts using the configured LLM provider."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._system_prompt = _load_system_prompt(config)
        self._client = self._build_client()

    # ------------------------------------------------------------------
    # Client initialisation
    # ------------------------------------------------------------------

    def _build_client(self):
        provider = self.config.llm_provider
        if provider == "anthropic":
            try:
                import anthropic  # type: ignore
            except ImportError as exc:
                raise ImportError("Install 'anthropic' package: pip install anthropic") from exc
            return anthropic.Anthropic(api_key=self.config.anthropic_api_key)
        elif provider == "openai":
            try:
                import openai  # type: ignore
            except ImportError as exc:
                raise ImportError("Install 'openai' package: pip install openai") from exc
            return openai.OpenAI(api_key=self.config.openai_api_key)
        else:
            raise ValueError(f"Unknown LLM provider: {provider!r}. Use 'anthropic' or 'openai'.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_reply(self, incoming: IncomingEmail, template_context: str | None = None) -> str:
        """Return the generated reply body text.

        If *template_context* is provided (a Notion KB template body), it is
        prepended to the system prompt so the LLM personalises the approved
        template rather than free-forming a reply.
        """
        cfg = self.config
        user_message = _build_user_message(incoming)

        # Build the effective system prompt
        system_prompt = self._system_prompt
        if template_context:
            template_preamble = (
                "== APPROVED TEMPLATE FOR THIS SCENARIO ==\n"
                "The following is an approved response template for this type of inquiry. "
                "Use it as the BASIS for your reply, adapting it naturally to address the "
                "specific points the applicant raised. You may adjust tone and details but "
                "keep the core message intact. Replace {first name} with the applicant's "
                "actual first name.\n\n"
                "IMPORTANT: Keep your reply concise (under 150 words). Extract the KEY "
                "points from the template — do NOT copy the entire template verbatim. "
                "Summarize and personalize, don't dump.\n\n"
                f"Template:\n{template_context}\n\n"
                "== END TEMPLATE ==\n\n"
            )
            system_prompt = template_preamble + system_prompt

        last_exc: Exception | None = None

        for attempt in range(1, cfg.retry_attempts + 1):
            try:
                reply = self._call_llm_with_system(user_message, system_prompt)
                logger.info("Reply generated for UID %s (attempt %d).", incoming.uid, attempt)
                return reply.strip()
            except Exception as exc:
                last_exc = exc
                logger.warning("LLM attempt %d/%d failed: %s", attempt, cfg.retry_attempts, exc)
                if attempt < cfg.retry_attempts:
                    time.sleep(cfg.retry_delay_seconds)

        raise RuntimeError(f"LLM failed after {cfg.retry_attempts} attempts.") from last_exc

    def evaluate_effort(self, qa_pairs: list[tuple[str, str]]) -> dict:
        """Evaluate whether a Fillout form submission shows genuine effort.

        Args:
            qa_pairs: List of (question, answer) tuples from fillout_parser.extract_qa_pairs().

        Returns:
            Dict with keys: is_low_effort (bool), confidence (float), reason (str).
            Falls back to is_low_effort=False on any error so genuine responses are never blocked.
        """
        if not qa_pairs:
            return {"is_low_effort": False, "confidence": 0.0, "reason": "No Q&A pairs to evaluate."}

        # Format the Q&A for the LLM
        formatted = "\n\n".join(
            f"Q: {q}\nA: {a}" for q, a in qa_pairs
        )
        user_message = f"Please evaluate the effort in these form answers:\n\n{formatted}"

        try:
            raw = self._call_llm_with_system(user_message, _EFFORT_SYSTEM_PROMPT)
            result = json.loads(raw)
            logger.info(
                "Effort evaluation: low_effort=%s (confidence=%.0f%%) — %s",
                result.get("is_low_effort"),
                result.get("confidence", 0) * 100,
                result.get("reason", ""),
            )
            return result
        except Exception as exc:
            logger.warning("Effort evaluation failed: %s — defaulting to high-effort.", exc)
            return {"is_low_effort": False, "confidence": 0.0, "reason": f"Evaluation error: {exc}"}

    # ------------------------------------------------------------------
    # Provider dispatch
    # ------------------------------------------------------------------

    def _call_llm(self, user_message: str) -> str:
        return self._call_llm_with_system(user_message, self._system_prompt)

    def _call_llm_with_system(self, user_message: str, system_prompt: str) -> str:
        if self.config.llm_provider == "anthropic":
            return self._call_anthropic(user_message, system_prompt)
        return self._call_openai(user_message, system_prompt)

    def _call_anthropic(self, user_message: str, system_prompt: str) -> str:
        cfg = self.config
        response = self._client.messages.create(
            model=cfg.effective_llm_model,
            max_tokens=cfg.llm_max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text

    def _call_openai(self, user_message: str, system_prompt: str) -> str:
        cfg = self.config
        response = self._client.chat.completions.create(
            model=cfg.effective_llm_model,
            max_tokens=cfg.llm_max_tokens,
            temperature=cfg.llm_temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        return response.choices[0].message.content or ""
