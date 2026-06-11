from __future__ import annotations

import time
from typing import Callable

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from twitch_auto_opener.config import StreamerConfig
from twitch_auto_opener.recorder import TwitchRecorder
from twitch_auto_opener.twitch_client import LiveStreamInfo, TwitchApiError, TwitchClient


class MonitorService:
    def __init__(
        self,
        twitch_client: TwitchClient,
        streamers: list[StreamerConfig],
        check_interval_seconds: int,
        url_opener: Callable[[str], None],
        recorder: TwitchRecorder | None = None,
        debug: bool = False,
    ) -> None:
        self._twitch_client = twitch_client
        self._streamers = streamers
        self._check_interval_seconds = check_interval_seconds
        self._url_opener = url_opener
        self._recorder = recorder
        self._debug = debug
        self._streamer_flags_by_login: dict[str, tuple[bool, bool, bool]] = {
            item.login: (item.auto_open, item.record, item.auto_srt) for item in streamers
        }
        self._login_by_user_id: dict[str, str] = {}
        self._flags_by_user_id: dict[str, tuple[bool, bool, bool]] = {}
        self._previous_live_ids: set[str] = set()

    def _debug_log(self, message: str) -> None:
        if self._debug:
            print(f"[debug] {message}")

    def setup(self) -> None:
        streamers = list(self._streamer_flags_by_login.keys())
        by_login = self._twitch_client.resolve_user_ids(streamers)
        self._login_by_user_id = {user_id: login for login, user_id in by_login.items()}
        self._flags_by_user_id = {
            user_id: self._streamer_flags_by_login[login]
            for user_id, login in self._login_by_user_id.items()
        }
        self._debug_log(
            f"setup resolved {len(self._login_by_user_id)} streamers: {', '.join(streamers)}"
        )

    @retry(
        retry=retry_if_exception_type(TwitchApiError),
        wait=wait_exponential(multiplier=1, min=5, max=300),
        stop=stop_after_attempt(10),
        reraise=True,
    )
    def _fetch_live(self, user_ids: list[str]) -> dict[str, LiveStreamInfo]:
        return self._twitch_client.fetch_live_streams(user_ids)

    def run_forever(self) -> None:
        if not self._login_by_user_id:
            raise RuntimeError("MonitorService.setup() must be called first")

        user_ids = list(self._login_by_user_id.keys())
        while True:
            self._debug_log(
                f"poll start: monitored={len(user_ids)} interval={self._check_interval_seconds}s"
            )
            try:
                live_by_user_id = self._fetch_live(user_ids)
            except TwitchApiError as exc:
                print(f"[error] twitch api permanently failed: {exc}")
                raise

            live_ids = set(live_by_user_id.keys())
            newly_live = live_ids - self._previous_live_ids
            for user_id in newly_live:
                login = self._login_by_user_id[user_id]
                auto_open, _record, _auto_srt = self._flags_by_user_id[user_id]
                if not auto_open:
                    continue
                url = f"https://www.twitch.tv/{login}"
                print(f"[info] streamer went live: {login}; opening {url}")
                self._url_opener(url)

            if self._recorder:
                for user_id in live_ids:
                    login = self._login_by_user_id[user_id]
                    _auto_open, record, auto_srt = self._flags_by_user_id[user_id]
                    if not record:
                        continue
                    stream_info = live_by_user_id.get(user_id)
                    if stream_info is None:
                        continue
                    url = f"https://www.twitch.tv/{login}"
                    self._recorder.ensure_recording(
                        user_id=user_id,
                        login=login,
                        url=url,
                        is_live_now=lambda uid=user_id: uid in self._fetch_live([uid]),
                        stream_id=stream_info.stream_id,
                        stream_started_at_utc=stream_info.started_at_utc,
                        auto_srt=auto_srt,
                    )

            self._previous_live_ids = live_ids
            self._debug_log(
                f"poll done: live={len(live_ids)} newly_live={len(newly_live)} monitored={len(user_ids)}"
            )
            time.sleep(self._check_interval_seconds)
