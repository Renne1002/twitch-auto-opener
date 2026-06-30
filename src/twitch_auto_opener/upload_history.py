from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class UploadHistoryWriter:
    def __init__(self, history_path: Path) -> None:
        self._history_path = history_path

    def _append(self, payload: dict[str, Any]) -> None:
        self._history_path.parent.mkdir(parents=True, exist_ok=True)
        with self._history_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(payload, ensure_ascii=False) + "\n")

    @staticmethod
    def _event_base(event_type: str, user_id: str, login: str, file_path: Path) -> dict[str, Any]:
        return {
            "event_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "event_type": event_type,
            "user_id": user_id,
            "login": login,
            "file_path": str(file_path),
            "file_size": file_path.stat().st_size if file_path.exists() else None,
        }

    def upload_succeeded(
        self,
        *,
        user_id: str,
        login: str,
        file_path: Path,
        video_id: str,
        privacy_status: str,
        title: str,
    ) -> None:
        payload = self._event_base("upload_succeeded", user_id, login, file_path)
        payload.update(
            {
                "video_id": video_id,
                "privacy_status": privacy_status,
                "title": title,
            }
        )
        self._append(payload)

    def upload_failed(
        self,
        *,
        user_id: str,
        login: str,
        file_path: Path,
        error_code: str,
        error_reason: str,
    ) -> None:
        payload = self._event_base("upload_failed", user_id, login, file_path)
        payload.update(
            {
                "error_code": error_code,
                "error_reason": error_reason,
            }
        )
        self._append(payload)

    def upload_accepted_unverified(
        self,
        *,
        user_id: str,
        login: str,
        file_path: Path,
        video_id: str,
        privacy_status: str,
        title: str,
        reason: str,
    ) -> None:
        payload = self._event_base("upload_accepted_unverified", user_id, login, file_path)
        payload.update(
            {
                "video_id": video_id,
                "privacy_status": privacy_status,
                "title": title,
                "error_reason": reason,
            }
        )
        self._append(payload)

    def upload_skipped_quota_block(
        self,
        *,
        user_id: str,
        login: str,
        file_path: Path,
        quota_blocked_until: str,
    ) -> None:
        payload = self._event_base("upload_skipped_quota_block", user_id, login, file_path)
        payload["quota_blocked_until"] = quota_blocked_until
        self._append(payload)

    def delete_succeeded(self, *, user_id: str, login: str, file_path: Path) -> None:
        payload = self._event_base("delete_succeeded", user_id, login, file_path)
        self._append(payload)

    def delete_failed(
        self,
        *,
        user_id: str,
        login: str,
        file_path: Path,
        error_reason: str,
    ) -> None:
        payload = self._event_base("delete_failed", user_id, login, file_path)
        payload["error_reason"] = error_reason
        self._append(payload)
