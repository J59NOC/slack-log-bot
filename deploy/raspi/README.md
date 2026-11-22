# Raspberry Pi へのデプロイ

Ubuntu Server for Raspberry Pi（**arm64**）の cloud-init 設定です。
SDカードを書いたあと、ブートパーティション（`system-boot`）にこの2ファイルを置くだけで、
起動時にリポジトリの取得・venv構築・systemd登録まで済みます。

```bash
cp user-data network-config /Volumes/system-boot/
```

Raspberry Pi で動かすときの OS の選び方や、Raspberry Pi OS に手で入れる場合は
[docs/running.md](../../docs/running.md#raspberry-pi-などのヘッドレス機で動かす) を参照してください。

## 事前に書き換えるところ

`user-data` の以下を自分の値にしてください（ファイルの先頭にも一覧があります）。

| 項目 | 書き換え方 |
|------|---------|
| `hostname` | 好きな名前。`<名前>.local` で引けるようになる |
| `users[0].name` | 使いたいユーザー名。変えるなら `slackbot` をファイル全体で置換する（systemd の `User=` と `/home/slackbot/...` のパスにも出てくる） |
| `passwd` | `mkpasswd -m sha-512` |
| `ssh_authorized_keys` | `cat ~/.ssh/id_ed25519.pub` |

`network-config` は既定で eth0 の固定 IP（`192.168.1.50/24`、ゲートウェイ `192.168.1.1`）です。
`addresses`・`routes`・`nameservers` を自分のネットワークに合わせて書き換えてください。
DHCP で構わない場合は、ファイル先頭のコメントにある `dhcp4: true` の設定に置き換えます。
無線を使うときだけ `wifis` を足してください（SSID を埋めないまま残すと、存在しないアクセスポイントへ接続を試み続けます）。

## 起動後にやること

**認証情報はこの設定に含めていません。** ブートパーティションは FAT32 で
パーミッションを持てず、カードを差した人なら誰でも読めてしまうためです。
bot は `.env` が無いうちは起動しない（`ConditionPathExists`）ので、
送ってから起動します。

```bash
scp .env client_secret.json drive_token.json slackbot@slack-log-bot.local:~/slack-log-bot/
ssh slackbot@slack-log-bot.local 'chmod 600 ~/slack-log-bot/.env ~/slack-log-bot/*.json && sudo systemctl start slack-log-bot'
```

Google 認証をまだ通していない場合は、手元のPCで `uv run setup.py` を済ませてから
上のファイルを送るのが簡単です。Pi 上で完結させたい場合は `--auth-port` を使います
（[Google 認証をヘッドレスで通す](../../docs/running.md#google-認証をヘッドレスで通す)）。

## 確認

```bash
ssh slackbot@slack-log-bot.local
systemctl status slack-log-bot          # 常駐
journalctl -u slack-log-bot -f          # ログ
systemctl status slack-log-bot-install  # 初回のクローンとvenv構築
systemctl list-timers slack-log-bot*    # 週次収集
```

## 注意

- **arm64 のイメージを使ってください。** 32bit（armhf）だと `cryptography` の
  ホイールが無く、Rust でのソースビルドになります。
- Python 3.10 以上が必要です。Ubuntu 24.04 以降なら問題ありません。
- 初回起動は pip の取得で数分かかります。`slack-log-bot-install.service` の完了を待ってください。
- この設定が作る systemd ユニットは1体運用用です（チェックアウト直下の `.env` を読みます）。
  複数の bot を動かす場合は [docs/multiple-bots.md](../../docs/multiple-bots.md) を参照してください。
