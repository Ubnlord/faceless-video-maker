"""Environment-driven configuration.

Every value has a sensible default so the app runs with zero configuration.
Settings are read from environment variables (and a local ``.env`` file if one
is present) at import time.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _load_dotenv() -> None:
    """Best-effort load of a local .env file (no external dependency)."""
    path = Path(".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


class Settings:
    """All tunables, read once at import time."""

    # --- script generation ---
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_base_url: str = os.getenv(
        "OPENAI_BASE_URL", "https://api.openai.com/v1"
    ).rstrip("/")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    llm_timeout: int = _env_int("FVM_LLM_TIMEOUT", 60)

    # --- voice ---
    default_voice: str = os.getenv("FVM_DEFAULT_VOICE", "en-US-AndrewNeural")
    tts_rate: str = os.getenv("FVM_TTS_RATE", "+8%")

    # --- render ---
    width: int = _env_int("FVM_WIDTH", 1080)
    height: int = _env_int("FVM_HEIGHT", 1920)
    fps: int = _env_int("FVM_FPS", 30)
    crf: int = _env_int("FVM_CRF", 20)
    preset: str = os.getenv("FVM_PRESET", "veryfast")
    max_workers: int = _env_int("FVM_MAX_WORKERS", 1)

    # --- storage ---
    data_dir: Path = Path(os.getenv("FVM_DATA_DIR", "data"))
    output_dir: Path = Path(os.getenv("FVM_OUTPUT_DIR", "output"))

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openai_api_key.strip())

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"


settings = Settings()
