<p align="center">
  <img src="docs/banner.png" alt="Slack Log Bot" width="100%">
</p>

# Slack Log Bot

Slack無料プランではメッセージ履歴が一定期間で消えてしまいます。
このbotは、Slackの投稿・スレッド返信・添付ファイルを自動で **Google スプレッドシート** と **Google Drive** に保存します。

## 特徴

- **リアルタイム収集** — Socket Modeで常駐し、投稿を即時記録。ファイルアップロードはバックグラウンド処理のためメッセージ順序が崩れない
- **週次定期収集** — 毎週自動で過去1週間のメッセージを収集
- **過去履歴の一括取り込み** — Slackコマンドまたはスクリプトで既存メッセージをバックフィル
- **Slackコマンド** — botにメンションしてURL表示・バックフィルを実行
- **スレッド対応** — 親メッセージとスレッド返信を隣接して記録。返信は背景色で区別し、**折りたたんで隠せる**。親メッセージには返信数を表示
- **発言者の識別** — 表示名のセルを人ごとに色分けし、投稿者が変わる行に区切り線を表示
- **添付ファイル保存** — PDF・画像等をSlackからダウンロードしてGoogle Driveに自動アップロード。チャンネル別フォルダで整理、Drive URLをスプレッドシートに記録
- **重複防止** — メッセージの一意ID（TS）で自動判定。何度実行しても同じメッセージは追加されない
- **変更への追従** — Slack側で表示名を変えたりメッセージを編集すると、記録済みの行にも反映される
- **削除への追従** — Slackでメッセージを削除すると、その行も消える。添付ファイルはDriveのゴミ箱へ
- **リネーム追従** — スプレッドシートはチャンネルIDで紐づくため、チャンネル名を変えてもログが分断されない
- **アクセス制御** — public channel は共有スプレッドシート、private channel は専用フォルダと専用スプレッドシートに分け、private channel のメンバーだけに共有。参加・退出に応じて自動で追従
- **保存先を選べる** — botアカウントのマイドライブ、または**共有ドライブ**。共有ドライブならファイルの所有者が組織になり、botのアカウントを失ってもログが残る
- **複数botの並行運用** — 1つのチェックアウトから、公開チャンネル用・非公開チャンネル用のように保存先ごと分けた複数のbotを動かせる
- **見やすいスプレッドシート** — グリーンヘッダー・色分け・メッセージ列11ptフォント・列幅調整・オートフィルターを自動適用

## 動作イメージ

> スクリーンショットは旧バージョンのものです（列構成・書式が現在と異なります）。

<table>
  <tr>
    <td width="50%"><strong>Slack</strong></td>
    <td width="50%"><strong>Google Sheets</strong></td>
  </tr>
  <tr>
    <td><img src="docs/screenshot_slack.png" alt="Slack側の動作イメージ"></td>
    <td><img src="docs/screenshot_sheets.png" alt="スプレッドシート側の動作イメージ"></td>
  </tr>
</table>

## はじめかた

必要なもの: Python 3.10 以上、Slack ワークスペースの管理者権限、Google アカウント。

1. Slack App をマニフェスト（[`slack-app-manifest.yml`](slack-app-manifest.yml)）から作る
2. Google Cloud で OAuth クライアント ID を作り、`client_secret.json` を置く
3. リポジトリを取得して `uv sync` で依存を入れる
4. 保存先（マイドライブか共有ドライブか）を決めて `uv run setup.py` を実行する
5. `uv run backfill.py --channel general --days 7` で記録されることを確かめる

各手順の詳細は [docs/setup.md](docs/setup.md) にあります。
動作を確認できたら、[docs/running.md](docs/running.md) の手順で Docker や systemd で常駐させます。

## Slackコマンド

チャンネル内でbotにメンションすると、以下のコマンドが使えます：

| コマンド | 説明 |
|----------|------|
| `@slack-log-bot-private help` | コマンド一覧とスプレッドシートURLを表示 |
| `@slack-log-bot-private url` | そのチャンネルのスプレッドシートURLを表示 |
| `@slack-log-bot-private backfill` | そのチャンネルの過去90日分のログを即座に収集 |
| `@slack-log-bot-private backfill 30` | 過去N日分を収集（日数指定） |

コマンドラインからの収集については [docs/running.md](docs/running.md) を参照してください。

## ドキュメント

| ファイル | 内容 |
|----------|------|
| [docs/setup.md](docs/setup.md) | 導入手順（Slack App の作成からセットアップ、動作確認まで） |
| [docs/running.md](docs/running.md) | 運用（リアルタイム収集・週次収集・バックフィル、Docker・systemd・Raspberry Pi での常駐） |
| [docs/storage.md](docs/storage.md) | 保存先の構成と共有（Drive のフォルダ、共有の自動同期、共有ドライブ、スプレッドシートの列と書式） |
| [docs/multiple-bots.md](docs/multiple-bots.md) | 複数の bot を動かす・別々のワークスペースで使う |
| [docs/troubleshooting.md](docs/troubleshooting.md) | トラブルシューティング |
| [docs/development.md](docs/development.md) | 開発者向け（プロジェクト構成、保守スクリプト、テスト、Ruff） |
| [docs/github-pages.md](docs/github-pages.md) | Google OAuth 用のホームページとプライバシーポリシーの公開 |
| [deploy/raspi/README.md](deploy/raspi/README.md) | Raspberry Pi（Ubuntu Server）への cloud-init デプロイ |

## ライセンス

MIT（[LICENSE](LICENSE)）

このリポジトリは [nnnnnnnnnke/slack-log-bot](https://github.com/nnnnnnnnnke/slack-log-bot) をもとに、
独自に機能追加・改修しているものです。
