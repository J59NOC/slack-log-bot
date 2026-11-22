# トラブルシューティング

## Google の認証

### `エラー 403: access_denied` /「Google の審査プロセスを完了していません」

次のどちらかの状態です。

- OAuth同意画面が **「テスト中」** で、自分がテストユーザーに入っていない
- ユーザーの種類が **「内部」** なのに、組織外のアカウント（`@gmail.com` など）で認証しようとした

[導入手順 Step 2](setup.md#step-2-google-cloudの設定) 末尾の注意に従って、
「内部に公開」か「アプリを公開」に切り替えてください。

### 1週間ほど動いた後に突然 Google 認証が失敗する（`invalid_grant`）

公開ステータスが **「テスト中」** のままで、リフレッシュトークンが7日で失効しています。
[導入手順 Step 2](setup.md#step-2-google-cloudの設定) 末尾の注意に従って切り替えてから、再認証してください。

```bash
uv run setup.py --reauth
```

保存先が共有ドライブなら、認証するアカウントを組織のアカウントに替えても既存のファイルはそのまま使えます
（そのアカウントが共有ドライブのメンバーであれば）。

### `Google の認証ファイル drive_token.json がありません`

`uv run setup.py` を実行してください。

### `drive_token.json に必要な権限がありません`

Sheets と Drive の認証が1本化される前のトークンです（Drive権限しか持っていません）。
`uv run setup.py --reauth` で認証をやり直してください。

## 保存先

### `public スプレッドシートが見つかりません`

親フォルダの `.env` 設定（`GOOGLE_DRIVE_FOLDER_ID`）を確認し、`uv run setup.py` を
実行してください。`public` フォルダと public 用スプレッドシートが自動作成されます。

## Slack

### スコープ不足のエラーが出る

`uv run setup.py` の 1/4 が必要なスコープをすべて検証します。不足していれば名前を挙げるので、
`slack-app-manifest.yml` を App Manifest 画面に貼り直し、アプリを再インストールしてください。

### botがチャンネルのメッセージを取得できない

- botがチャンネルに招待されているか確認: `/invite @slack-log-bot-private`
- プライベートチャンネルの場合、`groups:history` と `groups:read` スコープがあるか確認

### botのメンションに反応しない

- Event Subscriptionsで `app_mention` が追加されているか確認
- Socket Modeが有効になっているか確認
