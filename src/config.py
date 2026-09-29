from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: str = "ollama"
    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    default_model: str = "qwen2.5:7b"
    llm_api_key: str = "ollama"
    groq_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"

    qualify_threshold: int = 75
    nurture_threshold: int = 50

    scrape_timeout_seconds: float = 12.0
    scrape_max_bytes: int = 400_000
    # Only for local development against sites on your own network. Keep False in
    # any deployment: it disables the SSRF guard on the inbound-lead webhook.
    scrape_allow_private_hosts: bool = False
    user_agent: str = (
        "LeadQualificationAgent/1.0 (+https://github.com/lupenox/lead-qualification-agent)"
    )
    tavily_api_key: str = ""
    slack_webhook_url: str = ""
    database_path: Path = Path("data/leads.db")

    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    target_industries: list[str] = Field(
        default_factory=lambda: [
            "saas",
            "software",
            "logistics",
            "fintech",
            "healthcare",
            "professional services",
            "manufacturing",
            "cybersecurity",
        ]
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
