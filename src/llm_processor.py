"""LLM reply generator — wraps Anthropic and OpenAI clients."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .config import Config
from .email_reader import IncomingEmail

logger = logging.getLogger("email_automation.llm")

DEFAULT_SYSTEM_PROMPT = """\
You are Paula, a warm and professional HR assistant at Flomad, responding to job applicants.
Your task is to write a genuine, personalised reply to an incoming applicant email.

Rules:
- Tone: warm, encouraging, and professional — never cold or robotic.
- Keep replies concise (under 200 words) unless more detail is genuinely needed.
- Start with a greeting on its own line: "Hi {first_name},"
  Use the first name from the "From:" line of the email. Never use an email address,
  domain name, or the word "Notifications" as a name.
- Acknowledge what the applicant said specifically (their background, answers, interests, etc.).
- Always include a clear next step (e.g., ask for 2–3 availability slots, explain what happens next).
- Do not make up details about the role, salary, or company that were not in the original email.
- Do NOT include any sign-off, closing line, or signature (e.g. no "Best regards", "Sincerely",
  "Warm regards", your name, etc.). The system appends the signature automatically.
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
    return (
        f"From: {sender_name} <{incoming.sender_address}>\n"
        f"Subject: {incoming.subject}\n"
        f"Date: {incoming.date}\n\n"
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

    def generate_reply(self, incoming: IncomingEmail) -> str:
        """Return the generated reply body text."""
        cfg = self.config
        user_message = _build_user_message(incoming)
        last_exc: Exception | None = None

        for attempt in range(1, cfg.retry_attempts + 1):
            try:
                reply = self._call_llm(user_message)
                logger.info("Reply generated for UID %s (attempt %d).", incoming.uid, attempt)
                return reply.strip()
            except Exception as exc:
                last_exc = exc
                logger.warning("LLM attempt %d/%d failed: %s", attempt, cfg.retry_attempts, exc)
                if attempt < cfg.retry_attempts:
                    time.sleep(cfg.retry_delay_seconds)

        raise RuntimeError(f"LLM failed after {cfg.retry_attempts} attempts.") from last_exc

    # ------------------------------------------------------------------
    # Provider dispatch
    # ------------------------------------------------------------------

    def _call_llm(self, user_message: str) -> str:
        if self.config.llm_provider == "anthropic":
            return self._call_anthropic(user_message)
        return self._call_openai(user_message)

    def _call_anthropic(self, user_message: str) -> str:
        cfg = self.config
        response = self._client.messages.create(
            model=cfg.effective_llm_model,
            max_tokens=cfg.llm_max_tokens,
            system=self._system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text

    def _call_openai(self, user_message: str) -> str:
        cfg = self.config
        response = self._client.chat.completions.create(
            model=cfg.effective_llm_model,
            max_tokens=cfg.llm_max_tokens,
            temperature=cfg.llm_temperature,
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        return response.choices[0].message.content or ""
