from __future__ import annotations

import json
import subprocess
import webbrowser
from dataclasses import dataclass
from pathlib import Path


class ChromeLaunchError(RuntimeError):
    """Raised when chrome launch preconditions fail."""


@dataclass
class ChromeTarget:
    chrome_path: Path
    user_data_dir: Path
    profile_directory: str


def _default_chrome_path() -> Path:
    candidates = [
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise ChromeLaunchError("chrome.exe not found; set chrome_path in config.toml")


def _default_user_data_dir() -> Path:
    base = Path.home() / "AppData/Local/Google/Chrome/User Data"
    if not base.exists():
        raise ChromeLaunchError("Chrome user data directory not found; set chrome_user_data_dir")
    return base


def _load_profiles(user_data_dir: Path) -> dict[str, dict]:
    local_state = user_data_dir / "Local State"
    if not local_state.exists():
        raise ChromeLaunchError(f"Chrome Local State not found: {local_state}")

    raw = local_state.read_text(encoding="utf-8")
    payload = json.loads(raw)
    profile_info = payload.get("profile", {}).get("info_cache", {})
    if not isinstance(profile_info, dict):
        raise ChromeLaunchError("unexpected Local State structure: profile.info_cache")
    return profile_info


def resolve_chrome_target(
    profile_email: str, chrome_path: str | None, chrome_user_data_dir: str | None
) -> ChromeTarget:
    chrome_exe = Path(chrome_path) if chrome_path else _default_chrome_path()
    user_data_dir = Path(chrome_user_data_dir) if chrome_user_data_dir else _default_user_data_dir()

    if not chrome_exe.exists():
        raise ChromeLaunchError(f"chrome path does not exist: {chrome_exe}")
    if not user_data_dir.exists():
        raise ChromeLaunchError(f"chrome user data directory does not exist: {user_data_dir}")

    profiles = _load_profiles(user_data_dir)
    expected = profile_email.strip().lower()
    for profile_dir, meta in profiles.items():
        gaia = str(meta.get("user_name", "")).strip().lower()
        if gaia == expected:
            return ChromeTarget(
                chrome_path=chrome_exe,
                user_data_dir=user_data_dir,
                profile_directory=profile_dir,
            )

    known = ", ".join(sorted(str(meta.get("user_name", "")) for meta in profiles.values()))
    raise ChromeLaunchError(
        "target chrome profile email not found. "
        f"expected={profile_email}, known_profiles=[{known}]"
    )


def open_stream_url(target: ChromeTarget, url: str) -> None:
    command = [
        str(target.chrome_path),
        f"--user-data-dir={target.user_data_dir}",
        f"--profile-directory={target.profile_directory}",
        "--new-tab",
        url,
    ]
    subprocess.Popen(command)  # noqa: S603


def open_url_default_browser(url: str) -> None:
    webbrowser.open_new_tab(url)
