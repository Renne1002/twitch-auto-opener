# Twitch Auto Opener

指定した Twitch 配信者がライブ開始したら、指定 Chrome プロファイルで自動的に配信ページを開く Windows 常駐アプリです。

## 前提

- Windows 10/11
- mise
- Chrome に対象メールアドレスのプロファイルが存在
- Twitch Developer で取得した `client_id` / `client_secret`

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
