# Twitch Auto Opener

指定したTwitch 配信者の配信を監視し、以下の処理を提供します。

- 配信開始を監視し Chrome 自動起動
- 配信の録画
- 配信のコメント保存
- 配信終了後に自動で文字起こし生成
- 一定期間経過した録画を YouTube へ自動アップロード

## 前提環境

- Windows 10/11
- Chrome がインストール済かつプロファイルが作成済
- Twitch Developer で取得した `client_id` / `client_secret`
- 設定ファイル `config.toml`

## 機能モジュール

### 1. 監視・起動モジュール

- Twitch API でライブ状態を定期監視
- 配信開始イベントで Chrome を対象プロファイルで起動
- Windows 起動時の自動起動登録（任意）
- 多重起動防止（Windows mutex）

補足:

- 監視ループは `monitor.check_interval_seconds` 間隔で実行されます。
- 起動関連の設定値は `config.sample.toml` の `[chrome]`, `[monitor]`, `[startup]` を参照してください。

### 2. 録画モジュール

- `streamlink` を利用して `.ts` 録画を実行
- Option: 必要に応じて `ffmpeg` で `.mp4` へ変換

### 3. コメント収集モジュール

- Twitch IRC へ匿名 read-only 接続 (`justinfan`) してライブコメントを取得
- セッションメタデータ (`*.chat.session.json`) とイベントログ (`*.chat.jsonl`) を保存
- 配信開始時刻基準・録画開始基準の相対時刻を保持

保存ファイル:

- `<login>_<session_ts>.chat.session.json`
- `<login>_<session_ts>.chat.jsonl`

主なイベント項目:

- `event_type`
- `sent_at_utc`
- `rel_stream_ms`
- `rel_record_ms`
- `message`
- `tags`

補足:

- VOD 後追い補完は行いません。ライブ中に受信できたイベントのみ保存します。

### 4. 字幕生成モジュール

- `auto_srt = true` の配信者について、録画終了後に `faster-whisper` で `.srt` を生成
- `record = true` と併用してください。

### 5. YouTube アップロードモジュール

- 対象 `.ts` ファイルをキュー走査して YouTube へアップロード
- クォータ超過・レート制限・失敗時のバックオフとリトライ
- 長尺動画の分割アップロード
- 状態管理 (`youtube.state_file`) と監査ログ (`youtube.history_file`) の永続化

補足:

- アップロード運用時は `recording.convert_to_mp4 = false` に設定する必要があります

## YouTube OAuth 初回認証

1. Google Cloud Project を作成
2. `YouTube Data API v3` を有効化
3. OAuth 同意画面を作成（External で可）
4. OAuth クライアント ID（Desktop App）を作成
5. クライアントシークレット JSON を取得
6. `config.toml` の `youtube.auth.client_secrets_file` と `youtube.auth.token_file` を設定
7. 初回認証を実行

開発中（ソースから実行）:

```bash
python -m twitch_auto_opener.youtube_auth --config config.toml
```

スクリプトをインストール済み（`youtube-auth` が PATH にある）:

```bash
youtube-auth --config config.toml
```

## 開発

### セットアップ

```bash
mise run install && mise run init
# config.toml を編集
```

`config.toml` の作成例:

```bash
cp config.sample.toml config.toml
```

### 実行

```bash
mise run dev
```

### ビルド

```bash
mise run build

# or
mise run build:windows_on_wsl "C:\\Users\\<username>\\Desktop\\twitch-build"
```
