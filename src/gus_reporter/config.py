from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOTENV_PATH = REPO_ROOT / ".env"


def load_runtime_env(dotenv_path: Path | None = None) -> None:
    path = dotenv_path or DEFAULT_DOTENV_PATH
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


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
    tinyfish_max_total_jobs: int
    tinyfish_max_acquisition_waves: int
    tinyfish_retry_with_stealth: bool
    tinyfish_request_max_retries: int
    tinyfish_request_retry_backoff_seconds: float

    @classmethod
    def from_env(cls) -> "AppConfig":
        load_runtime_env()
        model = os.getenv("OPENAI_MODEL", "gpt-5-mini").strip() or "gpt-5-mini"
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
            tinyfish_batch_size=int(os.getenv("TINYFISH_BATCH_SIZE", "50")),
            tinyfish_max_total_jobs=int(
                os.getenv("TINYFISH_MAX_TOTAL_JOBS", "50")
            ),
            tinyfish_max_acquisition_waves=int(
                os.getenv("TINYFISH_MAX_ACQUISITION_WAVES", "6")
            ),
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


load_runtime_env()
