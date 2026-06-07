# Twitch Auto Opener

指定した Twitch 配信者がライブ開始したら、指定 Chrome プロファイルで自動的に配信ページを開きつつ、配信の録画保存も行うアプリです。

## 前提

- Windows 10/11
- Chrome がインストールされていること
- Chrome に対象メールアドレスのプロファイルが存在すること
- Twitch Developer で取得した `client_id` / `client_secret`
- 設定ファイル `config.toml`

## 実行時に必要な依存関係

### 必須

- Google Chrome 本体
- 対象 Chrome プロファイルのユーザー データ
- Twitch API へアクセスできるネットワーク接続
- `config.toml` に設定した Twitch API クレデンシャル

### 配信録画を有効にする場合

- `streamlink` 実行ファイル
- `ffmpeg` 実行ファイル
  `recording.convert_to_mp4 = true` の場合に必要です。`false` なら不要です。

### 自動字幕生成を有効にする場合

- `faster-whisper` 実行ファイル（[Faster-Whisper-XXL](https://github.com/Purfview/whisper-standalone-win) など）
  - `record = true` かつ `auto_srt = true` の配信者の録画セッションが終了したタイミングで、録画ファイル（`.ts`）を入力として字幕（`.srt`）を自動生成します。
  - 字幕生成に失敗した場合は `recording.fastwhisper.retry_max_failures` 回リトライします。最終的に失敗しても録画ファイルは保持されます（非致命）。
  - 実行ファイルのパスは `recording.fastwhisper.fast_whisper_path` で指定します。

### 補足

- exe には Python ランタイムと Python パッケージは同梱されるため、通常は別途 Python / mise は不要です。
- `recording.tools.streamlink_path` と `recording.tools.ffmpeg_path` は設定で別パスに変更できます。
- Windows のスタートアップ登録に追加の外部ツールは不要です。`startup.enabled = true` のときに `APPDATA` 配下の Startup フォルダへ `.cmd` を書き込みます。

## セットアップ

```bash
mise run install && mise run init
# `config.toml` を編集
```

## 実行

```bash
mise run dev
```

## ビルド

```bash
mise run build

# or
mise run build:windows_on_wsl "C:\\Users\\<username>\\Desktop\\twitch-build"
```

## 主要設定項目

### 配信者フラグ（`[streamer_default_config]` / `[streamer_configs]`）

| キー        | 既定値  | 説明                                                                 |
| ----------- | ------- | -------------------------------------------------------------------- |
| `auto_open` | `true`  | 配信開始時にブラウザで自動オープン                                   |
| `record`    | `false` | 配信開始時に自動録画                                                 |
| `auto_srt`  | `false` | 録画完了後に字幕（`.srt`）を自動生成。`record = true` との併用が必要 |

### 字幕生成設定（`[recording.fastwhisper]`）

| キー                  | 既定値           | 説明                                                                  |
| --------------------- | ---------------- | --------------------------------------------------------------------- |
| `fast_whisper_path`   | `faster-whisper` | faster-whisper 実行ファイルのパス                                     |
| `model`               | `base`           | 使用モデル（`base` / `small` / `medium` / `large-v3` / `turbo` など） |
| `device`              | `cpu`            | 推論デバイス（`cpu` / `cuda`）                                        |
| `language`            | `""`             | 文字起こし言語（空文字で自動判定）                                    |
| `threads`             | `0`              | 使用スレッド数（`0` で自動）                                          |
| `max_line_width`      | `100`            | 字幕 1 行あたりの最大文字幅                                           |
| `retry_max_failures`  | `3`              | 字幕生成失敗時の最大再試行回数                                        |
| `retry_delay_seconds` | `2`              | 再試行待機時間（秒）                                                  |
