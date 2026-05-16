from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

try:
    import tomllib
except ModuleNotFoundError as exc:  # pragma: no cover
    raise RuntimeError("Python 3.12+ is required (tomllib is unavailable)") from exc


class AppConfig(BaseModel):
    twitch_client_id: str = Field(min_length=1)
    twitch_client_secret: str = Field(min_length=1)
    streamer_logins: list[str] = Field(min_length=1)
    chrome_profile_email: str = Field(min_length=3)
    check_interval_seconds: int = Field(default=30, ge=1, le=300)
    debug: bool = False
    chrome_path: str | None = None
    chrome_user_data_dir: str | None = None
    record_vod_enabled: bool = True
    vod_output_dir: str | None = None
    record_quality: str = Field(default="best", min_length=1)
    streamlink_path: str = Field(default="streamlink", min_length=1)
    ffmpeg_path: str = Field(default="ffmpeg", min_length=1)
    convert_record_to_mp4: bool = True
    record_retry_delay_seconds: int = Field(default=10, ge=3, le=300)

    @field_validator("streamer_logins")
    @classmethod
    def normalize_streamer_logins(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            item = raw.strip().lower()
            if not item:
                continue
            if item in seen:
                continue
            seen.add(item)
            normalized.append(item)

        if not normalized:
            raise ValueError("streamer_logins must contain at least one non-empty login")
        return normalized


def load_config(config_path: str | Path = "config.toml") -> AppConfig:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")

    with path.open("rb") as fp:
        data: dict[str, Any] = tomllib.load(fp)

    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"invalid config.toml: {exc}") from exc
