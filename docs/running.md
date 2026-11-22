# 運用

[導入手順](setup.md) を終えたあとの、収集の実行と常駐のさせ方です。
複数の bot を動かす場合は [multiple-bots.md](multiple-bots.md) も参照してください。

## 収集の3つの入口

| 入口 | 用途 |
|------|------|
| `main.py` | リアルタイム収集。Socket Mode で常駐し、投稿を即時記録 |
| `collect_weekly.py` | 週次収集。常駐が止まっていた間の取りこぼしを回収 |
| `backfill.py` | 過去履歴の一括取り込み |

どれもメッセージの一意ID（TS）で重複を判定するので、併用しても二重記録は発生しません。
Socket Mode は Slack へ外向きに接続するだけなので、どこで常駐させても受信ポートを開ける必要はありません。

> **同じ bot を2箇所で同時に動かさないでください。** 同じアプリトークンで Socket Mode 接続が
> 二重になります。サーバーで常駐させたら、手元や他のマシンで動かしていた `main.py` は止めます。

---

## リアルタイム収集

Socket Modeで常駐させて、投稿をリアルタイムに記録します。

```bash
uv run main.py
```

常駐させるには [Docker](#docker-で動かす) か [systemd](#systemd-で常駐させる) を使います。

## 週次定期収集

```bash
# 過去8日分を収集（7日 + 1日の重複マージン）
uv run collect_weekly.py

# botが参加しているpublic channel全件
uv run collect_weekly.py --public

# 特定チャンネルのみ
uv run collect_weekly.py --channel general

# 日数を指定
uv run collect_weekly.py --days 14
```

定期実行は [systemd timer](#週次収集のタイマー) か、Docker なら [cron](#週次収集とバックフィル) で設定します。

## バックフィル

過去のメッセージをまとめて取り込みます。初回導入時や、取りこぼしを遡って埋めたいときに使います。
Slackからコマンドでも実行できます: `@slack-log-bot-private backfill`

```bash
# 全チャンネル・過去90日分（デフォルト）
uv run backfill.py

# botが参加しているpublic channel全件・過去90日分
uv run backfill.py --public

# 特定チャンネルのみ
uv run backfill.py --channel general

# 過去30日分のみ
uv run backfill.py --days 30
```

> Slackの無料プランでは古いメッセージは既に削除されている場合があります。
> 導入は早ければ早いほどデータを救える量が増えます。

---

## Docker で動かす

Docker（Compose v2）が動くサーバーで常駐させる方法です。

認証情報はイメージに入れず、`profiles/<名前>/` をコンテナにマウントして渡します。
実行時に必要なのは `.env` と `drive_token.json` の2つだけです（`client_secret.json` は
`setup.py` で認証するときにしか使いません）。

### 1. 手元の PC でセットアップする

ブラウザでの Google 認証が必要なので、`setup.py` は手元で実行します。

```bash
uv run setup.py --profile private
```

`profiles/private/.env` と `profiles/private/drive_token.json` ができます。

### 2. サーバーに配置して起動する

```bash
git clone https://github.com/J59NOC/slack-log-bot.git
cd slack-log-bot
# 手元から profiles/private/ をコピーする（例）
#   scp -r profiles/private server:~/slack-log-bot/profiles/
chmod 700 profiles profiles/private && chmod 600 profiles/private/*

UID=$(id -u) GID=$(id -g) docker compose up -d --build
docker compose logs -f
```

コンテナは `profiles/` 以外を読み取り専用で動かします。Google のトークンを更新したときは
`drive_token.json` に書き戻すので、コンテナはホストのユーザーと同じ UID で動かします
（`UID`/`GID` を渡さない場合は 1000:1000）。

サービス名（`private`）がプロファイル名です。bot を増やすときは、`compose.yaml` の
`private` を複製して、サービス名と `SLACK_LOG_PROFILE` を変えてください。

### 週次収集とバックフィル

```bash
# 週次収集（cron などで定期実行する）
docker compose run --rm private python collect_weekly.py
# 過去分の取り込み
docker compose run --rm private python backfill.py --days 14
```

cron の例（毎週月曜 9:00）:

```cron
0 9 * * 1  cd /path/to/slack-log-bot && UID=1000 GID=1000 docker compose run --rm private python collect_weekly.py >> collect_weekly.log 2>&1
```

### 更新

```bash
git pull
UID=$(id -u) GID=$(id -g) docker compose up -d --build
```

---

## systemd で常駐させる

Linux で bot を1体だけ動かす場合のユニットです。ユーザー名（`ubuntu`）とパス
（`/home/ubuntu/slack-log-bot`）は環境に合わせて書き換えてください。
複数の bot を動かす場合は、`deploy/systemd/` のテンプレートユニットを使います
（[multiple-bots.md](multiple-bots.md#実行と常駐)）。

### 常駐（リアルタイム収集）

```bash
sudo tee /etc/systemd/system/slack-log-bot.service << 'EOF'
[Unit]
Description=Slack Log Bot - Realtime (Socket Mode)
After=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/slack-log-bot
ExecStart=/home/ubuntu/slack-log-bot/.venv/bin/python main.py
Restart=on-failure
RestartSec=10
StandardOutput=append:/var/log/slack-log-bot.log
StandardError=append:/var/log/slack-log-bot.log

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now slack-log-bot.service
```

### 週次収集のタイマー

```bash
# サービスファイル
sudo tee /etc/systemd/system/slack-log-bot-weekly.service << 'EOF'
[Unit]
Description=Slack Log Bot - Weekly Collection
After=network-online.target

[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/home/ubuntu/slack-log-bot
ExecStart=/home/ubuntu/slack-log-bot/.venv/bin/python collect_weekly.py
StandardOutput=append:/var/log/slack-log-bot-weekly.log
StandardError=append:/var/log/slack-log-bot-weekly.log
EOF

# タイマーファイル（毎週月曜 9:00）
sudo tee /etc/systemd/system/slack-log-bot-weekly.timer << 'EOF'
[Unit]
Description=Run Slack Log Bot weekly

[Timer]
OnCalendar=Mon *-*-* 09:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now slack-log-bot-weekly.timer
```

---

## Raspberry Pi などのヘッドレス機で動かす

常時稼働させるなら Raspberry Pi 4 で十分動きます。

- **64bit（arm64）の OS を使ってください。** ネイティブ拡張を持つ依存パッケージ（`cryptography` など）は、
  arm64 ならビルド済みのホイールが入ります。32bit 版（armhf）では Rust でのソースビルドになります
- Python 3.10 以上が必要です。Raspberry Pi OS なら Bookworm 以降、Ubuntu Server なら 24.04 以降を
  使ってください（**Bullseye は Python 3.9 なので動きません**）

### cloud-init で一式を用意する（Ubuntu Server）

`deploy/raspi/` に Ubuntu Server for Raspberry Pi 向けの cloud-init 一式があります。
SDカードを焼いて `user-data` と `network-config` をブートパーティションに置けば、起動時に clone から
仮想環境の構築、systemd への登録までが済みます。ホスト名・ユーザー名・IPアドレスなど
書き換える箇所と、認証情報を送る起動後の手順は [deploy/raspi/README.md](../deploy/raspi/README.md) を参照してください。

Raspberry Pi OS などに手で入れる場合は、[導入手順](setup.md) と
[systemd で常駐させる](#systemd-で常駐させる) の手順がそのまま使えます。

### Google 認証をヘッドレスで通す

`setup.py` の Google 認証はブラウザを開いて `localhost` で受け取るため、SSH 越しだとそのままでは完了できません。方法は2つあります。
以下の `slackbot@slack-log-bot.local` は `deploy/raspi/` の既定値です。ユーザー名とホスト名は環境に合わせてください。

**A. SSHポートフォワード**（Pi 上で完結させたい場合）

手元の端末から:

```bash
ssh -L 8080:localhost:8080 slackbot@slack-log-bot.local
```

Pi 側で:

```bash
uv run setup.py --auth-port 8080
# uv を入れていない場合（deploy/raspi の cloud-init で構築した場合など）
.venv/bin/python setup.py --auth-port 8080
```

表示されたURLを**手元のブラウザ**で開けば、リダイレクトがフォワード経由でPiに届きます。

**B. 認証だけ手元のPCで済ませる**（簡単）

手元のPCで `uv run setup.py` を通したあと、生成された `client_secret.json` と
`drive_token.json` を Pi にコピーします。`setup.py` は既存の認証を再利用するので、
Pi 側ではブラウザが不要になります。

```bash
scp client_secret.json drive_token.json slackbot@slack-log-bot.local:~/slack-log-bot/
```
