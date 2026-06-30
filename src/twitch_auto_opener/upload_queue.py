from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from twitch_auto_opener.config import StreamerConfig

_TIMESTAMP_PATTERN = re.compile(r"_(\d{8}_\d{6})_part\d+\.ts$", re.IGNORECASE)


@dataclass(slots=True)
class UploadCandidate:
    user_id: str
    login: str
    file_path: Path
    captured_at_utc: datetime
    file_size: int
    mtime_ns: int


class UploadQueueScanner:
    def __init__(self, output_dir: Path, min_age_days: int) -> None:
        self._output_dir = output_dir
        self._min_age_days = min_age_days

    @staticmethod
    def _extract_captured_at(file_path: Path) -> datetime | None:
        match = _TIMESTAMP_PATTERN.search(file_path.name)
        if not match:
            return None
        try:
            return datetime.strptime(match.group(1), "%Y%m%d_%H%M%S").replace(tzinfo=UTC)
        except ValueError:
            return None

    @staticmethod
    def _fallback_mtime_as_utc(file_path: Path) -> datetime:
        stat = file_path.stat()
        return datetime.fromtimestamp(stat.st_mtime, tz=UTC)

    def scan(self, streamers_by_user_id: dict[str, StreamerConfig], now_utc: datetime) -> list[UploadCandidate]:
        threshold = now_utc.timestamp() - (self._min_age_days * 86400)
        candidates: list[UploadCandidate] = []

        for user_id, streamer in streamers_by_user_id.items():
            if not streamer.youtube_enabled:
                continue

            streamer_dir = self._output_dir / streamer.login
            if not streamer_dir.exists():
                continue

            for file_path in streamer_dir.glob("*.ts"):
                try:
                    stat = file_path.stat()
                except OSError:
                    continue

                if stat.st_mtime > threshold:
                    continue

                captured_at_utc = self._extract_captured_at(file_path)
                if captured_at_utc is None:
                    captured_at_utc = self._fallback_mtime_as_utc(file_path)

                candidates.append(
                    UploadCandidate(
                        user_id=user_id,
                        login=streamer.login,
                        file_path=file_path,
                        captured_at_utc=captured_at_utc,
                        file_size=stat.st_size,
                        mtime_ns=stat.st_mtime_ns,
                    )
                )

        candidates.sort(key=lambda item: (item.captured_at_utc, item.file_path.name))
        return candidates
