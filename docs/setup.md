# 導入手順

Slack App を作り、Google Cloud で OAuth クライアントを用意して、セットアップウィザードで保存先を作るまでの手順です。
常駐させる方法は [running.md](running.md)、複数の bot を動かす場合は [multiple-bots.md](multiple-bots.md) を参照してください。

## 前提条件

- Python 3.10以上
- Slackワークスペースの管理者権限（アプリ作成に必要）
- Googleアカウント

---

## Step 1: Slack Appの作成

このリポジトリの [`slack-app-manifest.yml`](../slack-app-manifest.yml) を貼り付けるだけで、
スコープ・イベント・Socket Mode がすべて設定済みのアプリが作られます。

### 1-1. マニフェストからアプリを作成

1. [Slack API](https://api.slack.com/apps) にアクセス
2. **「Create New App」** → **「From an app manifest」** を選択
3. 対象のワークスペースを選んで **「Next」**
4. **YAML** タブを開き、[`slack-app-manifest.yml`](../slack-app-manifest.yml) の中身をまるごと貼り付けて **「Next」**
5. 権限の確認画面が出るので **「Create」**

> アプリ名を変えたい場合は、貼り付ける前に YAML 内の `display_information.name` と
> `features.bot_user.display_name` の両方を書き換えてください。

### 1-2. App-Level Token の生成

App-Level Token だけはマニフェストで作れないため、手動で生成します。

1. 左メニュー **「Basic Information」** → **「App-Level Tokens」** → **「Generate Token and Scopes」**
2. Token名: `socket-token`、Scope: **`connections:write`** を追加して **「Generate」**
3. 生成された `xapp-...` トークンを控えておく（この画面を閉じると再表示できません）

### 1-3. アプリのインストール

1. 左メニュー **「Install App」** → **「Install to Workspace」**
2. 権限を確認して **「許可する」**
3. 表示される **Bot User OAuth Token**（`xoxb-...`）を控えておく
4. 記録したいチャンネルにbotを招待: `/invite @slack-log-bot-private`
   > プライベートチャンネルにも忘れずに招待してください

<details>
<summary><b>マニフェストを使わず手動で設定する場合（既存アプリに追加するときなど）</b></summary>

**OAuth & Permissions** → **Bot Token Scopes** に以下を追加:

| Scope | 用途 |
|-------|------|
| `app_mentions:read` | botへのメンションイベント受信（`app_mention` イベントに必須） |
| `channels:history` | パブリックチャンネルのメッセージ履歴読み取り |
| `channels:read` | チャンネル情報（名前等）の取得 |
| `groups:history` | プライベートチャンネルのメッセージ履歴読み取り |
| `groups:read` | プライベートチャンネル情報の取得 |
| `users:read` | 表示名の取得 |
| `users:read.email` | ユーザーのメールアドレス取得（プライベートチャンネルの共有制御に必要） |
| `files:read` | 添付ファイルのダウンロード |
| `chat:write` | botのメッセージ送信（コマンド応答に必要） |

**Event Subscriptions** → **Enable Events** をON → **Subscribe to bot events** に以下を追加:

- `message.channels` — パブリックチャンネルのメッセージ
- `message.groups` — プライベートチャンネルのメッセージ
- `app_mention` — botへのメンション（コマンド応答に必要）
- `member_joined_channel` — メンバー参加時に共有を自動追加
- `member_left_channel` — メンバー退出時に共有を自動解除
- `channel_rename` / `group_rename` — チャンネル名変更に追従
- `user_change` — 表示名の変更に追従

**Socket Mode** → **Enable Socket Mode** をON。

その後、上記 1-2（App-Level Token 生成）と 1-3（インストール）を実施してください。

> 既存アプリのマニフェストを差し替える方法もあります:
> **「App Manifest」** 画面で YAML を貼り替えて **「Save Changes」** → その後アプリの再インストールが必要です。

</details>

---

## Step 2: Google Cloudの設定

必要なのは **OAuth クライアントID を1つ作るだけ**です。
スプレッドシートと Drive フォルダは Step 4 の `setup.py` が自動で作成するため、
手で作ってIDをコピーする作業はありません。

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを新規作成（既存でも可）
2. 以下の2つのAPIを有効化:
   - **Google Sheets API** — [有効化リンク](https://console.cloud.google.com/apis/library/sheets.googleapis.com)
   - **Google Drive API** — [有効化リンク](https://console.cloud.google.com/apis/library/drive.googleapis.com)
3. **OAuth同意画面**（[Google Auth Platform](https://console.cloud.google.com/auth/overview)）を設定
   - 左メニューの **「ブランディング」** を開く
   - **アプリ名**に `Slackログ保存bot_privch対応版` を入力
   - **ユーザーサポートメール**は自分の Google アカウントを選択
   - 下部の **デベロッパーの連絡先情報**に自分のメールアドレスを入力
   - ホームページとプライバシーポリシーは、[GitHub Pages の URL](github-pages.md#google-oauth-の入力値) を入力
   - ロゴと利用規約 URL は個人利用なら空欄のままでよい
   - 画面下部の **「保存」** を押す
4. **[対象](https://console.cloud.google.com/auth/audience)** を開く
   - **ユーザーの種類**は **外部** を選択
   - **テストユーザー**に、Drive と Sheets を操作する自分の Google アカウントを追加
5. 必要に応じて **[データアクセス](https://console.cloud.google.com/auth/scopes)** を開き、次のスコープを追加
   - `https://www.googleapis.com/auth/spreadsheets`
   - `https://www.googleapis.com/auth/drive`
6. **[認証情報](https://console.cloud.google.com/apis/credentials) →「認証情報を作成」→「OAuth クライアント ID」**
   - アプリケーションの種類: **デスクトップアプリ**
   - 作成後、JSONをダウンロードして `client_secret.json` としてプロジェクトに配置

> ブランディング画面のアプリ名が `temp-...` のままでも動作しますが、OAuth の確認画面に表示される名前なので、
> この bot 用の名前に変更してください。**確認ステータス**の警告は、個人利用の初回設定では無視して構いません。
>
> Google Auth Platform の画面に **「外部」** が表示されない場合は、ブランディングを保存してから
> 左メニューの **「対象」** を開いてください。そこで「内部」または「外部」を選択します。
> 「内部」しか選べない場合は、Google Workspace 組織に属するプロジェクトで、管理者が外部アプリを
> 禁止している可能性があります。その場合は管理者に外部アプリの許可を依頼するか、個人利用できる
> Google Cloud プロジェクトを選択してください。

> [!IMPORTANT]
> 個人利用や動作確認だけなら、アプリを審査へ送信する必要はありません。
> 認証時に「このアプリは Google で確認されていません」と警告が出た場合は、
> **「詳細」→「（アプリ名）に移動」** で進めます。
>
> ユーザーの種類が「外部」で公開ステータスが「テスト中」の間は、Google の仕様で
> リフレッシュトークンが7日で失効します。継続運用する場合は、次のどちらかにしてください。
>
> - **Google Workspace 組織のプロジェクトの場合**：**対象**の画面で **「内部に公開」** を押し、
>   組織のアカウントで認証します。「内部」には公開ステータスがなく、7日失効もテストユーザーの登録もありません。
>   組織外のアカウント（`@gmail.com` など）では認証できなくなるので注意してください。
> - **それ以外（組織なしのプロジェクト、個人の Google アカウント）**：**「アプリを公開」** で
>   本番環境に切り替えます。個人利用なら審査を通さなくても動きます（未確認アプリの警告は出ます）。

---

## Step 3: botのインストール

```bash
git clone https://github.com/J59NOC/slack-log-bot.git
cd slack-log-bot

uv venv --python 3.13 .venv
uv sync
```

> [!IMPORTANT]
> **Python 3.10 以上が必要です。**
> macOS に最初から入っている `/usr/bin/python3` は 3.9 なので、
> `python3 -m venv` だとそれを拾って `TypeError: unsupported operand type(s) for |`
> で起動に失敗します。[uv](https://docs.astral.sh/uv/) を使うか、
> 3.10以上の Python を明示してください。

<details>
<summary>uv を使わない場合</summary>

```bash
python3.13 -m venv .venv     # バージョンを明示する
.venv/bin/pip install -r requirements.txt
```

以降の `uv run xxx.py` は `.venv/bin/python xxx.py` に読み替えてください。

</details>

以降は `uv run` 経由で実行します。

---

## Step 4: 保存先を決めてセットアップ

### 4-1. 保存先を決める

ログの置き場所は2通りあります。**あとから移すのは面倒なので、先に決めてください。**

| | bot アカウントのマイドライブ | チームの共有ドライブ |
|---|---|---|
| 用意するもの | なし（既定） | Google Workspace の共有ドライブ |
| ファイルの所有者 | bot の Google アカウント | **共有ドライブ（組織）** |
| bot のアカウントを失ったら | **ログも失う** | ログは残る |
| private channel のアクセス制御 | 効く | `private` 親フォルダを限定公開にすれば効く |
| private channel の退出時の権限剥奪 | 効く | 効く（private channel のフォルダで） |

判断の目安です。

- **bot 用アカウントに依存したくない・組織の資産にしたい** → 共有ドライブ
- **共有ドライブが無い、または `private` 親フォルダを限定公開にできない** → マイドライブ
- 共有ドライブでは、public channel のログは共有ドライブのメンバー全員が読めます

共有ドライブを使う場合は、bot の Google アカウントを**「コンテンツ管理者」以上**
として追加してください。「投稿者」だとファイルをゴミ箱に移せず、添付の整理と、
削除されたメッセージの後片付けが動きません。

> 組織外のアカウントを共有ドライブのメンバーにできない設定になっていることが
> あります。その場合は、そのドメインのアカウントを1つ用意してください。

共有ドライブでの動きは [storage.md の「共有ドライブに保存する場合」](storage.md#共有ドライブに保存する場合) にまとめています。

### 4-2. 共有ドライブなら、先に権限を確かめる

bot がそこでやることを実際に全部試して、最後に全部消します。何も書き換えません。

```bash
uv run setup.py --check-drive 'https://drive.google.com/drive/folders/xxxxx'
```

```
保存先の事前チェック
  ✓ フォルダを開けました: チーム共有（共有ドライブ内）
  ✓ フォルダの作成
  ✓ スプレッドシートの作成
  ✓ ファイルのアップロード（添付ファイル用）
  ✓ ゴミ箱への移動（削除されたメッセージの添付を片付ける）
  ✓ 作ったものを検索で見つけられるか
```

失敗したら、どの操作がどう失敗したかと、原因の候補を表示します。

### 4-3. セットアップウィザード

```bash
uv run setup.py
```

保存先を共有ドライブにするなら、そのフォルダを指定します。省略すると対話で
聞かれ、そこでも空 Enter ならマイドライブに作成します。

```bash
uv run setup.py --parent 'https://drive.google.com/drive/folders/xxxxx'
```

> `.env` に `GOOGLE_DRIVE_FOLDER_ID` が既にある場合、保存先はその値が優先され、
> `--parent` は無視されます。移したいときは、その行を消してから実行してください。
> **既に書き込まれたファイルは移動しません**（新旧が分断されます）。

対話形式で以下をすべて実行します:

| | 内容 |
|---|---|
| 1/4 | Slackトークンの入力と接続確認 — **必要なスコープが揃っているか事前に検証**し、足りなければ名前を挙げて中断 |
| 2/4 | Googleの認証（ブラウザが開きます）→ `drive_token.json` を生成 |
| 3/4 | **スプレッドシートとDriveフォルダを自動作成**し、IDを `.env` に書き込み |
| 4/4 | 実際に開けるかを確認する書き込みテスト |

何度でも再実行できます。設定済みかつ正常な項目はスキップされるので、
途中で失敗しても最初からやり直す必要はありません。

```bash
uv run setup.py --reauth   # Googleの認証だけやり直す
```

> [!IMPORTANT]
> **保存先が共有ドライブなら、`main.py` を起動する前に `private` フォルダを限定公開にしてください。**
> `private` フォルダはこのウィザードが保存先の下に作ります。これを Google Drive で「限定公開」に
> 設定すると、private channel ごとのフォルダ、専用スプレッドシート、添付ファイルがその権限を継承します。
> 設定しないまま起動すると、private channel のログが共有ドライブのメンバー全員に見えてしまいます。
> Google Workspace 管理者の設定で限定公開を使えない場合は、共有ドライブの管理者に設定を依頼してください。

ブラウザの無いマシン（SSH 越しの Raspberry Pi など）で認証する場合は、
[running.md の「Google 認証をヘッドレスで通す」](running.md#google-認証をヘッドレスで通す) を参照してください。

<details>
<summary><b>手動で .env を設定する場合</b></summary>

既存の保存先フォルダを使いたいときは、`.env` に親フォルダ ID を直接指定します。
public/private フォルダと public スプレッドシートは、その親フォルダから自動検出されます。

```bash
cp .env.example .env
```

```ini
SLACK_BOT_TOKEN=xoxb-xxxxxxxxxxxx-xxxxxxxxxxxx-xxxxxxxxxxxxxxxxxxxxxxxx
SLACK_APP_TOKEN=xapp-x-xxxxxxxxxx-xxxxxxxxxxxxx-xxxxxxxx

# Google DriveフォルダURLの /folders/ の後ろ
GOOGLE_DRIVE_FOLDER_ID=your_folder_id_here

TIMEZONE=Asia/Tokyo
```

その後 `uv run setup.py` を実行すれば、認証と疎通確認だけが行われます。

</details>

---

## Step 5: 動作確認

```bash
# まず過去メッセージの取り込みテスト（特定チャンネル・過去7日）
uv run backfill.py --channel general --days 7
```

成功すると以下のようなログが出力されます:

```
2026-03-31 09:00:01 [INFO] Backfilling #general [public]...
2026-03-31 09:00:03 [INFO] Wrote 15 messages (grouped) to #general
2026-03-31 09:00:03 [INFO]   #general: 15 new, 0 duplicates skipped
2026-03-31 09:00:03 [INFO] Backfill complete. New: 15, Skipped (duplicate): 0
```

Google スプレッドシートを開いて、メッセージが記録されていることを確認してください。

うまくいかないときは [troubleshooting.md](troubleshooting.md) を参照してください。
動作を確認できたら、[running.md](running.md) の手順で常駐させます。
