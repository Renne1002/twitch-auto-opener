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

### ライブコメント保存を有効にする場合

- Twitch IRC へ接続できるネットワーク
  - 本アプリは匿名 read-only 接続（`justinfan`）でコメントを取得します。
  - VOD 後追い補完は行いません。ライブ中に取得できたイベントのみ保存します。

### 自動字幕生成を有効にする場合

- `faster-whisper` 実行ファイル（[Faster-Whisper-XXL](https://github.com/Purfview/whisper-standalone-win) など）
  - `record = true` かつ `auto_srt = true` の配信者の録画セッションが終了したタイミングで、録画ファイル（`.ts`）を入力として字幕（`.srt`）を自動生成します。
  - 字幕生成に失敗した場合は `recording.fastwhisper.retry_max_failures` 回リトライします。最終的に失敗しても録画ファイルは保持されます（非致命）。
  - 実行ファイルのパスは `recording.fastwhisper.fast_whisper_path` で指定します。

### YouTube 自動アップロードを有効にする場合

- Google Cloud Project
- YouTube Data API v3 を利用できる Google アカウント
- OAuth クライアント情報（Desktop App）
- 初回認証で生成した `token.json`

> 注意
> - YouTube Data API v3 の動画アップロードは、通常チャンネルではサービスアカウントではなく OAuth 2.0 が必要です。
> - `recording.convert_to_mp4 = true` の場合、録画終了時に `.ts` は `.mp4` に変換されて削除されます。YouTube アップロード対象は `.ts` のため、アップロード運用時は `recording.convert_to_mp4 = false` を推奨します。

### 補足

- exe には Python ランタイムと Python パッケージは同梱されるため、通常は別途 Python / mise は不要です。
- `recording.tools.streamlink_path` と `recording.tools.ffmpeg_path` は設定で別パスに変更できます。
- Windows のスタートアップ登録に追加の外部ツールは不要です。`startup.enabled = true` のときに `APPDATA` 配下の Startup フォルダへ `.cmd` を書き込みます。

## セットアップ

```bash
mise run install && mise run init
# `config.toml` を編集
```

### YouTube OAuth 初回認証

1. GCP で以下を設定
  - Google Cloud Project を作成
  - `YouTube Data API v3` を有効化
  - OAuth 同意画面を作成（External で可）
  - OAuth クライアント ID を作成（種類: Desktop App）
  - クライアントシークレット JSON をダウンロード
2. `config.toml` に以下を設定
  - `youtube.auth.client_secrets_file`
  - `youtube.auth.token_file`
3. 実行ディレクトリをプロジェクトルートに移動

```bash
cd /path/to/twitch-auto-opener
```

4. 初回認証を実行（どちらか1つ）

開発中（ソースから実行）:

```bash
python -m twitch_auto_opener.youtube_auth --config config.toml
```

スクリプトをインストール済み（`youtube-auth` が PATH にある）:

```bash
youtube-auth --config config.toml
```

5. ブラウザで同意後、`youtube.auth.token_file` に設定したパスへトークンが保存されることを確認

補足:

- `--config config.toml` は「現在の作業ディレクトリから見た相対パス」です。
- `youtube.auth.client_secrets_file` と `youtube.auth.token_file` も、相対パスの場合は実行時の作業ディレクトリ基準で解決されます。
- そのため、迷った場合は必ずプロジェクトルートで実行するのが安全です。

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

### コメント保存設定（`[recording.chat]`）

| キー                        | 既定値  | 説明                                                                |
| --------------------------- | ------- | ------------------------------------------------------------------- |
| `enabled`                   | `false` | IRCリアルタイムコメント保存を有効化                                 |
| `capture_moderation_events` | `true`  | `CLEARCHAT`/`CLEARMSG`/`USERNOTICE`/`NOTICE`/`ROOMSTATE` も保存する |
| `reconnect_delay_seconds`   | `5`     | IRC 切断時の再接続待機時間（秒）                                    |
| `connect_timeout_seconds`   | `15`    | IRC 接続タイムアウト（秒）                                          |
| `read_timeout_seconds`      | `120`   | IRC 読み取りタイムアウト（秒）                                      |
| `debug`                     | `false` | コメント保存のデバッグログ                                          |

### YouTube 全体設定（`[youtube]`）

| キー                    | 既定値                                | 説明                                                      |
| ----------------------- | ------------------------------------- | --------------------------------------------------------- |
| `enabled`               | `true`                                | YouTube 自動アップロード機能の有効化                      |
| `min_age_days`          | `7`                                   | アップロード対象にする録画ファイルの最低経過日数（最小2） |
| `tick_interval_seconds` | `300`                                 | アップロード処理の実行間隔（秒）                          |
| `state_file`            | `./VOD/.youtube_upload_state.json`    | 重複防止・リトライ制御用の状態ファイル                    |
| `history_file`          | `./VOD/.youtube_upload_history.jsonl` | 監査用の履歴ファイル（JSONL）                             |
| `max_uploads_per_tick`  | `1`                                   | 1回の tick で処理する最大アップロード本数                 |

### YouTube 認証設定（`[youtube.auth]`）

| キー                  | 既定値 | 説明                                   |
| --------------------- | ------ | -------------------------------------- |
| `client_secrets_file` | `""`   | GCP で取得した OAuth クライアント JSON |
| `token_file`          | `""`   | 初回認証で生成するトークン保存先       |

### YouTube デフォルト動画設定（`[youtube.defaults]`）

| キー                     | 既定値                               | 説明                                                  |
| ------------------------ | ------------------------------------ | ----------------------------------------------------- |
| `privacy_status`         | `unlisted`                           | 公開範囲 (`private` / `unlisted` / `public`)          |
| `title_template`         | `【Twitch】{id} {ts:%Y-%m-%d %H:%M}` | タイトルテンプレート（`{id}` / `{ts:strftime}` 対応） |
| `category_id`            | `20`                                 | YouTube 動画カテゴリ ID                               |
| `made_for_kids`          | `false`                              | 子ども向けコンテンツ設定                              |
| `delete_ts_after_upload` | `false`                              | アップロード成功後に `.ts` を削除するか               |

### YouTube クォータ制御（`[youtube.quota]`）

| キー                                | 既定値                | 説明                                         |
| ----------------------------------- | --------------------- | -------------------------------------------- |
| `quota_reset_timezone`              | `America/Los_Angeles` | 日次クォータリセット判定に使うタイムゾーン   |
| `skip_after_quota_exceeded_for_day` | `true`                | クォータ超過時に当日アップロードを停止するか |

### Streamer ごとの YouTube 上書き設定

`[streamer_default_config.youtube]` と `streamer_configs.<login>.youtube` で配信者単位の上書きが可能です。

- `enabled`: 配信者ごとのアップロード有効/無効
- `title_template`: 配信者専用タイトルテンプレート
- `privacy_status`: 配信者専用公開範囲
- `delete_ts_after_upload`: 配信者専用のアップロード後削除フラグ

例:

```toml
[streamer_default_config.youtube]
enabled = false

[streamer_configs]
streamer1 = { youtube = { enabled = true } }
streamer2 = { youtube = { enabled = true, title_template = "【TW】{id} {ts:%Y%m%d_%H%M}", delete_ts_after_upload = true } }
```

## コメント保存フォーマット

`recording.chat.enabled = true` のとき、録画ファイルと同じ配信者ディレクトリに以下を保存します。

- `<login>_<session_ts>.chat.session.json`
  - セッションメタデータ（stream_id、アンカー時刻、統計）
- `<login>_<session_ts>.chat.jsonl`
  - 1行1イベントの JSONL
  - 主な項目: `event_type`, `sent_at_utc`, `rel_stream_ms`, `rel_record_ms`, `message`, `tags`

同期用アンカーは以下を両方保持します。

- `stream_started_at_utc`: Twitch 側の配信開始時刻
- `recorder_first_byte_at_utc`: 録画側の開始アンカー時刻

`rel_stream_ms` と `rel_record_ms` は用途別に使い分けられます。mpv 連動用途では通常 `rel_record_ms` を使用します。

## YouTube 状態ファイルと履歴ファイル

- `youtube.state_file`
  - 制御用ファイル
  - 重複アップロード防止、失敗リトライ、クォータ当日停止状態を保持
- `youtube.history_file`
  - 監査用ファイル（JSONL）
  - `upload_succeeded`, `upload_failed`, `upload_skipped_quota_block`, `delete_succeeded`, `delete_failed` を追記
