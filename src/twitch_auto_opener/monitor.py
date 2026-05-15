from __future__ import annotations

import time
from typing import Callable

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from twitch_auto_opener.twitch_client import TwitchApiError, TwitchClient


class MonitorService:
    def __init__(
        self,
        twitch_client: TwitchClient,
        streamers: list[str],
        check_interval_seconds: int,
        url_opener: Callable[[str], None],
        debug: bool = False,
    ) -> None:
        self._twitch_client = twitch_client
        self._streamers = streamers
        self._check_interval_seconds = check_interval_seconds
        self._url_opener = url_opener
        self._debug = debug
        self._login_by_user_id: dict[str, str] = {}
        self._previous_live_ids: set[str] = set()

    def _debug_log(self, message: str) -> None:
        if self._debug:
            print(f"[debug] {message}")

    def setup(self) -> None:
        by_login = self._twitch_client.resolve_user_ids(self._streamers)
        self._login_by_user_id = {user_id: login for login, user_id in by_login.items()}
        self._debug_log(
            f"setup resolved {len(self._login_by_user_id)} streamers: {', '.join(self._streamers)}"
        )

    @retry(
        retry=retry_if_exception_type(TwitchApiError),
        wait=wait_exponential(multiplier=1, min=5, max=300),
        stop=stop_after_attempt(10),
        reraise=True,
    )
    def _fetch_live(self, user_ids: list[str]) -> set[str]:
        return self._twitch_client.fetch_live_user_ids(user_ids)

    def run_forever(self) -> None:
        if not self._login_by_user_id:
            raise RuntimeError("MonitorService.setup() must be called first")

        user_ids = list(self._login_by_user_id.keys())
        while True:
            self._debug_log(
                f"poll start: monitored={len(user_ids)} interval={self._check_interval_seconds}s"
            )
            try:
                live_ids = self._fetch_live(user_ids)
            except TwitchApiError as exc:
                print(f"[error] twitch api permanently failed: {exc}")
                raise

            newly_live = live_ids - self._previous_live_ids
            for user_id in newly_live:
                login = self._login_by_user_id[user_id]
                url = f"https://www.twitch.tv/{login}"
                print(f"[info] streamer went live: {login}; opening {url}")
                self._url_opener(url)

            self._previous_live_ids = live_ids
            self._debug_log(
                f"poll done: live={len(live_ids)} newly_live={len(newly_live)} monitored={len(user_ids)}"
            )
            time.sleep(self._check_interval_seconds)
