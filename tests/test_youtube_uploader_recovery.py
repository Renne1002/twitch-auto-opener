from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from google.auth.exceptions import RefreshError

from twitch_auto_opener.upload_history import UploadHistoryWriter
from twitch_auto_opener.upload_queue import UploadCandidate
from twitch_auto_opener.upload_state import UploadStateStore
from twitch_auto_opener.youtube_uploader import _MAX_TRANSIENT_FAILURES, YoutubeUploader


def _make_uploader(tmp: Path) -> YoutubeUploader:
    # NOTE: __init__ はAppConfig全体を要求するため、検証対象が使う属性だけを差し込む
    uploader = object.__new__(YoutubeUploader)
    uploader._state = UploadStateStore(tmp / "state.json")
    uploader._history = UploadHistoryWriter(tmp / "history.jsonl")
    return uploader


def _make_candidate(path: Path) -> UploadCandidate:
    stat = path.stat()
    return UploadCandidate(
        user_id="1",
        login="streamer",
        file_path=path,
        captured_at_utc=datetime(2026, 7, 1, tzinfo=UTC),
        file_size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )


def _fake_service(items: list[dict]) -> mock.MagicMock:
    service = mock.MagicMock()
    service.videos.return_value.list.return_value.execute.return_value = {"items": items}
    return service


class UnexpectedErrorHandlingTest(unittest.TestCase):
    def test_refresh_error_sets_auth_backoff_without_marking_file_failed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            uploader = _make_uploader(tmp)
            should_abort = uploader._handle_unexpected_error(
                file_key="k",
                user_id="1",
                login="streamer",
                file_path=tmp / "a.ts",
                error_code="upload_error",
                exc=RefreshError("invalid_grant: Token has been expired or revoked."),
            )

            self.assertTrue(should_abort)
            self.assertTrue(uploader._state.should_retry_failed("k", datetime.now(UTC)))
            self.assertTrue(uploader._state.is_auth_backoff_active(datetime.now(UTC)))

    def test_transient_error_is_retryable_until_limit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            uploader = _make_uploader(tmp)
            for _ in range(_MAX_TRANSIENT_FAILURES - 1):
                uploader._handle_unexpected_error(
                    file_key="k",
                    user_id="1",
                    login="streamer",
                    file_path=tmp / "a.ts",
                    error_code="upload_error",
                    exc=OSError("EOF occurred in violation of protocol"),
                )
                self.assertFalse(uploader._state.is_permanently_failed("k"))

            uploader._handle_unexpected_error(
                file_key="k",
                user_id="1",
                login="streamer",
                file_path=tmp / "a.ts",
                error_code="upload_error",
                exc=OSError("EOF occurred in violation of protocol"),
            )
            self.assertTrue(uploader._state.is_permanently_failed("k"))


class DeleteAlreadyUploadedTest(unittest.TestCase):
    def _setup(self, tmp: Path, *, verified: bool = True) -> tuple[YoutubeUploader, UploadCandidate, dict]:
        ts = tmp / "a.ts"
        ts.write_bytes(b"x")
        uploader = _make_uploader(tmp)
        candidate = _make_candidate(ts)
        key = UploadStateStore.build_file_key(ts, candidate.file_size, candidate.mtime_ns)
        uploader._state.mark_uploaded(key, video_id="v1,v2", verified=verified)
        streamers = {"1": SimpleNamespace(youtube_delete_ts_after_upload=True)}
        return uploader, candidate, streamers

    def test_deletes_when_all_videos_processed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            uploader, candidate, streamers = self._setup(Path(raw))
            service = _fake_service(
                [
                    {"id": "v1", "status": {"uploadStatus": "processed"}},
                    {"id": "v2", "status": {"uploadStatus": "processed"}},
                ]
            )
            uploader._delete_already_uploaded_originals(service, [candidate], streamers)
            self.assertFalse(candidate.file_path.exists())

    def test_keeps_file_when_any_video_missing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            uploader, candidate, streamers = self._setup(Path(raw))
            service = _fake_service([{"id": "v1", "status": {"uploadStatus": "processed"}}])
            uploader._delete_already_uploaded_originals(service, [candidate], streamers)
            self.assertTrue(candidate.file_path.exists())

    def test_skips_unverified_upload_without_api_call(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            uploader, candidate, streamers = self._setup(Path(raw), verified=False)
            service = _fake_service([])
            uploader._delete_already_uploaded_originals(service, [candidate], streamers)
            self.assertTrue(candidate.file_path.exists())
            service.videos.assert_not_called()


if __name__ == "__main__":
    unittest.main()
