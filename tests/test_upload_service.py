from __future__ import annotations

import threading
import unittest

from twitch_auto_opener.upload_service import UploadService


class _FakeUploader:
    def __init__(self, raise_once: bool = False) -> None:
        self._raise_once = raise_once
        self.count = 0
        self.called_event = threading.Event()
        self._lock = threading.Lock()

    def tick(self, _streamers: dict[str, object]) -> None:
        with self._lock:
            self.count += 1
            current = self.count
        if self._raise_once and current == 1:
            raise RuntimeError("transient failure")
        self.called_event.set()


class UploadServiceThreadingTest(unittest.TestCase):
    def test_service_calls_tick_in_background(self) -> None:
        uploader = _FakeUploader()
        service = UploadService(
            uploader=uploader,
            get_streamers_by_user_id=lambda: {},
            loop_interval_seconds=0.05,
        )

        service.start()
        self.assertTrue(uploader.called_event.wait(timeout=1.0), "tick was not called")
        service.stop()
        service.join(timeout=1.0)

        self.assertFalse(service.is_alive(), "upload thread did not stop")
        self.assertGreaterEqual(uploader.count, 1)

    def test_service_recovers_from_tick_exception(self) -> None:
        uploader = _FakeUploader(raise_once=True)
        service = UploadService(
            uploader=uploader,
            get_streamers_by_user_id=lambda: {},
            loop_interval_seconds=0.05,
        )

        service.start()
        self.assertTrue(uploader.called_event.wait(timeout=1.5), "tick did not recover after error")
        service.stop()
        service.join(timeout=1.0)

        self.assertFalse(service.is_alive(), "upload thread did not stop")
        self.assertGreaterEqual(uploader.count, 2)


if __name__ == "__main__":
    unittest.main()
