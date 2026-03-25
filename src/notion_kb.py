"""Notion Knowledge Base — fetches email templates and matches them to incoming emails."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from .config import Config

logger = logging.getLogger("email_automation.notion_kb")

NOTION_API_VERSION = "2022-06-28"
NOTION_DB_QUERY_URL = "https://api.notion.com/v1/databases/{db_id}/query"


@dataclass
class Template:
    name: str
    body: str
    tags: list[str]


class NotionKB:
    """Fetches and caches email templates from a Notion database."""

    def __init__(self, config: Config) -> None:
        self._api_token = config.notion_api_token
        self._database_id = config.notion_database_id
        self._templates: list[Template] = []
        self._load_templates()

    # ------------------------------------------------------------------
    # Template loading
    # ------------------------------------------------------------------

    def _load_templates(self) -> None:
        """Fetch all active templates from the Notion database."""
        if not self._api_token or not self._database_id:
            logger.warning("Notion API token or database ID not configured — KB disabled.")
            return

        headers = {
            "Authorization": f"Bearer {self._api_token}",
            "Notion-Version": NOTION_API_VERSION,
            "Content-Type": "application/json",
        }
        url = NOTION_DB_QUERY_URL.format(db_id=self._database_id)

        templates: list[Template] = []
        has_more = True
        start_cursor: str | None = None

        try:
            while has_more:
                payload: dict = {}
                if start_cursor:
                    payload["start_cursor"] = start_cursor

                resp = requests.post(url, headers=headers, json=payload, timeout=30)
                resp.raise_for_status()
                data = resp.json()

                for page in data.get("results", []):
                    props = page.get("properties", {})

                    # Template_Status — checkbox, must be True
                    status_prop = props.get("Template_Status", {})
                    if not status_prop.get("checkbox", False):
                        continue

                    # Template_Name — title
                    name_parts = props.get("Template_Name", {}).get("title", [])
                    name = "".join(part.get("plain_text", "") for part in name_parts).strip()
                    if not name:
                        continue

                    # Template body — always fetched from the page content (block
                    # children API), NOT from the Template_Body property.
                    # This avoids Notion's 2000-char rich_text limit and lets
                    # templates be maintained as full Notion pages.
                    page_id = page.get("id", "")
                    body = self._fetch_page_blocks(page_id, headers) if page_id else ""

                    if not body:
                        logger.warning(
                            "Template %r has no page content — skipping.", name
                        )
                        continue

                    # Template_Tags — multi_select
                    tags = [
                        tag.get("name", "")
                        for tag in props.get("Template_Tags", {}).get("multi_select", [])
                    ]

                    templates.append(Template(name=name, body=body, tags=tags))

                has_more = data.get("has_more", False)
                start_cursor = data.get("next_cursor")

            self._templates = templates
            logger.info("Loaded %d active templates from Notion KB.", len(templates))

        except Exception as exc:
            logger.error("Failed to load Notion KB templates: %s", exc)
            self._templates = []

    @staticmethod
    def _fetch_page_blocks(page_id: str, headers: dict) -> str:
        """Fetch the full text content of a Notion page via its block children.

        Reads the page body directly (no property size limits). Handles
        pagination so even very long pages are fully captured.
        Returns the concatenated plain text of all text blocks, or empty
        string on failure.
        """
        base_url = f"https://api.notion.com/v1/blocks/{page_id}/children"
        lines: list[str] = []
        has_more = True
        start_cursor: str | None = None

        try:
            while has_more:
                params = "page_size=100"
                if start_cursor:
                    params += f"&start_cursor={start_cursor}"
                resp = requests.get(f"{base_url}?{params}", headers=headers, timeout=30)
                resp.raise_for_status()
                data = resp.json()

                for block in data.get("results", []):
                    block_type = block.get("type", "")
                    block_data = block.get(block_type, {})
                    rich_text = block_data.get("rich_text", [])
                    text = "".join(part.get("plain_text", "") for part in rich_text)
                    if text:
                        lines.append(text)

                has_more = data.get("has_more", False)
                start_cursor = data.get("next_cursor")

            full_text = "\n".join(lines).strip()
            if full_text:
                logger.debug("Fetched %d chars from page blocks for %s", len(full_text), page_id)
            return full_text
        except Exception as exc:
            logger.warning("Failed to fetch page blocks for %s: %s", page_id, exc)
            return ""

    # ------------------------------------------------------------------
    # Template matching
    # ------------------------------------------------------------------

    def find_matching_template(
        self, email_subject: str, email_body: str, config: Config
    ) -> tuple[str | None, str | None]:
        """Use the LLM to find the best matching template for an incoming email.

        Returns (template_name, template_body) or (None, None) if no match.
        """
        if not self._templates:
            return None, None

        try:
            return self._match_via_llm(email_subject, email_body, config)
        except Exception as exc:
            logger.error("Template matching failed: %s — falling back to no match.", exc)
            return None, None

    def _match_via_llm(
        self, email_subject: str, email_body: str, config: Config
    ) -> tuple[str | None, str | None]:
        """Call the LLM to pick the best matching template."""
        # Build a compact summary of available templates
        summaries = []
        for i, t in enumerate(self._templates, 1):
            preview = t.body[:300].replace("\n", " ") if t.body else "(empty)"
            tags_str = ", ".join(t.tags) if t.tags else "none"
            summaries.append(f"{i}. {t.name} [tags: {tags_str}] — {preview}…")

        template_list = "\n".join(summaries)

        system_prompt = (
            "You are a template matcher. Given an incoming email and a list of response templates, "
            "determine which template (if any) is the best match for replying to this email.\n\n"
            "Rules:\n"
            "- Pick the ONE template that best addresses the topic/question in the email.\n"
            "- If no template is a good fit, respond with NONE.\n"
            "- Respond with ONLY the template number (e.g. '3') or 'NONE'. Nothing else."
        )

        # Pass the full email body (including thread history) — no truncation.
        # The LLM needs the complete conversation context for accurate matching.
        truncated_body = email_body if email_body else "(empty)"

        user_message = (
            f"== INCOMING EMAIL ==\n"
            f"Subject: {email_subject}\n"
            f"Body:\n{truncated_body}\n\n"
            f"== AVAILABLE TEMPLATES ==\n{template_list}\n\n"
            f"Which template number best matches this email? Reply with the number or NONE."
        )

        # Call the LLM
        if config.llm_provider == "anthropic":
            raw = self._call_anthropic(user_message, system_prompt, config)
        else:
            raw = self._call_openai(user_message, system_prompt, config)

        answer = raw.strip().upper()

        if answer == "NONE":
            logger.info("LLM template match: NONE")
            return None, None

        # Parse the number
        try:
            idx = int(answer.strip(".")) - 1
            if 0 <= idx < len(self._templates):
                matched = self._templates[idx]
                logger.info("LLM template match: #%d — %s", idx + 1, matched.name)
                return matched.name, matched.body
        except ValueError:
            pass

        logger.warning("LLM returned unexpected template match answer: %r", raw.strip())
        return None, None

    # ------------------------------------------------------------------
    # LLM calls (lightweight — reuse pattern from llm_processor)
    # ------------------------------------------------------------------

    @staticmethod
    def _call_anthropic(user_message: str, system_prompt: str, config: Config) -> str:
        import anthropic  # type: ignore
        client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        response = client.messages.create(
            model=config.effective_llm_model,
            max_tokens=16,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text

    @staticmethod
    def _call_openai(user_message: str, system_prompt: str, config: Config) -> str:
        import openai  # type: ignore
        client = openai.OpenAI(api_key=config.openai_api_key)
        model = config.effective_llm_model

        # GPT-5+ models require 'max_completion_tokens' instead of 'max_tokens'
        is_gpt5_plus = model.startswith("gpt-5") or model.startswith("o")
        token_param = "max_completion_tokens" if is_gpt5_plus else "max_tokens"

        kwargs = {
            "model": model,
            token_param: 16,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }

        response = client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""
