from __future__ import annotations

import threading
import time
import unittest

from twitch_auto_opener.config import StreamerConfig
from twitch_auto_opener.monitor import MonitorService
from twitch_auto_opener.twitch_client import LiveStreamInfo


class _FakeTwitchClient:
    def __init__(self) -> None:
        self._fetch_count = 0

    def resolve_user_ids(self, logins: list[str]) -> dict[str, str]:
        return {login: "uid-1" for login in logins}

    def fetch_live_streams(self, user_ids: list[str]) -> dict[str, LiveStreamInfo]:
        self._fetch_count += 1
        if self._fetch_count == 1:
            return {
                "uid-1": LiveStreamInfo(
                    user_id="uid-1",
                    stream_id="stream-1",
                    started_at_utc="2026-01-01T00:00:00Z",
                )
            }
        return {}


class MonitorServiceThreadingTest(unittest.TestCase):
    def test_newly_live_opens_url_and_stops_cleanly(self) -> None:
        stop_event = threading.Event()
        opened_urls: list[str] = []
        opened_signal = threading.Event()

        def _open(url: str) -> None:
            opened_urls.append(url)
            opened_signal.set()

        monitor = MonitorService(
            twitch_client=_FakeTwitchClient(),
            streamers=[
                StreamerConfig(
                    login="alice",
                    auto_open=True,
                    record=False,
                    auto_srt=False,
                    youtube_enabled=False,
                    youtube_title_template="{id}",
                    youtube_privacy_status="unlisted",
                    youtube_delete_ts_after_upload=False,
                )
            ],
            check_interval_seconds=1,
            url_opener=_open,
            recorder=None,
            debug=False,
            stop_event=stop_event,
        )

        monitor.setup()
        worker = threading.Thread(target=monitor.run_forever, daemon=True)
        worker.start()

        self.assertTrue(opened_signal.wait(timeout=2.0), "stream open event was not observed")

        monitor.stop()
        worker.join(timeout=2.0)

        self.assertFalse(worker.is_alive(), "monitor thread did not stop")
        self.assertEqual(opened_urls, ["https://www.twitch.tv/alice"])


if __name__ == "__main__":
    unittest.main()
