from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _env_flag(name: str, default: str) -> bool:
    value = os.getenv(name, default).strip().lower()
    return value in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AppConfig:
    openai_api_key: str
    tinyfish_api_key: str
    openai_base_url: str
    openai_model: str
    tinyfish_base_url: str
    report_output_dir: Path
    tinyfish_default_browser_profile: str
    tinyfish_poll_interval_seconds: int
    tinyfish_poll_timeout_seconds: int
    tinyfish_batch_size: int
    tinyfish_retry_with_stealth: bool
    tinyfish_request_max_retries: int
    tinyfish_request_retry_backoff_seconds: float

    @classmethod
    def from_env(cls) -> "AppConfig":
        model = os.getenv("OPENAI_MODEL", "gpt-5.4").strip() or "gpt-5.4"
        if model != "gpt-5.4":
            raise RuntimeError(
                "OPENAI_MODEL must remain gpt-5.4 because this project is "
                "configured for gpt-5.4 only."
            )
        return cls(
            openai_api_key=_require_env("OPENAI_API_KEY"),
            tinyfish_api_key=_require_env("TINYFISH_API_KEY"),
            openai_base_url=os.getenv(
                "OPENAI_BASE_URL", "https://api.openai.com/v1"
            ).rstrip("/"),
            openai_model=model,
            tinyfish_base_url=os.getenv(
                "TINYFISH_BASE_URL", "https://agent.tinyfish.ai/v1"
            ).rstrip("/"),
            report_output_dir=Path(
                os.getenv("REPORT_OUTPUT_DIR", "./runs")
            ).expanduser(),
            tinyfish_default_browser_profile=os.getenv(
                "TINYFISH_DEFAULT_BROWSER_PROFILE", "lite"
            ).strip()
            or "lite",
            tinyfish_poll_interval_seconds=int(
                os.getenv("TINYFISH_POLL_INTERVAL_SECONDS", "5")
            ),
            tinyfish_poll_timeout_seconds=int(
                os.getenv("TINYFISH_POLL_TIMEOUT_SECONDS", "600")
            ),
            tinyfish_batch_size=int(os.getenv("TINYFISH_BATCH_SIZE", "20")),
            tinyfish_retry_with_stealth=_env_flag(
                "TINYFISH_RETRY_WITH_STEALTH", "true"
            ),
            tinyfish_request_max_retries=int(
                os.getenv("TINYFISH_REQUEST_MAX_RETRIES", "3")
            ),
            tinyfish_request_retry_backoff_seconds=float(
                os.getenv("TINYFISH_REQUEST_RETRY_BACKOFF_SECONDS", "2.0")
            ),
        )
