from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


class UploadStateStore:
    def __init__(self, state_path: Path) -> None:
        self._state_path = state_path
        self._state: dict[str, Any] = {
            "uploaded_files": {},
            "failed_files": {},
            "multipart_uploads": {},
            "quota_blocked_until": None,
            "auth_failed_until": None,
            "rate_limited_until": None,
        }
        self.load()

    def load(self) -> None:
        if not self._state_path.exists():
            return
        try:
            with self._state_path.open("r", encoding="utf-8") as fp:
                raw = json.load(fp)
        except Exception:
            return

        if not isinstance(raw, dict):
            return

        self._state["uploaded_files"] = raw.get("uploaded_files", {})
        self._state["failed_files"] = raw.get("failed_files", {})
        self._state["multipart_uploads"] = raw.get("multipart_uploads", {})
        self._state["quota_blocked_until"] = raw.get("quota_blocked_until")
        self._state["auth_failed_until"] = raw.get("auth_failed_until")
        self._state["rate_limited_until"] = raw.get("rate_limited_until")

    def save(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        with self._state_path.open("w", encoding="utf-8") as fp:
            json.dump(self._state, fp, ensure_ascii=False, indent=2)

    @staticmethod
    def build_file_key(file_path: Path, size: int, mtime_ns: int) -> str:
        return f"{file_path.resolve()}|{size}|{mtime_ns}"

    @staticmethod
    def _parse_utc(ts: str | None) -> datetime | None:
        if not ts:
            return None
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            return None

    def quota_blocked_until(self) -> datetime | None:
        return self._parse_utc(self._state.get("quota_blocked_until"))

    def is_quota_blocked(self, now_utc: datetime) -> bool:
        blocked_until = self.quota_blocked_until()
        return blocked_until is not None and now_utc < blocked_until

    def set_quota_blocked_until(self, blocked_until: datetime) -> None:
        self._state["quota_blocked_until"] = blocked_until.astimezone(UTC).isoformat().replace(
            "+00:00", "Z"
        )

    def auth_failed_until(self) -> datetime | None:
        return self._parse_utc(self._state.get("auth_failed_until"))

    def is_auth_backoff_active(self, now_utc: datetime) -> bool:
        blocked_until = self.auth_failed_until()
        return blocked_until is not None and now_utc < blocked_until

    def set_auth_backoff(self, seconds: int) -> None:
        blocked_until = datetime.now(UTC) + timedelta(seconds=max(1, seconds))
        self._state["auth_failed_until"] = blocked_until.isoformat().replace("+00:00", "Z")

    def clear_auth_backoff(self) -> None:
        self._state["auth_failed_until"] = None

    def rate_limited_until(self) -> datetime | None:
        return self._parse_utc(self._state.get("rate_limited_until"))

    def is_rate_limited(self, now_utc: datetime) -> bool:
        blocked_until = self.rate_limited_until()
        return blocked_until is not None and now_utc < blocked_until

    def set_rate_limit_backoff(self, seconds: int) -> None:
        blocked_until = datetime.now(UTC) + timedelta(seconds=max(1, seconds))
        self._state["rate_limited_until"] = blocked_until.isoformat().replace("+00:00", "Z")

    def was_uploaded(self, file_key: str) -> bool:
        uploaded_files = self._state.get("uploaded_files", {})
        return file_key in uploaded_files

    def has_pending_multipart(self, file_key: str) -> bool:
        multipart_uploads = self._state.get("multipart_uploads", {})
        return file_key in multipart_uploads

    def list_pending_multipart(self) -> dict[str, dict[str, Any]]:
        multipart_uploads = self._state.get("multipart_uploads", {})
        if not isinstance(multipart_uploads, dict):
            return {}
        return dict(multipart_uploads)

    def set_pending_multipart(self, file_key: str, payload: dict[str, Any]) -> None:
        multipart_uploads = self._state.setdefault("multipart_uploads", {})
        multipart_uploads[file_key] = payload

    def clear_pending_multipart(self, file_key: str) -> None:
        multipart_uploads = self._state.setdefault("multipart_uploads", {})
        multipart_uploads.pop(file_key, None)

    def update_pending_multipart(self, file_key: str, payload: dict[str, Any]) -> None:
        multipart_uploads = self._state.setdefault("multipart_uploads", {})
        multipart_uploads[file_key] = payload

    def mark_uploaded(self, file_key: str, *, video_id: str, verified: bool = True) -> None:
        uploaded_files = self._state.setdefault("uploaded_files", {})
        uploaded_files[file_key] = {
            "video_id": video_id,
            "verified": verified,
            "uploaded_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }

        failed_files = self._state.setdefault("failed_files", {})
        failed_files.pop(file_key, None)
        self.clear_pending_multipart(file_key)

    def should_retry_failed(self, file_key: str, now_utc: datetime) -> bool:
        failed_files = self._state.get("failed_files", {})
        failed_entry = failed_files.get(file_key)
        if not failed_entry:
            return True

        if failed_entry.get("retryable") is False:
            return False

        next_retry_at = self._parse_utc(failed_entry.get("next_retry_at_utc"))
        return next_retry_at is None or now_utc >= next_retry_at

    def mark_failed(
        self,
        file_key: str,
        *,
        error_code: str,
        error_reason: str,
        retryable: bool = True,
    ) -> None:
        failed_files = self._state.setdefault("failed_files", {})
        current = failed_files.get(file_key, {})
        failures = int(current.get("failures", 0)) + 1
        delay_seconds = min(3600, 30 * (2 ** (failures - 1))) if retryable else 0
        next_retry_at = datetime.now(UTC) + timedelta(seconds=delay_seconds)

        failed_files[file_key] = {
            "failures": failures,
            "last_error_code": error_code,
            "last_error_reason": error_reason,
            "retryable": retryable,
            "last_failed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "next_retry_at_utc": (
                next_retry_at.isoformat().replace("+00:00", "Z") if retryable else None
            ),
        }
