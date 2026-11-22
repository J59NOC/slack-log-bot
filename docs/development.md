# 開発者向け

## プロジェクト構成

```
slack-log-bot/
├── main.py               # リアルタイム収集 (Socket Mode) + Slackコマンドの入口
├── collect_weekly.py     # 週次定期収集 (cron/systemd timer) の入口
├── backfill.py           # 過去履歴の一括取り込みの入口
├── setup.py              # セットアップウィザード（対話式）の入口
├── src/                  # 本体（ドメイン駆動設計のレイヤー構成）
│   ├── domain/           # チャンネル・メッセージ・添付ファイルと、その規則（外部 API を知らない）
│   ├── application/      # ユースケース（記録・編集・削除、履歴の収集、共有の同期、改名への追従）
│   ├── infrastructure/   # 外部サービスを使った実装
│   │   ├── google/       # OAuth2認証・Drive・添付ファイルの保存・セットアップ時の準備と点検
│   │   │   └── sheets/   # スプレッドシートの読み書き・書式・説明タブと索引
│   │   ├── slack/        # Slack API（ユーザー・チャンネル・履歴・ファイル・トークン確認）
│   │   ├── settings.py   # 環境変数（.env）とプロファイルの読み込み
│   │   └── env_file.py   # .env の書き出し（セットアップ用）
│   ├── presentation/     # Slackイベントのハンドラ・メンションコマンド・CLI
│   │   └── setup/        # セットアップウィザードの画面と流れ
│   └── bootstrap.py      # 部品の組み立て
├── scripts/              # 保守用スクリプト（uv run python -m scripts.<名前> で実行）
│   ├── cleanup_duplicate_files.py    # 重複した添付ファイルの掃除
│   └── relink_attachments.py         # 既存の添付セルをファイル名リンクに変換
├── tests/                # 単体テストとシナリオテスト（golden/ に API 呼び出しと画面出力の記録）
├── docs/                 # ドキュメントと README の画像
├── privacy-policy/       # Google OAuth 用のホームページとプライバシーポリシー（GitHub Pages で公開）
├── .github/workflows/    # CI（ci.yml）と、privacy-policy/ を GitHub Pages に公開する workflow（pages.yml）
├── slack-app-manifest.yml # Slack Appマニフェスト（貼るだけでApp設定完了）
├── pyproject.toml        # プロジェクト設定・依存パッケージ・Ruff の設定
├── uv.lock               # 依存パッケージのバージョン固定（uv）
├── requirements.txt      # pip 用の依存パッケージ（uv を使わない場合）
├── .env.example          # 環境変数テンプレート
├── Dockerfile            # コンテナイメージ
├── compose.yaml          # Docker Compose（1サービス = 1 bot）
├── deploy/
│   ├── systemd/          # 複数bot用のテンプレートユニット（slack-log-bot@<名前>）
│   └── raspi/            # Raspberry Pi の cloud-init 一式
└── profiles/             # 複数bot運用時の設定（bot ごとに .env とトークン／git管理外）
```

`src/` の依存は外側から内側への一方向です（presentation → application → domain ← infrastructure）。
domain は他のレイヤーを import せず、Google や Slack の都合は infrastructure の中に閉じています。
どの実装を組み合わせるかは `src/bootstrap.py` だけが知っています。

## 保守スクリプト

`scripts/` の保守用スクリプトは bot の動作には関わらず、必要なときに手元から実行します（Docker イメージには含めていません）。
どれも既定では対象を表示するだけで、`--apply` を付けたときだけ書き換えます。

```bash
uv run python -m scripts.relink_attachments            # 対象を表示するだけ
uv run python -m scripts.relink_attachments --apply    # 実際に書き換える
```

## テスト

テストは Google と Slack の API をフェイクに置き換えているため、`setup.py` 実行前でも実行できます。
依存関係を同期してから、次を実行します。

```bash
uv sync
uv run python -m unittest discover -s tests -v
```

ドメインの規則やユースケースの単体テストに加えて、シナリオテストが実際の使われ方をなぞります。

- `tests/test_scenarios.py`: 投稿・編集・削除・メンバーの出入り・改名・週次収集などのイベントを流し、
  そのとき呼んだ Sheets／Drive／Slack の API と中身を `tests/golden/` の記録と突き合わせます。
- `tests/test_setup_wizard.py`: `setup.py` に入力を流し、画面に出た文字・API 呼び出し・
  書き出した `.env` を `tests/golden/setup/` の記録と突き合わせます。

書式や共有、セットアップの挙動を意図して変えたときは、記録を書き直して差分を確認してください。

```bash
UPDATE_GOLDEN=1 uv run python -m unittest tests.test_scenarios tests.test_setup_wizard
git diff tests/golden/
```

共有ドライブ配下への実 API 保存を確認する場合は、保存先の設定と Google 認証を済ませたうえで、
次を実行します。テスト用のフォルダ、スプレッドシート、添付ファイルを作成し、終了時に削除します。

```bash
SLACK_LOG_INTEGRATION=1 uv run python -m unittest \
  tests.test_google_drive_integration -v
```

共有ドライブの管理設定や bot の権限によって失敗する場合があります。失敗時はテスト用の
フォルダが残ることがあるため、Drive 上で `__slack-log-bot-integration-` を確認してください。

## コードの検査と整形

[Ruff](https://docs.astral.sh/ruff/) で lint と整形をしています。設定は `pyproject.toml` の `[tool.ruff]` にあります。
Ruff は開発用の依存なので `uv sync` で入ります（Docker イメージには入りません）。

```bash
uv run ruff check .          # lint
uv run ruff check --fix .    # 自動で直せるものを直す
uv run ruff format .         # 整形
```

## CI

GitHub Actions（`.github/workflows/ci.yml`）が、pull request と `main` への push のたびに次を実行します。
Actions の画面から手動で実行することもできます（`workflow_dispatch`）。

ただし、どのジョブも読まないファイル（Markdown、`docs/`、`privacy-policy/`、`deploy/`、`compose.yaml`、`LICENSE`、
`pages.yml`）だけを変えたときは実行しません。除外する側を `paths-ignore` に並べているので、
それ以外のファイルを足したときは何もしなくても CI の対象になります。
CI が読むようになったファイルは、`paths-ignore` から外してください。

| ジョブ | 内容 |
|--------|------|
| Ruff | `ruff check` と `ruff format --diff`（整形が必要な箇所があれば差分を出して失敗） |
| Test | Python 3.10・3.13・3.14 でテストを実行（3.10 は対応する下限、3.13 は Docker イメージと同じ版） |
| Docker image | イメージをビルドし、その中で各入口（`main.py` など）を import できるか確かめる |

依存は `uv sync --locked` で入れるため、`pyproject.toml` を変えて `uv.lock` を更新し忘れると失敗します。
`uv lock` を実行して、`uv.lock` も一緒にコミットしてください。
CI で使う uv の版は workflow の `UV_VERSION` で固定しています。手元の uv を上げたときは、合わせて上げてください。

push する前に手元で同じ確認をするには、次を実行します。

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --diff .
uv run python -m unittest discover -s tests
```

共有ドライブを使う統合テスト（`SLACK_LOG_INTEGRATION=1`）は Google の認証情報が必要なため、CI ではスキップされます。
