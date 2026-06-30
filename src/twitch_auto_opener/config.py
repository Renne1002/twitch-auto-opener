from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

try:
    import tomllib
except ModuleNotFoundError as exc:  # pragma: no cover
    raise RuntimeError("Python 3.12+ is required (tomllib is unavailable)") from exc


class TwitchApiConfig(BaseModel):
    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)


YOUTUBE_PRIVACY_STATUSES = ("private", "unlisted", "public")
_TEMPLATE_PLACEHOLDER_PATTERN = re.compile(r"\{([^{}]+)\}")


def _validate_title_template(value: str) -> str:
    unknown_fields: list[str] = []
    for raw in _TEMPLATE_PLACEHOLDER_PATTERN.findall(value):
        if raw == "id":
            continue
        if raw.startswith("ts:") and len(raw) > 3:
            continue
        unknown_fields.append(raw)

    if unknown_fields:
        labels = ", ".join(sorted(set(unknown_fields)))
        raise ValueError(
            f"youtube title_template includes unknown placeholder(s): {labels}; "
            "allowed placeholders are {id} and {ts:%Y-%m-%d %H:%M}"
        )
    return value


class StreamerYoutubeDefaultConfig(BaseModel):
    enabled: bool = False
    title_template: str | None = None
    privacy_status: Literal["private", "unlisted", "public"] | None = None
    delete_ts_after_upload: bool | None = None

    @field_validator("title_template")
    @classmethod
    def validate_title_template(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _validate_title_template(value)


class StreamerYoutubeOverrideConfig(BaseModel):
    enabled: bool | None = None
    title_template: str | None = None
    privacy_status: Literal["private", "unlisted", "public"] | None = None
    delete_ts_after_upload: bool | None = None

    @field_validator("title_template")
    @classmethod
    def validate_title_template(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _validate_title_template(value)


class StreamerFlagsConfig(BaseModel):
    auto_open: bool = True
    record: bool = False
    auto_srt: bool = False
    youtube: StreamerYoutubeDefaultConfig = Field(default_factory=StreamerYoutubeDefaultConfig)


class StreamerOverrideConfig(BaseModel):
    auto_open: bool | None = None
    record: bool | None = None
    auto_srt: bool | None = None
    youtube: StreamerYoutubeOverrideConfig | None = None


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


class FastWhisperConfig(BaseModel):
    fast_whisper_path: str = Field(default="faster-whisper", min_length=1)
    model: str = Field(default="base", min_length=1)
    device: Literal["cpu", "cuda"] = "cpu"
    language: str = ""
    threads: int = Field(default=0, ge=0, le=128)
    max_line_width: int = Field(default=100, ge=1, le=500)
    retry_max_failures: int = Field(default=3, ge=1, le=20)
    retry_delay_seconds: int = Field(default=2, ge=0, le=300)


class ChatConfig(BaseModel):
    enabled: bool = False
    capture_moderation_events: bool = True
    reconnect_delay_seconds: int = Field(default=5, ge=1, le=300)
    connect_timeout_seconds: int = Field(default=15, ge=3, le=300)
    read_timeout_seconds: int = Field(default=120, ge=10, le=3600)
    debug: bool = False


class RecordingConfig(BaseModel):
    output_dir: str | None = None
    quality: str = Field(default="best", min_length=1)
    convert_to_mp4: bool = True
    retry_delay_seconds: int = Field(default=10, ge=3, le=300)
    tools: RecordingToolsConfig = Field(default_factory=RecordingToolsConfig)
    fastwhisper: FastWhisperConfig = Field(default_factory=FastWhisperConfig)
    chat: ChatConfig = Field(default_factory=ChatConfig)


class YoutubeAuthConfig(BaseModel):
    client_secrets_file: str = Field(default="", min_length=0)
    token_file: str = Field(default="", min_length=0)


class YoutubeDefaultsConfig(BaseModel):
    privacy_status: Literal["private", "unlisted", "public"] = "unlisted"
    title_template: str = Field(default="【Twitch】{id} {ts:%Y-%m-%d %H:%M}", min_length=1)
    category_id: str = Field(default="20", min_length=1)
    made_for_kids: bool = False
    delete_ts_after_upload: bool = False

    @field_validator("title_template")
    @classmethod
    def validate_title_template(cls, value: str) -> str:
        return _validate_title_template(value)


class YoutubeQuotaConfig(BaseModel):
    quota_reset_timezone: str = Field(default="America/Los_Angeles", min_length=1)
    skip_after_quota_exceeded_for_day: bool = True


class YoutubeConfig(BaseModel):
    enabled: bool = True
    min_age_days: int = Field(default=7, ge=2)
    tick_interval_seconds: int = Field(default=300, ge=30)
    state_file: str = Field(default="./VOD/.youtube_upload_state.json", min_length=1)
    history_file: str = Field(default="./VOD/.youtube_upload_history.jsonl", min_length=1)
    max_uploads_per_tick: int = Field(default=1, ge=1, le=20)
    auth: YoutubeAuthConfig = Field(default_factory=YoutubeAuthConfig)
    defaults: YoutubeDefaultsConfig = Field(default_factory=YoutubeDefaultsConfig)
    quota: YoutubeQuotaConfig = Field(default_factory=YoutubeQuotaConfig)

    @field_validator("state_file", "history_file")
    @classmethod
    def validate_path_like(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("path must be non-empty")
        return value


class StartupConfig(BaseModel):
    enabled: bool = False


class AppConfig(BaseModel):
    twitch_api: TwitchApiConfig
    streamer_default_config: StreamerFlagsConfig = Field(default_factory=StreamerFlagsConfig)
    streamer_configs: dict[str, StreamerOverrideConfig] = Field(min_length=1)
    chrome: ChromeConfig
    monitor: MonitorConfig = Field(default_factory=MonitorConfig)
    recording: RecordingConfig = Field(default_factory=RecordingConfig)
    youtube: YoutubeConfig = Field(default_factory=YoutubeConfig)
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

    @model_validator(mode="after")
    def validate_youtube_auth_settings(self) -> "AppConfig":
        if not self.youtube.enabled:
            return self

        any_streamer_enabled = any(
            (
                override.youtube.enabled
                if override.youtube and override.youtube.enabled is not None
                else self.streamer_default_config.youtube.enabled
            )
            for override in self.streamer_configs.values()
        )

        if any_streamer_enabled and (
            not self.youtube.auth.client_secrets_file.strip()
            or not self.youtube.auth.token_file.strip()
        ):
            raise ValueError(
                "youtube.auth.client_secrets_file and youtube.auth.token_file are required "
                "when youtube upload is enabled for any streamer"
            )
        return self

    @property
    def streamers(self) -> list["StreamerConfig"]:
        defaults = self.streamer_default_config
        global_youtube_defaults = self.youtube.defaults

        def effective_youtube_enabled(override: StreamerOverrideConfig) -> bool:
            if override.youtube and override.youtube.enabled is not None:
                return override.youtube.enabled
            return defaults.youtube.enabled

        def effective_youtube_title_template(override: StreamerOverrideConfig) -> str:
            if override.youtube and override.youtube.title_template is not None:
                return override.youtube.title_template
            if defaults.youtube.title_template is not None:
                return defaults.youtube.title_template
            return global_youtube_defaults.title_template

        def effective_youtube_privacy_status(
            override: StreamerOverrideConfig,
        ) -> Literal["private", "unlisted", "public"]:
            if override.youtube and override.youtube.privacy_status is not None:
                return override.youtube.privacy_status
            if defaults.youtube.privacy_status is not None:
                return defaults.youtube.privacy_status
            return global_youtube_defaults.privacy_status

        def effective_delete_ts_after_upload(override: StreamerOverrideConfig) -> bool:
            if override.youtube and override.youtube.delete_ts_after_upload is not None:
                return override.youtube.delete_ts_after_upload
            if defaults.youtube.delete_ts_after_upload is not None:
                return defaults.youtube.delete_ts_after_upload
            return global_youtube_defaults.delete_ts_after_upload

        return [
            StreamerConfig(
                login=login,
                auto_open=(
                    override.auto_open if override.auto_open is not None else defaults.auto_open
                ),
                record=(override.record if override.record is not None else defaults.record),
                auto_srt=(
                    override.auto_srt if override.auto_srt is not None else defaults.auto_srt
                ),
                youtube_enabled=effective_youtube_enabled(override),
                youtube_title_template=effective_youtube_title_template(override),
                youtube_privacy_status=effective_youtube_privacy_status(override),
                youtube_delete_ts_after_upload=effective_delete_ts_after_upload(override),
            )
            for login, override in self.streamer_configs.items()
        ]


class StreamerConfig(BaseModel):
    login: str = Field(min_length=1)
    auto_open: bool = True
    record: bool = False
    auto_srt: bool = False
    youtube_enabled: bool = False
    youtube_title_template: str = Field(default="【Twitch】{id} {ts:%Y-%m-%d %H:%M}", min_length=1)
    youtube_privacy_status: Literal["private", "unlisted", "public"] = "unlisted"
    youtube_delete_ts_after_upload: bool = False

    @field_validator("login")
    @classmethod
    def normalize_login(cls, value: str) -> str:
        login = value.strip().lower()
        if not login:
            raise ValueError("streamer login must be non-empty")
        return login

    @field_validator("youtube_title_template")
    @classmethod
    def validate_youtube_title_template(cls, value: str) -> str:
        return _validate_title_template(value)


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
