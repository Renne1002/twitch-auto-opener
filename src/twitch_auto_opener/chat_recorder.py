from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from twitch_auto_opener.config import ChatConfig
from twitch_auto_opener.irc_client import IrcEvent, TwitchIrcClient


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc(raw: str | None) -> datetime | None:
    if not raw:
        return None

    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _rel_millis(base_utc: str | None, event_utc: str) -> int | None:
    base_dt = _parse_utc(base_utc)
    event_dt = _parse_utc(event_utc)
    if base_dt is None or event_dt is None:
        return None
    return int((event_dt - base_dt).total_seconds() * 1000)


@dataclass
class _SessionState:
    user_id: str
    login: str
    session_id: str
    stream_id: str
    output_dir: Path
    session_path: Path
    comments_path: Path
    stream_started_at_utc: str
    recorder_requested_at_utc: str
    recorder_first_byte_at_utc: str | None
    capture_moderation_events: bool
    reconnect_delay_seconds: int
    connect_timeout_seconds: int
    read_timeout_seconds: int
    debug: bool
    stop_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    thread: threading.Thread | None = None
    stop_reason: str | None = None
    started_at_utc: str = field(default_factory=_utc_now_iso)
    ended_at_utc: str | None = None
    event_count_total: int = 0
    event_count_by_type: dict[str, int] = field(default_factory=dict)


class ChatRecorder:
    def __init__(self, config: ChatConfig) -> None:
        self._enabled = config.enabled
        self._capture_moderation_events = config.capture_moderation_events
        self._reconnect_delay_seconds = config.reconnect_delay_seconds
        self._connect_timeout_seconds = config.connect_timeout_seconds
        self._read_timeout_seconds = config.read_timeout_seconds
        self._debug = config.debug
        self._lock = threading.Lock()
        self._active_sessions: dict[str, _SessionState] = {}

    def _debug_log(self, message: str) -> None:
        if self._debug:
            print(f"[debug] chat {message}")

    def _write_session_file(self, state: _SessionState, status: str) -> None:
        payload = {
            "schema_version": 1,
            "status": status,
            "user_id": state.user_id,
            "login": state.login,
            "session_id": state.session_id,
            "stream_id": state.stream_id,
            "started_at_utc": state.started_at_utc,
            "ended_at_utc": state.ended_at_utc,
            "stop_reason": state.stop_reason,
            "anchors": {
                "stream_started_at_utc": state.stream_started_at_utc,
                "recorder_requested_at_utc": state.recorder_requested_at_utc,
                "recorder_first_byte_at_utc": state.recorder_first_byte_at_utc,
            },
            "files": {
                "comments": state.comments_path.name,
            },
            "stats": {
                "event_count_total": state.event_count_total,
                "event_count_by_type": state.event_count_by_type,
            },
        }

        state.session_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def start_session(
        self,
        user_id: str,
        login: str,
        session_id: str,
        output_dir: Path,
        stream_id: str,
        stream_started_at_utc: str,
        recorder_requested_at_utc: str,
    ) -> None:
        if not self._enabled:
            return

        with self._lock:
            existing = self._active_sessions.get(user_id)
            if existing and existing.thread and existing.thread.is_alive():
                return

            output_dir.mkdir(parents=True, exist_ok=True)
            base_name = f"{login}_{session_id}"
            session_path = output_dir / f"{base_name}.chat.session.json"
            comments_path = output_dir / f"{base_name}.chat.jsonl"

            state = _SessionState(
                user_id=user_id,
                login=login,
                session_id=session_id,
                stream_id=stream_id,
                output_dir=output_dir,
                session_path=session_path,
                comments_path=comments_path,
                stream_started_at_utc=stream_started_at_utc,
                recorder_requested_at_utc=recorder_requested_at_utc,
                recorder_first_byte_at_utc=None,
                capture_moderation_events=self._capture_moderation_events,
                reconnect_delay_seconds=self._reconnect_delay_seconds,
                connect_timeout_seconds=self._connect_timeout_seconds,
                read_timeout_seconds=self._read_timeout_seconds,
                debug=self._debug,
            )
            self._write_session_file(state, status="running")

            worker = threading.Thread(
                target=self._run_session,
                args=(state,),
                daemon=True,
                name=f"chat-{login}",
            )
            state.thread = worker
            self._active_sessions[user_id] = state
            worker.start()
            self._debug_log(f"chat session started for {login}")

    def set_recorder_anchor(self, user_id: str, recorder_first_byte_at_utc: str) -> None:
        if not self._enabled:
            return

        with self._lock:
            state = self._active_sessions.get(user_id)
        if state is None:
            return

        with state.lock:
            if state.recorder_first_byte_at_utc:
                return
            state.recorder_first_byte_at_utc = recorder_first_byte_at_utc
            self._write_session_file(state, status="running")

    def stop_session(self, user_id: str, reason: str) -> None:
        if not self._enabled:
            return

        with self._lock:
            state = self._active_sessions.pop(user_id, None)
        if state is None:
            return

        state.stop_reason = reason
        state.stop_event.set()

        thread = state.thread
        if thread and thread.is_alive():
            thread.join(timeout=5)

        with state.lock:
            state.ended_at_utc = _utc_now_iso()
            self._write_session_file(state, status="stopped")

        self._debug_log(f"chat session stopped for {state.login}: reason={reason}")

    def stop_all(self, reason: str) -> None:
        if not self._enabled:
            return

        with self._lock:
            user_ids = list(self._active_sessions.keys())

        for user_id in user_ids:
            self.stop_session(user_id, reason=reason)

    @staticmethod
    def _map_event_type(command: str) -> str:
        mapping = {
            "PRIVMSG": "message",
            "CLEARMSG": "clearmsg",
            "CLEARCHAT": "clearchat",
            "USERNOTICE": "usernotice",
            "NOTICE": "notice",
            "ROOMSTATE": "roomstate",
        }
        return mapping.get(command, command.lower())

    @staticmethod
    def _should_store_event(command: str, capture_moderation_events: bool) -> bool:
        if command == "PRIVMSG":
            return True

        if not capture_moderation_events:
            return False

        return command in {"CLEARMSG", "CLEARCHAT", "USERNOTICE", "NOTICE", "ROOMSTATE"}

    def _build_event_payload(self, state: _SessionState, event: IrcEvent) -> dict[str, Any]:
        event_type = self._map_event_type(event.command)
        return {
            "event_type": event_type,
            "event_id": event.tags.get("id", ""),
            "stream_id": state.stream_id,
            "user_id": state.user_id,
            "login": state.login,
            "sent_at_utc": event.sent_at_utc,
            "rel_stream_ms": _rel_millis(state.stream_started_at_utc, event.sent_at_utc),
            "rel_record_ms": _rel_millis(state.recorder_first_byte_at_utc, event.sent_at_utc),
            "user_login": event.user_login,
            "display_name": event.tags.get("display-name", ""),
            "message": event.message,
            "tags": event.tags,
            "raw_line": event.raw_line,
        }

    def _run_session(self, state: _SessionState) -> None:
        with state.comments_path.open("a", encoding="utf-8") as fp:
            while not state.stop_event.is_set():
                client = TwitchIrcClient(
                    channel_login=state.login,
                    connect_timeout_seconds=state.connect_timeout_seconds,
                    read_timeout_seconds=state.read_timeout_seconds,
                    debug=state.debug,
                )

                try:
                    client.connect()
                    for event in client.iter_events(state.stop_event):
                        if not self._should_store_event(
                            event.command,
                            capture_moderation_events=state.capture_moderation_events,
                        ):
                            continue

                        payload = self._build_event_payload(state, event)
                        fp.write(json.dumps(payload, ensure_ascii=False) + "\n")
                        fp.flush()

                        event_type = payload["event_type"]
                        with state.lock:
                            state.event_count_total += 1
                            state.event_count_by_type[event_type] = (
                                state.event_count_by_type.get(event_type, 0) + 1
                            )
                except Exception as exc:
                    self._debug_log(f"chat loop error for {state.login}: {exc}")
                    if state.stop_event.is_set():
                        break
                    time.sleep(state.reconnect_delay_seconds)
                finally:
                    client.close()
