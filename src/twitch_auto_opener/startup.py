from __future__ import annotations

import os
from pathlib import Path


def ensure_startup_registration(command: str) -> None:
    appdata = os.getenv("APPDATA")
    if not appdata:
        return

    startup_dir = Path(appdata) / "Microsoft/Windows/Start Menu/Programs/Startup"
    if not startup_dir.exists():
        return

    launcher = startup_dir / "twitch-auto-opener.cmd"
    desired = f"@echo off\r\n{command}\r\n"
    if launcher.exists() and launcher.read_text(encoding="utf-8", errors="ignore") == desired:
        return
    launcher.write_text(desired, encoding="utf-8")
