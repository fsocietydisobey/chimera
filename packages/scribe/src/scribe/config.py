"""Configuration for Meeting Scribe."""

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_OUTPUT_DIR = Path.home() / ".local" / "share" / "meeting-scribe"
DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_CHANNELS = 1


@dataclass
class Config:
    """Runtime configuration."""

    gemini_api_key: str = field(
        default_factory=lambda: os.environ.get("GOOGLE_AI_API_KEY", "")
    )
    gemini_model: str = "gemini-2.0-flash"
    output_dir: Path = DEFAULT_OUTPUT_DIR
    sample_rate: int = DEFAULT_SAMPLE_RATE
    channels: int = DEFAULT_CHANNELS
    record_system_audio: bool = True
    record_mic: bool = True

    def __post_init__(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)


def load_config() -> Config:
    """Load config from environment variables."""
    return Config(
        gemini_api_key=os.environ.get("GOOGLE_AI_API_KEY", ""),
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
        output_dir=Path(os.environ.get("SCRIBE_OUTPUT_DIR", DEFAULT_OUTPUT_DIR)),
    )
