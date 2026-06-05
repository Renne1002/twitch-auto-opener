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

### 補足

- exe には Python ランタイムと Python パッケージは同梱されるため、通常は別途 Python / mise は不要です。
- `recording.tools.streamlink_path` と `recording.tools.ffmpeg_path` は設定で別パスに変更できます。
- Windows のスタートアップ登録に追加の外部ツールは不要です。`APPDATA` 配下の Startup フォルダへ `.cmd` を書き込みます。

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
mise run build:windows_on_wsl # [output-dir]
```
