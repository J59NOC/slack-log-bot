# GitHub Pages の設定

Google OAuth のブランディング画面（[導入手順 Step 2](setup.md#step-2-google-cloudの設定)）に入力する
ホームページとプライバシーポリシーを、GitHub Pages で公開しています。

## 公開される内容

GitHub Actions（`.github/workflows/pages.yml`）が `privacy-policy/` ディレクトリだけを Pages artifact として公開します。
`docs/` やリポジトリ全体が公開されるわけではありません。

公開 URL:

```text
https://j59noc.github.io/slack-log-bot/
https://j59noc.github.io/slack-log-bot/privacy-policy.html
```

## GitHub 側の設定

リポジトリの **Settings → Pages** を開き、Source を **GitHub Actions** に設定します。

workflow は、`main` に `privacy-policy/` か workflow 自体の変更が push されたときに実行されます。
Actions の画面から手動で実行することもできます（`workflow_dispatch`）。

## Google OAuth の入力値

Google Auth Platform のブランディング画面には、次を入力します。

```text
承認済みドメイン: j59noc.github.io
アプリケーションのホームページ: https://j59noc.github.io/slack-log-bot/
プライバシーポリシー: https://j59noc.github.io/slack-log-bot/privacy-policy.html
```

`j59noc.github.io` の所有権確認を求められた場合は、Google Search Console の案内に従って確認してください。
