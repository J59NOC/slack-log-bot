# 複数の bot を動かす

1チャンネル群につき1体、という分け方ができます。たとえば公開チャンネル用と
プライベートチャンネル用で、保存先の Google アカウントごと分ける場合です。

**プロファイル**を使うと、1つのチェックアウトから複数の bot を動かせます。
コードは共有され、Slack アプリ・Google アカウント・保存先だけが分かれます。
`git pull` 1回で全部の bot が更新されます。

bot 同士は一切通信しないので、**別々のマシンで動かしても構いません**。
1台に同居させるときだけプロファイルが要ります。

> [!WARNING]
> **1つのチャンネルに2体 invite しないでください。** 同じ発言が両方のシートに記録されます。

## 設定ファイルの置き場所

```
slack-log-bot/
├── client_secret.json          ← 共通で使う場合はここ（プロファイル側にもあればそちら優先）
└── profiles/
    ├── public/
    │   ├── .env                ← Slack トークン・保存先ID
    │   └── drive_token.json    ← Google アカウントA
    └── leaders/
        ├── .env
        └── drive_token.json    ← Google アカウントB
```

> `--profile` を付けずに実行すると、これまで通りチェックアウト直下の `.env` を
> 読みます。1体だけ動かしている既存の環境はそのまま動きます。

## セットアップ

**Slack App は bot ごとに作成が必要です**（[Step 1](setup.md#step-1-slack-appの作成) を bot の数だけ実施）。
`features.bot_user.display_name` を `slack-log-bot-private` のように変えてください。
ASCII のみです。

セットアップは `--profile` を付けて、bot の数だけ繰り返します。

```bash
uv run setup.py --profile public
uv run setup.py --profile leaders
```

保存先を分けるので、[Step 4](setup.md#step-4-保存先を決めてセットアップ) の判断も bot ごとに行います。
共有ドライブに置くなら `--parent` を添えます。

```bash
uv run setup.py --profile leaders --parent 'https://drive.google.com/drive/folders/xxxxx'
```

## 実行と常駐

実行時は `SLACK_LOG_PROFILE` で切り替えます。

```bash
SLACK_LOG_PROFILE=leaders uv run main.py
SLACK_LOG_PROFILE=leaders uv run backfill.py --days 90
```

常駐化には `deploy/systemd/` のテンプレートユニットを使います。`@` の後ろが
プロファイル名です。ユニット内のユーザー名とパス（`slackbot`、`/home/slackbot/slack-log-bot`）は
環境に合わせて書き換えてください。

```bash
sudo cp deploy/systemd/slack-log-*@.* /etc/systemd/system/
sudo systemctl enable --now slack-log-bot@public
sudo systemctl enable --now slack-log-bot@leaders
sudo systemctl enable --now slack-log-collect@public.timer
```

Docker で動かす場合は、`compose.yaml` のサービスを bot ごとに増やします
（[running.md の「Docker で動かす」](running.md#docker-で動かす)）。

## 別々のワークスペースで使う

`setup.py` が作る Drive フォルダと索引スプレッドシートには**ワークスペース名が入る**ため、
同じ Google アカウントでも混ざりません。

```
📁 Slack ログ - 研究室        ← ワークスペースA
📁 Slack ログ - サークル      ← ワークスペースB
```

> 名前を分けていないと、2つ目のワークスペースが1つ目のフォルダを名前で見つけて再利用し、
> 同名チャンネル（`#general` など）のログが同じスプレッドシートに混ざります。
