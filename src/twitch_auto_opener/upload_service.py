from __future__ import annotations

import threading
from collections.abc import Callable

from twitch_auto_opener.config import StreamerConfig
from twitch_auto_opener.youtube_uploader import YoutubeUploader


class UploadService:
    def __init__(
        self,
        uploader: YoutubeUploader,
        get_streamers_by_user_id: Callable[[], dict[str, StreamerConfig]],
        loop_interval_seconds: float = 1.0,
    ) -> None:
        self._uploader = uploader
        self._get_streamers_by_user_id = get_streamers_by_user_id
        self._loop_interval_seconds = max(0.1, loop_interval_seconds)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        thread = self._thread
        if thread is not None and thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="youtube-uploader",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def join(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout=timeout)

    def is_alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._uploader.tick(self._get_streamers_by_user_id())
            except Exception as exc:
                print(f"[warn] youtube uploader tick failed: {exc}")

            self._stop_event.wait(self._loop_interval_seconds)
