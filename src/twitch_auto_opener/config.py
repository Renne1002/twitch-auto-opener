from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

try:
    import tomllib
except ModuleNotFoundError as exc:  # pragma: no cover
    raise RuntimeError("Python 3.12+ is required (tomllib is unavailable)") from exc


class TwitchApiConfig(BaseModel):
    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)


class StreamerFlagsConfig(BaseModel):
    auto_open: bool = True
    record: bool = False


class StreamerOverrideConfig(BaseModel):
    auto_open: bool | None = None
    record: bool | None = None


class ChromeConfig(BaseModel):
    profile_email: str = Field(min_length=3)
    path: str | None = None
    user_data_dir: str | None = None


class MonitorConfig(BaseModel):
    check_interval_seconds: int = Field(default=30, ge=1, le=300)
    debug: bool = False


class RecordingToolsConfig(BaseModel):
    streamlink_path: str = Field(default="streamlink", min_length=1)
    ffmpeg_path: str = Field(default="ffmpeg", min_length=1)


class RecordingConfig(BaseModel):
    output_dir: str | None = None
    quality: str = Field(default="best", min_length=1)
    convert_to_mp4: bool = True
    retry_delay_seconds: int = Field(default=10, ge=3, le=300)
    tools: RecordingToolsConfig = Field(default_factory=RecordingToolsConfig)


class StartupConfig(BaseModel):
    enabled: bool = False


class AppConfig(BaseModel):
    twitch_api: TwitchApiConfig
    streamer_default_config: StreamerFlagsConfig = Field(default_factory=StreamerFlagsConfig)
    streamer_configs: dict[str, StreamerOverrideConfig] = Field(min_length=1)
    chrome: ChromeConfig
    monitor: MonitorConfig = Field(default_factory=MonitorConfig)
    recording: RecordingConfig = Field(default_factory=RecordingConfig)
    startup: StartupConfig = Field(default_factory=StartupConfig)

    @field_validator("streamer_configs", mode="before")
    @classmethod
    def normalize_streamer_config_keys(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        normalized: dict[str, Any] = {}
        for raw_login, raw_config in value.items():
            login = str(raw_login).strip().lower()
            if not login:
                raise ValueError("streamer_configs contains empty login key")
            if login in normalized:
                raise ValueError(f"streamer_configs contains duplicated login: {login}")
            normalized[login] = raw_config
        return normalized

    @field_validator("streamer_configs")
    @classmethod
    def validate_streamer_configs(
        cls, value: dict[str, StreamerOverrideConfig]
    ) -> dict[str, StreamerOverrideConfig]:
        if not value:
            raise ValueError("streamer_configs must contain at least one streamer")
        return value

    @property
    def streamers(self) -> list["StreamerConfig"]:
        defaults = self.streamer_default_config
        return [
            StreamerConfig(
                login=login,
                auto_open=(
                    override.auto_open if override.auto_open is not None else defaults.auto_open
                ),
                record=(override.record if override.record is not None else defaults.record),
            )
            for login, override in self.streamer_configs.items()
        ]


class StreamerConfig(BaseModel):
    login: str = Field(min_length=1)
    auto_open: bool = True
    record: bool = False

    @field_validator("login")
    @classmethod
    def normalize_login(cls, value: str) -> str:
        login = value.strip().lower()
        if not login:
            raise ValueError("streamer login must be non-empty")
        return login


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
