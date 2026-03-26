"""Configuration loaded from environment variables."""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # --- Email IMAP (reading) ---
    imap_host: str = field(default_factory=lambda: os.getenv("IMAP_HOST", "imap.gmail.com"))
    imap_port: int = field(default_factory=lambda: int(os.getenv("IMAP_PORT", "993")))
    imap_use_ssl: bool = field(default_factory=lambda: os.getenv("IMAP_USE_SSL", "true").lower() == "true")
    email_address: str = field(default_factory=lambda: os.getenv("EMAIL_ADDRESS", ""))
    email_password: str = field(default_factory=lambda: os.getenv("EMAIL_PASSWORD", ""))
    imap_mailbox: str = field(default_factory=lambda: os.getenv("IMAP_MAILBOX", "INBOX"))

    # --- Email SMTP (sending) ---
    smtp_host: str = field(default_factory=lambda: os.getenv("SMTP_HOST", "smtp.gmail.com"))
    smtp_port: int = field(default_factory=lambda: int(os.getenv("SMTP_PORT", "587")))
    smtp_use_tls: bool = field(default_factory=lambda: os.getenv("SMTP_USE_TLS", "true").lower() == "true")
    # Default outbound From address. Falls back to email_address (IMAP login) if not set.
    # This should be set to the Gmail "Send mail as" default (e.g. hr@flolabsinnovations.com).
    from_email: str = field(default_factory=lambda: os.getenv("FROM_EMAIL", ""))
    # Second from-address for Form 2 / post-orientation emails (hr@flolabsrd.com).
    # Falls back to from_email / email_address if not set.
    from_email_rd: str = field(default_factory=lambda: os.getenv("FROM_EMAIL_RD", ""))

    # --- LLM ---
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "anthropic"))  # "anthropic" | "openai"
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", ""))  # empty → use provider default
    llm_max_tokens: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_TOKENS", "1024")))
    llm_temperature: float = field(default_factory=lambda: float(os.getenv("LLM_TEMPERATURE", "0.7")))

    # --- Behavior ---
    # How to handle replies: "auto" (send immediately) | "queue" (write to outbox/ folder for review)
    reply_mode: str = field(default_factory=lambda: os.getenv("REPLY_MODE", "queue"))
    # Sender name shown in outgoing emails
    sender_name: str = field(default_factory=lambda: os.getenv("SENDER_NAME", ""))
    # Max emails to process per run (0 = unlimited)
    max_emails: int = field(default_factory=lambda: int(os.getenv("MAX_EMAILS", "10")))
    # Mark processed emails as read in IMAP
    mark_as_read: bool = field(default_factory=lambda: os.getenv("MARK_AS_READ", "true").lower() == "true")
    # Retry attempts for transient failures
    retry_attempts: int = field(default_factory=lambda: int(os.getenv("RETRY_ATTEMPTS", "3")))
    retry_delay_seconds: float = field(default_factory=lambda: float(os.getenv("RETRY_DELAY_SECONDS", "2.0")))

    # --- Prompt customisation ---
    # Path to a plain-text file containing the system prompt. Falls back to built-in default.
    system_prompt_file: str = field(default_factory=lambda: os.getenv("SYSTEM_PROMPT_FILE", ""))

    # --- Gmail API (funnel labels) ---
    gmail_credentials_file: str = field(default_factory=lambda: os.getenv("GMAIL_CREDENTIALS_FILE", "credentials.json"))
    gmail_token_file: str = field(default_factory=lambda: os.getenv("GMAIL_TOKEN_FILE", "token.json"))
    gmail_labels_enabled: bool = field(default_factory=lambda: os.getenv("GMAIL_LABELS_ENABLED", "true").lower() == "true")

    # --- Notion Knowledge Base ---
    notion_api_token: str = field(default_factory=lambda: os.getenv("NOTION_API_TOKEN", ""))
    notion_database_id: str = field(default_factory=lambda: os.getenv("NOTION_DATABASE_ID", ""))
    notion_kb_enabled: bool = field(default_factory=lambda: os.getenv("NOTION_KB_ENABLED", "true").lower() == "true")

    # --- Discord Webhook ---
    discord_webhook_url: str = field(default_factory=lambda: os.getenv("DISCORD_WEBHOOK_URL", ""))

    # --- Logging ---
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    log_file: str = field(default_factory=lambda: os.getenv("LOG_FILE", ""))

    def validate(self) -> None:
        """Raise ValueError for obviously wrong configuration."""
        if not self.email_address:
            raise ValueError("EMAIL_ADDRESS is required.")
        if not self.email_password:
            raise ValueError("EMAIL_PASSWORD is required.")
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic.")
        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai.")
        if self.reply_mode not in ("auto", "queue"):
            raise ValueError("REPLY_MODE must be 'auto' or 'queue'.")

    @property
    def effective_llm_model(self) -> str:
        if self.llm_model:
            return self.llm_model
        return (
            "claude-sonnet-4-6" if self.llm_provider == "anthropic" else "gpt-5.4-nano"
        )
