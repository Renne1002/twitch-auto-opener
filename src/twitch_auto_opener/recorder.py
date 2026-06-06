from __future__ import annotations

import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from twitch_auto_opener.config import FastWhisperConfig


class TwitchRecorder:
    def __init__(
        self,
        output_dir: Path,
        streamlink_path: str,
        ffmpeg_path: str,
        quality: str,
        convert_to_mp4: bool,
        retry_delay_seconds: int,
        fastwhisper_config: FastWhisperConfig | None = None,
        debug: bool = False,
    ) -> None:
        self._output_dir = output_dir
        self._streamlink_path = streamlink_path
        self._ffmpeg_path = ffmpeg_path
        self._quality = quality
        self._convert_to_mp4 = convert_to_mp4
        self._retry_delay_seconds = retry_delay_seconds
        self._fastwhisper_config = fastwhisper_config
        self._debug = debug
        self._active_threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def _debug_log(self, message: str) -> None:
        if self._debug:
            print(f"[debug] recorder {message}")

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    @staticmethod
    def _sanitize_name(name: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in name.strip())
        return safe or "unknown"

    def _build_streamer_output_dir(self, login: str) -> Path:
        safe_login = self._sanitize_name(login)
        return self._output_dir / safe_login

    def _build_ts_path(self, streamer_output_dir: Path, login: str, session_ts: str, part: int) -> Path:
        safe_login = self._sanitize_name(login)
        filename = f"{safe_login}_{session_ts}_part{part:03d}.ts"
        return streamer_output_dir / filename

    def _record_once(self, url: str, output_path: Path) -> int:
        command = [
            self._streamlink_path,
            "--twitch-disable-ads",
            "--retry-open",
            "60",
            "--retry-streams",
            "3",
            url,
            self._quality,
            "-o",
            str(output_path),
        ]
        print(f"[info] recording started: {output_path.name}")
        try:
            completed = subprocess.run(command, check=False)
        except FileNotFoundError:
            print(f"[error] streamlink not found: {self._streamlink_path}")
            return 127
        except Exception as exc:
            print(f"[error] streamlink execution failed: {exc}")
            return 1

        self._debug_log(
            f"streamlink finished: returncode={completed.returncode} file={output_path.name}"
        )
        return completed.returncode

    def _convert_to_mp4_if_needed(self, ts_path: Path) -> None:
        if not self._convert_to_mp4:
            return
        if not ts_path.exists():
            print(f"[warn] ts file not found after recording: {ts_path}")
            return

        mp4_path = ts_path.with_suffix(".mp4")
        command = [
            self._ffmpeg_path,
            "-y",
            "-i",
            str(ts_path),
            "-c",
            "copy",
            str(mp4_path),
        ]
        self._debug_log(f"running ffmpeg conversion: {mp4_path.name}")
        try:
            completed = subprocess.run(command, check=False)
        except FileNotFoundError:
            print(f"[warn] ffmpeg not found: {self._ffmpeg_path}; keep ts file")
            return
        except Exception as exc:
            print(f"[warn] ffmpeg execution failed: {exc}; keep ts file")
            return

        if completed.returncode == 0:
            try:
                ts_path.unlink()
                print(f"[info] recording saved: {mp4_path}")
            except OSError as exc:
                print(f"[warn] failed to delete ts file {ts_path}: {exc}")
        else:
            print(f"[warn] ffmpeg conversion failed (code={completed.returncode}); keep ts file")

    def _build_fastwhisper_command(self, video_path: Path) -> list[str]:
        fw = self._fastwhisper_config
        assert fw is not None
        command = [
            fw.fast_whisper_path,
            str(video_path),
            "--beep_off",
            "--model", fw.model,
            "--device", fw.device,
            "--max_line_width", str(fw.max_line_width),
            "--threads", str(fw.threads),
            "--output_dir", str(video_path.parent),
        ]
        if fw.language:
            command.extend(["--language", fw.language])
        return command

    def _generate_subtitle_if_needed(self, video_path: Path, login: str) -> None:
        fw = self._fastwhisper_config
        if fw is None:
            print(f"[warn] auto_srt is enabled for {login} but fastwhisper_config is not set; skip")
            return

        if not video_path.exists():
            print(f"[warn] subtitle generation skipped: video file not found: {video_path}")
            return

        srt_path = video_path.with_suffix(".srt")
        command = self._build_fastwhisper_command(video_path)
        self._debug_log(f"subtitle generation start: {video_path.name} -> {srt_path.name}")
        print(f"[info] subtitle generation started: {video_path.name}")

        for attempt in range(1, fw.retry_max_failures + 1):
            try:
                completed = subprocess.run(command, check=False)
            except FileNotFoundError:
                print(f"[warn] faster-whisper not found: {fw.fast_whisper_path}; skip subtitle generation")
                return
            except Exception as exc:
                print(f"[warn] faster-whisper execution error (attempt {attempt}/{fw.retry_max_failures}): {exc}")
            else:
                if completed.returncode == 0 and srt_path.exists() and srt_path.stat().st_size > 0:
                    print(f"[info] subtitle saved: {srt_path.name}")
                    return
                print(
                    f"[warn] faster-whisper failed (code={completed.returncode}, "
                    f"attempt {attempt}/{fw.retry_max_failures})"
                )

            if attempt < fw.retry_max_failures:
                self._debug_log(f"subtitle retry in {fw.retry_delay_seconds}s")
                time.sleep(fw.retry_delay_seconds)

        print(f"[warn] subtitle generation gave up after {fw.retry_max_failures} attempt(s): {video_path.name}")

    def _run_recording_loop(
        self,
        user_id: str,
        login: str,
        url: str,
        is_live_now: Callable[[], bool],
        auto_srt: bool = False,
    ) -> None:
        session_ts = self._timestamp()
        part = 1
        streamer_output_dir = self._build_streamer_output_dir(login)

        try:
            streamer_output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"[error] failed to create output directory {streamer_output_dir}: {exc}")
            return

        print(f"[info] start VOD recording for {login}: output_dir={streamer_output_dir}")

        while True:
            output_path = self._build_ts_path(streamer_output_dir, login, session_ts, part)
            return_code = self._record_once(url, output_path)
            if return_code == 0:
                self._convert_to_mp4_if_needed(output_path)
                if auto_srt:
                    self._generate_subtitle_if_needed(output_path, login)
                print(f"[info] recording session ended for {login}")
                break

            try:
                still_live = is_live_now()
            except Exception as exc:
                print(f"[warn] failed to recheck stream status for {login}: {exc}")
                still_live = True

            if not still_live:
                if output_path.exists() and output_path.stat().st_size > 0:
                    self._convert_to_mp4_if_needed(output_path)
                    if auto_srt:
                        self._generate_subtitle_if_needed(output_path, login)
                print(f"[info] stream appears offline; stop recording retries for {login}")
                break

            part += 1
            print(
                f"[warn] recording process ended unexpectedly for {login} (code={return_code}); "
                f"retry in {self._retry_delay_seconds}s"
            )
            time.sleep(self._retry_delay_seconds)

        with self._lock:
            self._active_threads.pop(user_id, None)
        self._debug_log(f"recording thread finished for {login}")

    def ensure_recording(
        self,
        user_id: str,
        login: str,
        url: str,
        is_live_now: Callable[[], bool],
        auto_srt: bool = False,
    ) -> None:
        with self._lock:
            thread = self._active_threads.get(user_id)
            if thread and thread.is_alive():
                return

            worker = threading.Thread(
                target=self._run_recording_loop,
                args=(user_id, login, url, is_live_now, auto_srt),
                daemon=True,
                name=f"recorder-{login}",
            )
            self._active_threads[user_id] = worker
            worker.start()
            self._debug_log(f"recording thread started for {login}")
