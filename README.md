# Twitch Auto Opener

指定した Twitch 配信者がライブ開始したら、指定 Chrome プロファイルで自動的に配信ページを開きつつ、配信の録画保存も行うアプリです。

## 前提

- Windows 10/11
- mise
- Chrome に対象メールアドレスのプロファイルが存在
- Twitch Developer で取得した `client_id` / `client_secret`
- `streamlink` と `ffmpeg` が実行可能（Option: 配信録画する場合）

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
