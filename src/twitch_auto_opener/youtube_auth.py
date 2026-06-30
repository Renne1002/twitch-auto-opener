from __future__ import annotations

import argparse
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from twitch_auto_opener.config import load_config

_YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="YouTube OAuth bootstrap for twitch-auto-opener")
    parser.add_argument(
        "--config",
        default="config.toml",
        help="Path to config.toml (default: ./config.toml)",
    )
    return parser.parse_args()


def _resolve_path(raw_path: str, app_base_dir: Path) -> Path:
    candidate = Path(raw_path.replace("\\", "/")).expanduser()
    if not candidate.is_absolute():
        candidate = app_base_dir / candidate
    return candidate.resolve()


def run() -> None:
    args = _parse_args()
    config_path = Path(args.config).expanduser().resolve()
    config = load_config(config_path)

    config_base_dir = config_path.parent
    client_secrets = _resolve_path(config.youtube.auth.client_secrets_file, config_base_dir)
    token_file = _resolve_path(config.youtube.auth.token_file, config_base_dir)

    if not client_secrets.exists():
        raise FileNotFoundError(f"youtube client secret file not found: {client_secrets}")

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secrets), scopes=_YOUTUBE_SCOPES
    )
    creds = flow.run_local_server(port=0, authorization_prompt_message="")

    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json(), encoding="utf-8")
    print(f"[info] youtube token saved: {token_file}")


if __name__ == "__main__":
    run()
