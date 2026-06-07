#!/usr/bin/env sh
set -eu

WIN_REPO_PATH="$(wslpath -w "$PWD")"
WIN_SCRIPT_PATH="${WIN_REPO_PATH}\\build_for_windows_on_wsl.ps1"

if [ "$#" -gt 0 ]; then
  OUTPUT_DIR="$*"
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$WIN_SCRIPT_PATH" -RepoPath "$WIN_REPO_PATH" -OutputDir "$OUTPUT_DIR"
else
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$WIN_SCRIPT_PATH" -RepoPath "$WIN_REPO_PATH"
fi
