from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from collections.abc import Awaitable, Coroutine
from concurrent.futures import Future
from threading import Event, Thread
from typing import Any

from twitchAPI.twitch import Twitch


class TwitchApiError(RuntimeError):
    """Raised when Twitch API calls fail."""


@dataclass(frozen=True)
class LiveStreamInfo:
    user_id: str
    stream_id: str
    started_at_utc: str


class TwitchClient:
    def __init__(self, client_id: str, client_secret: str, timeout_seconds: int = 15) -> None:
        self._timeout_seconds = timeout_seconds
        self._loop = asyncio.new_event_loop()
        self._ready = Event()
        self._worker = Thread(target=self._run_loop, name="twitchapi-loop", daemon=True)
        self._worker.start()
        self._ready.wait()
        self._closed = False
        self._twitch = self._run(self._create_twitch(client_id, client_secret))

    async def _create_twitch(self, client_id: str, client_secret: str) -> Twitch:
        # twitchAPI's Twitch(...) returns an awaitable, so wrap it in a coroutine.
        return await Twitch(client_id, client_secret)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def _run(self, coro: Coroutine[Any, Any, Any]) -> Any:
        if self._closed:
            raise TwitchApiError("twitch client is already closed")

        future: Future[Any] = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=self._timeout_seconds)
        except Exception as exc:
            if isinstance(exc, TwitchApiError):
                raise
            raise TwitchApiError(f"twitchAPI call failed: {exc}") from exc

    def _run_awaitable(self, awaitable: Awaitable[Any]) -> Any:
        async def _wrap() -> Any:
            return await awaitable

        return self._run(_wrap())

    def close(self) -> None:
        if self._closed:
            return

        try:
            self._run_awaitable(self._twitch.close())
        except TwitchApiError as exc:
            print(f"[warn] failed to close twitch client cleanly: {exc}")
        finally:
            self._closed = True
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._worker.join(timeout=1.0)
            self._loop.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def resolve_user_ids(self, logins: list[str]) -> dict[str, str]:
        async def _resolve() -> dict[str, str]:
            by_login: dict[str, str] = {}
            async for user in self._twitch.get_users(logins=logins):
                login = str(user.login).strip().lower()
                user_id = str(user.id).strip()
                if login and user_id:
                    by_login[login] = user_id

            missing = [login for login in logins if login not in by_login]
            if missing:
                raise TwitchApiError(f"unknown streamer login(s): {', '.join(missing)}")
            return by_login

        return self._run(_resolve())

    @staticmethod
    def _to_utc_iso(value: Any) -> str:
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        else:
            dt = dt.astimezone(UTC)
        return dt.isoformat().replace("+00:00", "Z")

    def fetch_live_streams(self, user_ids: list[str]) -> dict[str, LiveStreamInfo]:
        if not user_ids:
            return {}

        async def _fetch() -> dict[str, LiveStreamInfo]:
            by_user_id: dict[str, LiveStreamInfo] = {}
            async for stream in self._twitch.get_streams(user_id=user_ids):
                user_id = str(stream.user_id).strip()
                stream_id = str(stream.id).strip()
                started_at = stream.started_at
                if not user_id or not stream_id or not started_at:
                    continue

                by_user_id[user_id] = LiveStreamInfo(
                    user_id=user_id,
                    stream_id=stream_id,
                    started_at_utc=self._to_utc_iso(started_at),
                )
            return by_user_id

        return self._run(_fetch())

    def fetch_live_user_ids(self, user_ids: list[str]) -> set[str]:
        return set(self.fetch_live_streams(user_ids).keys())
