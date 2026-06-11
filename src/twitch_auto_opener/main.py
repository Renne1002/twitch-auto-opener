from __future__ import annotations

import argparse
import platform
import signal
import sys
import webbrowser
from pathlib import Path

from twitch_auto_opener.chrome_launcher import (
    open_stream_url,
    resolve_chrome_target,
)
from twitch_auto_opener.chat_recorder import ChatRecorder
from twitch_auto_opener.config import load_config
from twitch_auto_opener.monitor import MonitorService
from twitch_auto_opener.recorder import TwitchRecorder
from twitch_auto_opener.single_instance import SingleInstance, SingleInstanceError
from twitch_auto_opener.startup import ensure_startup_registration
from twitch_auto_opener.twitch_client import TwitchClient


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Twitch Auto Opener")
    parser.add_argument(
        "--config",
        default="config.toml",
        help="Path to config.toml (default: ./config.toml)",
    )
    return parser.parse_args()


def _resolve_app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd().resolve()


def _resolve_vod_output_dir(raw_path: str | None, app_base_dir: Path) -> Path:
    if not raw_path or not raw_path.strip():
        return app_base_dir / "VOD"

    normalized = raw_path.strip().replace("\\", "/")
    candidate = Path(normalized).expanduser()
    if not candidate.is_absolute():
        candidate = app_base_dir / candidate
    return candidate.resolve()


def run() -> None:
    args = _parse_args()
    is_windows = platform.system() == "Windows"

    config = load_config(args.config)
    app_base_dir = _resolve_app_base_dir()
    vod_output_dir = _resolve_vod_output_dir(config.recording.output_dir, app_base_dir)

    lock = None
    if is_windows:
        lock = SingleInstance("Global\\twitch-auto-opener")
        try:
            lock.acquire()
        except SingleInstanceError as exc:
            print(f"[warn] {exc}")
            return
    else:
        print("[warn] non-Windows mode: startup registration and profile-specific launch are disabled")

    config_path = str(Path(args.config).resolve())
    if is_windows and config.startup.enabled:
        if getattr(sys, "frozen", False):
            startup_command = f'"{sys.executable}" --config "{config_path}"'
        else:
            startup_command = (
                f'"{sys.executable}" -m twitch_auto_opener.main --config "{config_path}"'
            )

        ensure_startup_registration(startup_command)

    twitch_client = TwitchClient(
        client_id=config.twitch_api.client_id,
        client_secret=config.twitch_api.client_secret,
    )

    if is_windows:
        chrome_target = resolve_chrome_target(
            profile_email=config.chrome.profile_email,
            chrome_path=config.chrome.path,
            chrome_user_data_dir=config.chrome.user_data_dir,
        )
        url_opener = lambda url: open_stream_url(chrome_target, url)
    else:
        url_opener = lambda url: webbrowser.open_new_tab(url)

    monitor = MonitorService(
        twitch_client=twitch_client,
        streamers=config.streamers,
        check_interval_seconds=config.monitor.check_interval_seconds,
        url_opener=url_opener,
        recorder=TwitchRecorder(
            output_dir=vod_output_dir,
            streamlink_path=config.recording.tools.streamlink_path,
            ffmpeg_path=config.recording.tools.ffmpeg_path,
            quality=config.recording.quality,
            convert_to_mp4=config.recording.convert_to_mp4,
            retry_delay_seconds=config.recording.retry_delay_seconds,
            fastwhisper_config=config.recording.fastwhisper,
            chat_recorder=ChatRecorder(config.recording.chat),
            debug=config.monitor.debug,
        ),
        debug=config.monitor.debug,
    )

    def _shutdown(*_args: object) -> None:
        print("[info] shutdown signal received")
        if monitor._recorder:
            monitor._recorder.stop_all_chat_sessions(reason="process-shutdown")
        twitch_client.close()
        if lock:
            lock.release()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print("[info] setup monitor")
    monitor.setup()
    print("[info] monitoring started")
    monitor.run_forever()


if __name__ == "__main__":
    run()
