from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from twitch_auto_opener.config import AppConfig, StreamerConfig
from twitch_auto_opener.upload_history import UploadHistoryWriter
from twitch_auto_opener.upload_queue import UploadCandidate, UploadQueueScanner
from twitch_auto_opener.upload_state import UploadStateStore
from twitch_auto_opener.youtube_template import TitleTemplateError, TitleTemplateRenderer

_YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
_DURATION_PATTERN = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_MAX_PART_DURATION_SECONDS = 10 * 60 * 60
_PROCESSING_POLL_INTERVAL_SECONDS = 60
_PROCESSING_POLL_TIMEOUT_SECONDS = 12 * 60 * 60


class VideoProcessingError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class YoutubeUploader:
    def __init__(
        self,
        config: AppConfig,
        output_dir: Path,
        app_base_dir: Path,
        config_base_dir: Path,
    ) -> None:
        self._enabled = config.youtube.enabled
        self._tick_interval_seconds = config.youtube.tick_interval_seconds
        self._max_uploads_per_tick = config.youtube.max_uploads_per_tick
        self._defaults = config.youtube.defaults
        self._quota_config = config.youtube.quota
        self._ffmpeg_path = config.recording.tools.ffmpeg_path
        self._output_dir = output_dir
        self._scanner = UploadQueueScanner(
            output_dir=output_dir,
            min_age_days=config.youtube.min_age_days,
        )
        self._last_tick_at_utc: datetime | None = None

        self._client_secrets_file = self._resolve_path(
            config.youtube.auth.client_secrets_file,
            config_base_dir,
        )
        self._token_file = self._resolve_path(
            config.youtube.auth.token_file,
            config_base_dir,
        )
        self._state = UploadStateStore(
            self._resolve_path(
                config.youtube.state_file,
                app_base_dir,
                fallback=output_dir / ".youtube_upload_state.json",
            )
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

        credentials = Credentials.from_authorized_user_file(
            str(self._token_file), _YOUTUBE_SCOPES
        )
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
        status_code = getattr(exc, "status_code", None)
        if status_code is None:
            status_code = getattr(getattr(exc, "resp", None), "status", "unknown")
        status = str(status_code)
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

    @staticmethod
    def _is_rate_limit_error(error_code: str, error_reason: str) -> bool:
        reason = error_reason.lower()
        if error_code == "429":
            return True
        if "ratelimit" in reason:
            return True
        return error_reason in {
            "rateLimitExceeded",
            "userRateLimitExceeded",
            "uploadRateLimitExceeded",
        }

    @staticmethod
    def _rate_limit_backoff_seconds(exc: HttpError) -> int:
        try:
            retry_after = exc.resp.get("retry-after")
            if retry_after is not None:
                return max(60, int(str(retry_after).strip()))
        except Exception:
            pass
        return 900

    @staticmethod
    def _is_video_too_long_reason(reason: str) -> bool:
        normalized = reason.strip().lower()
        return normalized in {
            "videotoolong",
            "toolong",
            "videotoolong",
        } or "too long" in normalized

    @staticmethod
    def _is_insufficient_permission_reason(reason: str) -> bool:
        return reason.strip().lower() in {"insufficientpermissions", "insufficient_permissions"}

    def _probe_duration_seconds(self, file_path: Path) -> float | None:
        command = [self._ffmpeg_path, "-hide_banner", "-i", str(file_path)]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            print(f"[warn] ffmpeg not found: {self._ffmpeg_path}; skip long-video pre-split")
            return None
        except Exception as exc:
            print(f"[warn] ffmpeg probe failed for {file_path.name}: {exc}")
            return None

        probe_text = (completed.stderr or "") + "\n" + (completed.stdout or "")
        match = _DURATION_PATTERN.search(probe_text)
        if not match:
            return None

        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = float(match.group(3))
        return hours * 3600 + minutes * 60 + seconds

    def _build_part_directory(self, file_path: Path, mtime_ns: int) -> Path:
        return file_path.parent / ".youtube_upload_parts" / f"{file_path.stem}_{mtime_ns}"

    def _build_multipart_plan(
        self,
        *,
        candidate: UploadCandidate,
        title: str,
        privacy_status: str,
        delete_original_after_success: bool,
    ) -> dict[str, Any] | None:
        duration_seconds = self._probe_duration_seconds(candidate.file_path)
        if duration_seconds is None or duration_seconds <= _MAX_PART_DURATION_SECONDS:
            return None

        part_dir = self._build_part_directory(candidate.file_path, candidate.mtime_ns)
        part_dir.mkdir(parents=True, exist_ok=True)
        output_pattern = part_dir / f"{candidate.file_path.stem}.part%03d.ts"
        command = [
            self._ffmpeg_path,
            "-y",
            "-i",
            str(candidate.file_path),
            "-c",
            "copy",
            "-map",
            "0",
            "-f",
            "segment",
            "-segment_time",
            str(_MAX_PART_DURATION_SECONDS),
            "-reset_timestamps",
            "1",
            str(output_pattern),
        ]
        try:
            completed = subprocess.run(command, check=False, capture_output=True, text=True)
        except FileNotFoundError as exc:
            raise RuntimeError(f"ffmpeg not found: {self._ffmpeg_path}") from exc
        except Exception as exc:
            raise RuntimeError(f"failed to split long video: {exc}") from exc

        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            raise RuntimeError(f"ffmpeg split failed: {stderr or 'unknown error'}")

        part_paths = sorted(part_dir.glob("*.ts"))
        if len(part_paths) <= 1:
            self._cleanup_generated_parts(
                {
                    "part_dir": str(part_dir),
                    "parts": [{"file_path": str(path)} for path in part_paths],
                },
                delete_original=False,
            )
            return None

        total = len(part_paths)
        return {
            "user_id": candidate.user_id,
            "login": candidate.login,
            "original_file_path": str(candidate.file_path),
            "delete_original_after_success": delete_original_after_success,
            "part_dir": str(part_dir),
            "next_part_index": 0,
            "completed_video_ids": [],
            "parts": [
                {
                    "file_path": str(part_path),
                    "title": f"{title} {index}/{total}",
                    "privacy_status": privacy_status,
                }
                for index, part_path in enumerate(part_paths, start=1)
            ],
        }

    def _cleanup_generated_parts(self, plan: dict[str, Any], *, delete_original: bool) -> None:
        original_file_path = Path(plan["original_file_path"]) if plan.get("original_file_path") else None
        for part in plan.get("parts", []):
            part_path = Path(part["file_path"])
            try:
                if part_path.exists():
                    part_path.unlink()
            except OSError:
                pass

        part_dir = Path(plan["part_dir"]) if plan.get("part_dir") else None
        if part_dir is not None:
            try:
                part_dir.rmdir()
            except OSError:
                pass
            try:
                parent = part_dir.parent
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass

        if delete_original and original_file_path is not None:
            try:
                if original_file_path.exists():
                    original_file_path.unlink()
            except OSError:
                pass

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

    def _wait_for_processing_complete(self, service, video_id: str) -> None:
        deadline = time.monotonic() + _PROCESSING_POLL_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            response = service.videos().list(
                part="status,processingDetails",
                id=video_id,
            ).execute(num_retries=3)
            items = response.get("items", [])
            if not items:
                raise VideoProcessingError("videoNotFound")

            item = items[0]
            status = item.get("status", {})
            processing = item.get("processingDetails", {})
            upload_status = str(status.get("uploadStatus", ""))
            processing_status = str(processing.get("processingStatus", ""))

            if upload_status == "processed" or processing_status == "succeeded":
                return

            if upload_status in {"failed", "rejected"}:
                reason = str(
                    status.get("failureReason")
                    or status.get("rejectionReason")
                    or upload_status
                )
                raise VideoProcessingError(reason)

            if processing_status in {"failed", "terminated"}:
                reason = str(
                    processing.get("processingFailureReason")
                    or processing_status
                )
                raise VideoProcessingError(reason)

            time.sleep(_PROCESSING_POLL_INTERVAL_SECONDS)

        raise VideoProcessingError("processingTimeout")

    def _upload_and_wait(
        self,
        *,
        service,
        file_path: Path,
        title: str,
        privacy_status: str,
    ) -> tuple[str, bool]:
        video_id = self._upload_file(
            service=service,
            file_path=file_path,
            title=title,
            privacy_status=privacy_status,
        )
        try:
            self._wait_for_processing_complete(service, video_id)
            return video_id, True
        except HttpError as exc:
            error_code, error_reason = self._parse_http_error(exc)
            if error_code == "403" and self._is_insufficient_permission_reason(error_reason):
                print(
                    "[warn] processing verification skipped due insufficientPermissions; "
                    "re-run youtube_auth to grant youtube.readonly scope"
                )
                return video_id, False
            raise

    def _delete_uploaded_original(
        self,
        *,
        user_id: str,
        login: str,
        file_path: Path,
    ) -> None:
        try:
            file_path.unlink()
        except OSError as exc:
            self._history.delete_failed(
                user_id=user_id,
                login=login,
                file_path=file_path,
                error_reason=str(exc),
            )
            print(f"[warn] failed to delete uploaded ts file: {file_path} ({exc})")
        else:
            self._history.delete_succeeded(
                user_id=user_id,
                login=login,
                file_path=file_path,
            )

    def _handle_http_error(
        self,
        *,
        file_key: str,
        user_id: str,
        login: str,
        file_path: Path,
        exc: HttpError,
    ) -> bool:
        error_code, error_reason = self._parse_http_error(exc)
        if error_reason in {"quotaExceeded", "dailyLimitExceeded"}:
            blocked_until = self._next_quota_reset_utc(self._quota_config.quota_reset_timezone)
            self._state.set_quota_blocked_until(blocked_until)
            self._history.upload_skipped_quota_block(
                user_id=user_id,
                login=login,
                file_path=file_path,
                quota_blocked_until=blocked_until.isoformat().replace("+00:00", "Z"),
            )
            print(
                "[warn] youtube quota exceeded; skipping uploads until "
                f"{blocked_until.isoformat().replace('+00:00', 'Z')}"
            )
            self._state.save()
            return True

        if self._is_rate_limit_error(error_code, error_reason):
            backoff_seconds = self._rate_limit_backoff_seconds(exc)
            self._state.set_rate_limit_backoff(seconds=backoff_seconds)
            self._state.mark_failed(
                file_key,
                error_code=error_code,
                error_reason=error_reason,
                retryable=True,
            )
            self._history.upload_failed(
                user_id=user_id,
                login=login,
                file_path=file_path,
                error_code=error_code,
                error_reason=error_reason,
            )
            print(
                "[warn] youtube rate limited; backing off uploads for "
                f"{backoff_seconds}s"
            )
            self._state.save()
            return True

        self._state.mark_failed(
            file_key,
            error_code=error_code,
            error_reason=error_reason,
            retryable=False,
        )
        self._history.upload_failed(
            user_id=user_id,
            login=login,
            file_path=file_path,
            error_code=error_code,
            error_reason=error_reason,
        )
        print(
            "[warn] youtube upload failed: "
            f"{file_path.name} code={error_code} reason={error_reason}"
        )
        return False

    def _process_pending_multipart(self, service, file_key: str, plan: dict[str, Any]) -> None:
        next_part_index = int(plan.get("next_part_index", 0))
        parts = plan.get("parts", [])
        if next_part_index >= len(parts):
            completed_video_ids = plan.get("completed_video_ids", [])
            self._state.mark_uploaded(file_key, video_id=",".join(completed_video_ids))
            if plan.get("delete_original_after_success"):
                self._delete_uploaded_original(
                    user_id=str(plan["user_id"]),
                    login=str(plan["login"]),
                    file_path=Path(plan["original_file_path"]),
                )
            self._cleanup_generated_parts(plan, delete_original=False)
            return

        current_part = parts[next_part_index]
        part_path = Path(current_part["file_path"])
        title = str(current_part["title"])
        privacy_status = str(current_part["privacy_status"])
        video_id, verified = self._upload_and_wait(
            service=service,
            file_path=part_path,
            title=title,
            privacy_status=privacy_status,
        )
        if verified:
            self._history.upload_succeeded(
                user_id=str(plan["user_id"]),
                login=str(plan["login"]),
                file_path=part_path,
                video_id=video_id,
                privacy_status=privacy_status,
                title=title,
            )
        else:
            self._history.upload_accepted_unverified(
                user_id=str(plan["user_id"]),
                login=str(plan["login"]),
                file_path=part_path,
                video_id=video_id,
                privacy_status=privacy_status,
                title=title,
                reason="insufficientPermissions",
            )
        completed_video_ids = list(plan.get("completed_video_ids", []))
        completed_video_ids.append(video_id)
        all_parts_verified = bool(plan.get("all_parts_verified", True)) and verified
        plan["all_parts_verified"] = all_parts_verified
        plan["completed_video_ids"] = completed_video_ids
        plan["next_part_index"] = next_part_index + 1
        self._state.update_pending_multipart(file_key, plan)

        if int(plan["next_part_index"]) >= len(parts):
            self._state.mark_uploaded(
                file_key,
                video_id=",".join(completed_video_ids),
                verified=bool(plan.get("all_parts_verified", True)),
            )
            if plan.get("delete_original_after_success") and bool(plan.get("all_parts_verified", True)):
                self._delete_uploaded_original(
                    user_id=str(plan["user_id"]),
                    login=str(plan["login"]),
                    file_path=Path(plan["original_file_path"]),
                )
            self._cleanup_generated_parts(plan, delete_original=False)

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

        if self._state.is_rate_limited(now_utc):
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

        attempt_count = 0
        pending_multipart = self._state.list_pending_multipart()
        for file_key, plan in pending_multipart.items():
            if attempt_count >= self._max_uploads_per_tick:
                break
            if self._state.is_rate_limited(datetime.now(UTC)):
                break

            attempt_count += 1
            current_part = plan.get("parts", [])[int(plan.get("next_part_index", 0))]
            current_part_path = Path(current_part["file_path"])
            try:
                self._process_pending_multipart(service, file_key, plan)
            except HttpError as exc:
                self._handle_http_error(
                    file_key=file_key,
                    user_id=str(plan["user_id"]),
                    login=str(plan["login"]),
                    file_path=current_part_path,
                    exc=exc,
                )
                self._state.save()
                return
            except VideoProcessingError as exc:
                self._state.clear_pending_multipart(file_key)
                self._cleanup_generated_parts(plan, delete_original=False)
                self._state.mark_failed(
                    file_key,
                    error_code="processing_error",
                    error_reason=exc.reason,
                    retryable=False,
                )
                self._history.upload_failed(
                    user_id=str(plan["user_id"]),
                    login=str(plan["login"]),
                    file_path=current_part_path,
                    error_code="processing_error",
                    error_reason=exc.reason,
                )
                print(
                    f"[warn] youtube processing failed for multipart upload: {current_part_path.name} ({exc.reason})"
                )
            except Exception as exc:
                self._state.clear_pending_multipart(file_key)
                self._cleanup_generated_parts(plan, delete_original=False)
                self._state.mark_failed(
                    file_key,
                    error_code="upload_error",
                    error_reason=str(exc),
                    retryable=False,
                )
                self._history.upload_failed(
                    user_id=str(plan["user_id"]),
                    login=str(plan["login"]),
                    file_path=current_part_path,
                    error_code="upload_error",
                    error_reason=str(exc),
                )
                print(
                    f"[warn] youtube multipart upload failed unexpectedly: {current_part_path.name} ({exc})"
                )

        candidates = self._scanner.scan(streamers_by_user_id=streamers_by_user_id, now_utc=now_utc)
        for candidate in candidates:
            if self._state.is_rate_limited(datetime.now(UTC)):
                break
            if attempt_count >= self._max_uploads_per_tick:
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
            if self._state.has_pending_multipart(file_key):
                continue
            if not self._state.should_retry_failed(file_key, now_utc):
                continue

            attempt_count += 1

            try:
                title = TitleTemplateRenderer.render(
                    streamer.youtube_title_template,
                    login=candidate.login,
                    captured_at=candidate.captured_at_utc,
                )
                multipart_plan = self._build_multipart_plan(
                    candidate=candidate,
                    title=title,
                    privacy_status=streamer.youtube_privacy_status,
                    delete_original_after_success=streamer.youtube_delete_ts_after_upload,
                )
                if multipart_plan is not None:
                    self._state.set_pending_multipart(file_key, multipart_plan)
                    self._process_pending_multipart(service, file_key, multipart_plan)
                    continue

                video_id, verified = self._upload_and_wait(
                    service=service,
                    file_path=candidate.file_path,
                    title=title,
                    privacy_status=streamer.youtube_privacy_status,
                )
                self._state.mark_uploaded(file_key, video_id=video_id, verified=verified)
                if verified:
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
                else:
                    self._history.upload_accepted_unverified(
                        user_id=candidate.user_id,
                        login=candidate.login,
                        file_path=candidate.file_path,
                        video_id=video_id,
                        privacy_status=streamer.youtube_privacy_status,
                        title=title,
                        reason="insufficientPermissions",
                    )
                    print(
                        "[warn] youtube upload accepted but processing was not verified: "
                        f"{candidate.file_path.name}"
                    )

                if streamer.youtube_delete_ts_after_upload and verified:
                    self._delete_uploaded_original(
                        user_id=candidate.user_id,
                        login=candidate.login,
                        file_path=candidate.file_path,
                    )
            except TitleTemplateError as exc:
                self._state.mark_failed(
                    file_key,
                    error_code="invalid_template",
                    error_reason=str(exc),
                    retryable=False,
                )
                self._history.upload_failed(
                    user_id=candidate.user_id,
                    login=candidate.login,
                    file_path=candidate.file_path,
                    error_code="invalid_template",
                    error_reason=str(exc),
                )
                print(f"[warn] youtube title template error: {exc}")
            except HttpError as exc:
                self._handle_http_error(
                    file_key=file_key,
                    user_id=candidate.user_id,
                    login=candidate.login,
                    file_path=candidate.file_path,
                    exc=exc,
                )
                self._state.save()
                return
            except VideoProcessingError as exc:
                if self._is_video_too_long_reason(exc.reason):
                    try:
                        multipart_plan = self._build_multipart_plan(
                            candidate=candidate,
                            title=title,
                            privacy_status=streamer.youtube_privacy_status,
                            delete_original_after_success=streamer.youtube_delete_ts_after_upload,
                        )
                    except Exception as split_exc:
                        self._state.mark_failed(
                            file_key,
                            error_code="split_error",
                            error_reason=str(split_exc),
                            retryable=False,
                        )
                        self._history.upload_failed(
                            user_id=candidate.user_id,
                            login=candidate.login,
                            file_path=candidate.file_path,
                            error_code="split_error",
                            error_reason=str(split_exc),
                        )
                        print(f"[warn] failed to split too-long video: {candidate.file_path.name} ({split_exc})")
                    else:
                        if multipart_plan is None:
                            self._state.mark_failed(
                                file_key,
                                error_code="processing_error",
                                error_reason=exc.reason,
                                retryable=False,
                            )
                            self._history.upload_failed(
                                user_id=candidate.user_id,
                                login=candidate.login,
                                file_path=candidate.file_path,
                                error_code="processing_error",
                                error_reason=exc.reason,
                            )
                        else:
                            self._state.set_pending_multipart(file_key, multipart_plan)
                            try:
                                self._process_pending_multipart(service, file_key, multipart_plan)
                            except HttpError as retry_exc:
                                current_part = multipart_plan["parts"][int(multipart_plan["next_part_index"])]
                                self._handle_http_error(
                                    file_key=file_key,
                                    user_id=candidate.user_id,
                                    login=candidate.login,
                                    file_path=Path(current_part["file_path"]),
                                    exc=retry_exc,
                                )
                                self._state.save()
                                return
                            except Exception as retry_other_exc:
                                self._state.clear_pending_multipart(file_key)
                                self._cleanup_generated_parts(multipart_plan, delete_original=False)
                                self._state.mark_failed(
                                    file_key,
                                    error_code="split_upload_error",
                                    error_reason=str(retry_other_exc),
                                    retryable=False,
                                )
                                self._history.upload_failed(
                                    user_id=candidate.user_id,
                                    login=candidate.login,
                                    file_path=candidate.file_path,
                                    error_code="split_upload_error",
                                    error_reason=str(retry_other_exc),
                                )
                else:
                    self._state.mark_failed(
                        file_key,
                        error_code="processing_error",
                        error_reason=exc.reason,
                        retryable=False,
                    )
                    self._history.upload_failed(
                        user_id=candidate.user_id,
                        login=candidate.login,
                        file_path=candidate.file_path,
                        error_code="processing_error",
                        error_reason=exc.reason,
                    )
                    print(
                        f"[warn] youtube processing failed: {candidate.file_path.name} ({exc.reason})"
                    )
            except Exception as exc:
                self._state.mark_failed(
                    file_key,
                    error_code="upload_error",
                    error_reason=str(exc),
                    retryable=False,
                )
                self._history.upload_failed(
                    user_id=candidate.user_id,
                    login=candidate.login,
                    file_path=candidate.file_path,
                    error_code="upload_error",
                    error_reason=str(exc),
                )
                print(f"[warn] youtube upload failed unexpectedly: {candidate.file_path.name} ({exc})")

        self._state.save()
