from __future__ import annotations

import random
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event
from typing import Iterator


@dataclass(frozen=True)
class IrcEvent:
    raw_line: str
    command: str
    channel: str
    user_login: str
    message: str
    tags: dict[str, str]
    sent_at_utc: str


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _iso_from_epoch_millis(raw: str | None) -> str:
    if not raw:
        return _utc_now_iso()

    try:
        millis = int(raw)
    except ValueError:
        return _utc_now_iso()

    dt = datetime.fromtimestamp(millis / 1000, tz=UTC)
    return dt.isoformat().replace("+00:00", "Z")


def _parse_tags(raw_tags: str) -> dict[str, str]:
    tags: dict[str, str] = {}
    for part in raw_tags.split(";"):
        if not part:
            continue
        key, _, value = part.partition("=")
        tags[key] = (
            value.replace(r"\\s", " ")
            .replace(r"\\:", ";")
            .replace(r"\\r", "\r")
            .replace(r"\\n", "\n")
            .replace(r"\\\\", "\\")
        )
    return tags


def parse_irc_event(line: str) -> IrcEvent | None:
    if not line:
        return None

    rest = line.strip()
    tags: dict[str, str] = {}
    if rest.startswith("@"):
        tag_part, _, rest = rest.partition(" ")
        tags = _parse_tags(tag_part[1:])

    prefix = ""
    if rest.startswith(":"):
        prefix_part, _, rest = rest.partition(" ")
        prefix = prefix_part[1:]

    if not rest:
        return None

    if " :" in rest:
        head, _, trailing = rest.partition(" :")
    else:
        head, trailing = rest, ""

    parts = head.split()
    if not parts:
        return None

    command = parts[0].upper()
    channel = ""
    if len(parts) >= 2 and parts[1].startswith("#"):
        channel = parts[1][1:]

    user_login = ""
    if prefix and "!" in prefix:
        user_login = prefix.split("!", 1)[0]

    return IrcEvent(
        raw_line=line,
        command=command,
        channel=channel,
        user_login=user_login,
        message=trailing,
        tags=tags,
        sent_at_utc=_iso_from_epoch_millis(tags.get("tmi-sent-ts")),
    )


class TwitchIrcClient:
    def __init__(
        self,
        channel_login: str,
        connect_timeout_seconds: int,
        read_timeout_seconds: int,
        debug: bool = False,
    ) -> None:
        self._channel_login = channel_login.strip().lower()
        self._connect_timeout_seconds = connect_timeout_seconds
        self._read_timeout_seconds = read_timeout_seconds
        self._debug = debug
        self._socket: socket.socket | ssl.SSLSocket | None = None
        self._buffer = b""

    def _debug_log(self, message: str) -> None:
        if self._debug:
            print(f"[debug] irc {self._channel_login} {message}")

    def connect(self) -> None:
        if not self._channel_login:
            raise ValueError("channel login is required")

        base_socket = socket.create_connection(
            ("irc.chat.twitch.tv", 6697),
            timeout=self._connect_timeout_seconds,
        )
        context = ssl.create_default_context()
        wrapped = context.wrap_socket(base_socket, server_hostname="irc.chat.twitch.tv")
        wrapped.settimeout(self._read_timeout_seconds)
        self._socket = wrapped

        nick = f"justinfan{random.randint(100000, 999999)}"
        self.send_raw("PASS SCHMOOPIIE")
        self.send_raw(f"NICK {nick}")
        self.send_raw("CAP REQ :twitch.tv/tags twitch.tv/commands")
        self.send_raw(f"JOIN #{self._channel_login}")
        self._debug_log("connected")

    def close(self) -> None:
        sock = self._socket
        self._socket = None
        if sock is None:
            return

        try:
            sock.close()
        except OSError:
            pass

    def send_raw(self, line: str) -> None:
        if self._socket is None:
            raise ConnectionError("irc socket is not connected")
        payload = f"{line}\r\n".encode("utf-8")
        self._socket.sendall(payload)

    def _read_line(self) -> str | None:
        if self._socket is None:
            raise ConnectionError("irc socket is not connected")

        while b"\r\n" not in self._buffer:
            try:
                chunk = self._socket.recv(4096)
            except socket.timeout:
                return None
            if not chunk:
                raise ConnectionError("irc connection closed by remote host")
            self._buffer += chunk

        raw_line, self._buffer = self._buffer.split(b"\r\n", 1)
        return raw_line.decode("utf-8", errors="replace")

    def iter_events(self, stop_event: Event) -> Iterator[IrcEvent]:
        while not stop_event.is_set():
            line = self._read_line()
            if line is None:
                continue

            if line.startswith("PING "):
                self.send_raw(line.replace("PING", "PONG", 1))
                continue

            event = parse_irc_event(line)
            if event is not None:
                yield event
