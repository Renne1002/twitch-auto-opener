from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from twitch_auto_opener.config import AppConfig, StreamerConfig
from twitch_auto_opener.upload_history import UploadHistoryWriter
from twitch_auto_opener.upload_queue import UploadQueueScanner
from twitch_auto_opener.upload_state import UploadStateStore
from twitch_auto_opener.youtube_template import TitleTemplateError, TitleTemplateRenderer

_YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"


class YoutubeUploader:
    def __init__(self, config: AppConfig, output_dir: Path, app_base_dir: Path) -> None:
        self._enabled = config.youtube.enabled
        self._tick_interval_seconds = config.youtube.tick_interval_seconds
        self._max_uploads_per_tick = config.youtube.max_uploads_per_tick
        self._defaults = config.youtube.defaults
        self._quota_config = config.youtube.quota
        self._output_dir = output_dir
        self._scanner = UploadQueueScanner(output_dir=output_dir, min_age_days=config.youtube.min_age_days)
        self._last_tick_at_utc: datetime | None = None

        self._client_secrets_file = self._resolve_path(config.youtube.auth.client_secrets_file, app_base_dir)
        self._token_file = self._resolve_path(config.youtube.auth.token_file, app_base_dir)
        self._state = UploadStateStore(
            self._resolve_path(config.youtube.state_file, app_base_dir, fallback=output_dir / ".youtube_upload_state.json")
        )
        self._history = UploadHistoryWriter(
            self._resolve_path(
                config.youtube.history_file,
                app_base_dir,
                fallback=output_dir / ".youtube_upload_history.jsonl",
            )
        )

    @staticmethod
    def _resolve_path(raw_path: str, app_base_dir: Path, fallback: Path | None = None) -> Path:
        value = raw_path.strip()
        if not value:
            if fallback is None:
                return app_base_dir
            return fallback

        candidate = Path(value.replace("\\", "/")).expanduser()
        if not candidate.is_absolute():
            candidate = app_base_dir / candidate
        return candidate.resolve()

    def _should_skip_tick(self, now_utc: datetime) -> bool:
        if not self._enabled:
            return True

        if self._last_tick_at_utc is None:
            return False

        elapsed = (now_utc - self._last_tick_at_utc).total_seconds()
        return elapsed < self._tick_interval_seconds

    @staticmethod
    def _next_quota_reset_utc(timezone_name: str) -> datetime:
        try:
            tz = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("America/Los_Angeles")

        local_now = datetime.now(tz)
        next_midnight = (local_now + timedelta(days=1)).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        return next_midnight.astimezone(UTC)

    def _load_credentials(self) -> Credentials:
        if not self._token_file.exists():
            raise RuntimeError(
                "youtube token file not found; run 'python -m twitch_auto_opener.youtube_auth --config config.toml' first"
            )

        credentials = Credentials.from_authorized_user_file(str(self._token_file), [_YOUTUBE_UPLOAD_SCOPE])
        if credentials.valid:
            return credentials

        if not credentials.refresh_token:
            raise RuntimeError("youtube token has no refresh_token; re-run youtube_auth")

        credentials.refresh(Request())
        self._token_file.parent.mkdir(parents=True, exist_ok=True)
        with self._token_file.open("w", encoding="utf-8") as fp:
            fp.write(credentials.to_json())
        return credentials

    def _build_youtube_service(self):
        creds = self._load_credentials()
        return build("youtube", "v3", credentials=creds, cache_discovery=False)

    @staticmethod
    def _parse_http_error(exc: HttpError) -> tuple[str, str]:
        status = str(exc.status_code)
        try:
            payload = json.loads(exc.content.decode("utf-8"))
            errors = payload.get("error", {}).get("errors", [])
            if errors:
                reason = str(errors[0].get("reason", "unknown"))
            else:
                reason = str(payload.get("error", {}).get("message", "unknown"))
            return status, reason
        except Exception:
            return status, "unknown"

    def _upload_file(
        self,
        *,
        service,
        file_path: Path,
        title: str,
        privacy_status: str,
    ) -> str:
        body = {
            "snippet": {
                "title": title,
                "categoryId": self._defaults.category_id,
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": self._defaults.made_for_kids,
            },
        }

        media = MediaFileUpload(str(file_path), chunksize=-1, resumable=True, mimetype="video/mp2t")
        request = service.videos().insert(part="snippet,status", body=body, media_body=media)
        response = request.execute(num_retries=3)
        video_id = str(response.get("id", "")).strip()
        if not video_id:
            raise RuntimeError("youtube upload response has no video id")
        return video_id

    def tick(self, streamers_by_user_id: dict[str, StreamerConfig]) -> None:
        now_utc = datetime.now(UTC)
        if self._should_skip_tick(now_utc):
            return
        self._last_tick_at_utc = now_utc

        self._state.load()
        if self._state.is_quota_blocked(now_utc) and self._quota_config.skip_after_quota_exceeded_for_day:
            self._state.save()
            return

        if self._state.is_auth_backoff_active(now_utc):
            self._state.save()
            return

        try:
            service = self._build_youtube_service()
            self._state.clear_auth_backoff()
        except Exception as exc:
            print(f"[warn] youtube auth failed: {exc}")
            self._state.set_auth_backoff(seconds=300)
            self._state.save()
            return

        upload_count = 0
        candidates = self._scanner.scan(streamers_by_user_id=streamers_by_user_id, now_utc=now_utc)
        for candidate in candidates:
            if upload_count >= self._max_uploads_per_tick:
                break

            streamer = streamers_by_user_id.get(candidate.user_id)
            if streamer is None or not streamer.youtube_enabled:
                continue

            file_key = UploadStateStore.build_file_key(
                candidate.file_path,
                candidate.file_size,
                candidate.mtime_ns,
            )
            if self._state.was_uploaded(file_key):
                continue
            if not self._state.should_retry_failed(file_key, now_utc):
                continue

            try:
                title = TitleTemplateRenderer.render(
                    streamer.youtube_title_template,
                    user_id=candidate.user_id,
                    captured_at=candidate.captured_at_utc,
                )
                video_id = self._upload_file(
                    service=service,
                    file_path=candidate.file_path,
                    title=title,
                    privacy_status=streamer.youtube_privacy_status,
                )
                self._state.mark_uploaded(file_key, video_id=video_id)
                self._history.upload_succeeded(
                    user_id=candidate.user_id,
                    login=candidate.login,
                    file_path=candidate.file_path,
                    video_id=video_id,
                    privacy_status=streamer.youtube_privacy_status,
                    title=title,
                )
                print(
                    f"[info] youtube upload succeeded: {candidate.file_path.name} -> video_id={video_id}"
                )

                if streamer.youtube_delete_ts_after_upload:
                    try:
                        candidate.file_path.unlink()
                    except OSError as exc:
                        self._history.delete_failed(
                            user_id=candidate.user_id,
                            login=candidate.login,
                            file_path=candidate.file_path,
                            error_reason=str(exc),
                        )
                        print(f"[warn] failed to delete uploaded ts file: {candidate.file_path} ({exc})")
                    else:
                        self._history.delete_succeeded(
                            user_id=candidate.user_id,
                            login=candidate.login,
                            file_path=candidate.file_path,
                        )

                upload_count += 1
            except TitleTemplateError as exc:
                error_code, error_reason = "invalid_template", str(exc)
                self._state.mark_failed(file_key, error_code=error_code, error_reason=error_reason)
                self._history.upload_failed(
                    user_id=candidate.user_id,
                    login=candidate.login,
                    file_path=candidate.file_path,
                    error_code=error_code,
                    error_reason=error_reason,
                )
                print(f"[warn] youtube title template error: {exc}")
            except HttpError as exc:
                error_code, error_reason = self._parse_http_error(exc)
                if error_reason in {"quotaExceeded", "dailyLimitExceeded"}:
                    blocked_until = self._next_quota_reset_utc(self._quota_config.quota_reset_timezone)
                    self._state.set_quota_blocked_until(blocked_until)
                    self._history.upload_skipped_quota_block(
                        user_id=candidate.user_id,
                        login=candidate.login,
                        file_path=candidate.file_path,
                        quota_blocked_until=blocked_until.isoformat().replace("+00:00", "Z"),
                    )
                    print(
                        "[warn] youtube quota exceeded; skipping uploads until "
                        f"{blocked_until.isoformat().replace('+00:00', 'Z')}"
                    )
                    break

                self._state.mark_failed(file_key, error_code=error_code, error_reason=error_reason)
                self._history.upload_failed(
                    user_id=candidate.user_id,
                    login=candidate.login,
                    file_path=candidate.file_path,
                    error_code=error_code,
                    error_reason=error_reason,
                )
                print(
                    "[warn] youtube upload failed: "
                    f"{candidate.file_path.name} code={error_code} reason={error_reason}"
                )
            except Exception as exc:
                self._state.mark_failed(file_key, error_code="upload_error", error_reason=str(exc))
                self._history.upload_failed(
                    user_id=candidate.user_id,
                    login=candidate.login,
                    file_path=candidate.file_path,
                    error_code="upload_error",
                    error_reason=str(exc),
                )
                print(f"[warn] youtube upload failed unexpectedly: {candidate.file_path.name} ({exc})")

        self._state.save()
