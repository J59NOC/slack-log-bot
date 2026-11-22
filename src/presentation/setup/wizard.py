"""Interactive setup wizard.

Walks through everything the bot needs and verifies each piece as it goes:
Slack tokens (with a scope check), Google OAuth2 consent, and the spreadsheet
and Drive folder, which are created for you rather than made by hand and
pasted in as IDs.

Safe to re-run: anything already configured and working is left alone.

ここは画面と流れだけを持ち、Slack・Google への問い合わせは infrastructure の
app_check / provisioning / auth に任せる。

Deliberately never loads the settings until the last step: load_settings()
exits when required values are missing, which is exactly the situation this
wizard exists to fix.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv

from src.infrastructure.env_file import EnvFile
from src.infrastructure.google import auth
from src.infrastructure.google.drive import (
    FOLDER_MIME,
    drive_service,
    move_into_folder,
    shared_drive_id,
)
from src.infrastructure.google.provisioning import (
    INDEX_SPREADSHEET_NAME,
    SPREADSHEET_MIME,
    WORKSPACE_FOLDER_NAME,
    StorageProbe,
    describe,
    ensure_guide,
    find_index_spreadsheet,
    find_or_create,
    folder_id_from,
    locate_index,
    prepare_layout,
    spreadsheet_title,
    stamp_channel_ids,
)
from src.infrastructure.settings import PROFILE_ROOT, apply_profile, load_settings
from src.infrastructure.slack.app_check import (
    REQUIRED_SLACK_SCOPES,
    BotAuth,
    TokenRejectedError,
    authenticate_bot,
    channel_pages,
    check_app_token,
    missing_scopes,
)
from src.presentation.setup.console import ask, fail, ok, step, warn

if TYPE_CHECKING:
    from collections.abc import Callable

    from google.oauth2.credentials import Credentials

    from src.infrastructure.google.drive import DriveService
    from src.infrastructure.google.provisioning import StorageLayout

# ── Step 1: Slack ──

# Slack のトークンは xoxb-1-2-xxxx のように、少なくとも 4 つに区切られている
TOKEN_SEGMENTS = 4


def check_token_shape(prefix: str) -> Callable[[str], str | None]:
    """トークンの形を確かめる関数を作る。問題があればその説明を返す。"""

    def validate(value: str) -> str | None:
        if not value.startswith(prefix):
            return f"{prefix} で始まる必要があります"
        if len(value.split("-")) < TOKEN_SEGMENTS:
            return "形式が違います。トークン全体をコピーできているか確認してください"
        return None

    return validate


def warn_if_truncated(token: str, usual_len: int) -> None:
    """Warn when a short trailing segment suggests the paste was cut off.

    Only a warning: auth.test is the real gate, and hard-failing on length
    would block anyone whose token Slack issues in a different shape.
    """
    secret = token.rsplit("-", maxsplit=1)[-1]
    if len(secret) < usual_len:
        warn(
            f"末尾が {len(secret)} 文字です（通常 {usual_len} 文字）。"
            f"コピー時に切れているかもしれません。"
        )


AUTH_ERROR_HINTS = {
    "invalid_auth": [
        "トークンが途中で切れていないか（手動選択ではなく Copy ボタンを使う）",
        "アプリをワークスペースにインストール済みか（Install App → Install to Workspace）",
        "インストール後に表示された最新のトークンか（再インストールすると変わります）",
        "別のアプリ／別のワークスペースのトークンを貼っていないか",
    ],
    "account_inactive": [
        "アプリがワークスペースからアンインストールされています。再インストールしてください",
    ],
    "token_revoked": [
        "トークンが失効しています。再インストールして新しいトークンを取得してください",
    ],
    "not_authed": ["トークンが空です"],
}

# bot トークンを入力し直してもらえる回数（.env にあったものを含む）
BOT_TOKEN_ATTEMPTS = 3


def _ensure_app_token(env: dict[str, str]) -> None:
    if not env.get("SLACK_APP_TOKEN"):
        print("  Socket Mode 用の App-Level Token が必要です。")
        print("  Basic Information → App-Level Tokens → connections:write")
        env["SLACK_APP_TOKEN"] = ask("SLACK_APP_TOKEN (xapp-...)", check_token_shape("xapp-"))
        warn_if_truncated(env["SLACK_APP_TOKEN"], 64)

    try:
        check_app_token(env["SLACK_APP_TOKEN"])
        ok("Socket Mode 用 App-Level Token を確認")
    except TokenRejectedError as e:
        warn(f"App-Level Token が無効です: {e.code}")
        print("    Basic Information → App-Level Tokens で、現在の Slack App 用に")
        print("    connections:write を付けた xapp-... token を再発行してください。")
        fail("Socket Mode 用 App-Level Token を確認できませんでした。")
    except Exception as e:
        fail(f"Socket Mode 用 App-Level Token の確認に失敗しました: {e}")


def _ask_bot_token(env: dict[str, str]) -> None:
    print("\n  Slack App の Bot User OAuth Token が必要です。")
    print("  https://api.slack.com/apps → 対象アプリ → OAuth & Permissions")
    print("  （手動で選択せず Copy ボタンを使ってください）")
    env["SLACK_BOT_TOKEN"] = ask("SLACK_BOT_TOKEN (xoxb-...)", check_token_shape("xoxb-"))
    warn_if_truncated(env["SLACK_BOT_TOKEN"], 24)


def _try_authenticate(token: str) -> BotAuth | str:
    """接続できれば BotAuth を、拒否されたら Slack のエラーコードを返す。"""
    try:
        return authenticate_bot(token)
    except TokenRejectedError as e:
        return e.code


def _explain_rejection(code: str, *, source_is_env: bool) -> None:
    print()
    warn(f"Slack が認証を拒否しました: {code}")
    for hint in AUTH_ERROR_HINTS.get(code, ["トークンを確認してください"]):
        print(f"    - {hint}")
    if source_is_env:
        print("    （このトークンは .env から読み込んだものです）")


def _authenticate(env: dict[str, str]) -> BotAuth:
    # Re-prompt on failure rather than exiting: re-running the whole wizard
    # because one paste came up short is needless.
    attempt = 0
    while True:
        attempt += 1
        source_is_env = bool(env.get("SLACK_BOT_TOKEN"))
        if not source_is_env:
            _ask_bot_token(env)

        result = _try_authenticate(env["SLACK_BOT_TOKEN"])
        if isinstance(result, BotAuth):
            return result

        _explain_rejection(result, source_is_env=source_is_env)
        if attempt == BOT_TOKEN_ATTEMPTS:
            fail("トークンを確認できませんでした。")
        print("\n  もう一度入力してください。")
        env["SLACK_BOT_TOKEN"] = ""


def _check_scopes(bot: BotAuth) -> None:
    if not bot.scopes:
        warn("付与スコープを確認できませんでした（手動で確認してください）")
        return
    missing = missing_scopes(bot.scopes)
    if missing:
        print()
        warn(f"スコープが不足しています: {', '.join(missing)}")
        print("    slack-app-manifest.yml を App Manifest 画面に貼り直し、")
        print("    アプリを再インストールしてください。")
        fail("スコープ不足のため中断しました。")
    ok(f"必要なスコープ {len(REQUIRED_SLACK_SCOPES)} 個すべてを確認")


def _list_channels(bot_token: str) -> dict[str, str]:
    channels: dict[str, str] = {}
    try:
        for page in channel_pages(bot_token):
            channels.update(page)
    except Exception as e:
        warn(f"チャンネル一覧を取得できませんでした: {e}")
    return channels


def check_slack(env: dict[str, str]) -> tuple[dict[str, str], str, dict[str, str]]:
    """トークンを確かめ（無ければ聞き）、(env, ワークスペース名, チャンネル一覧) を返す。"""
    step(1, "Slack トークンの確認")
    _ensure_app_token(env)

    try:
        bot = _authenticate(env)
    except Exception as e:
        fail(f"Slack に接続できません: {e}")

    workspace = bot.team
    ok(f"接続成功: {workspace} / bot={bot.user}")
    _check_scopes(bot)
    return env, workspace, _list_channels(env["SLACK_BOT_TOKEN"])


# ── Step 2: Google OAuth2 ──


def _explain_missing_client(client_file: str) -> None:
    print(f"  {client_file} がありません。以下の手順で取得してください:")
    print("    1. https://console.cloud.google.com/ でプロジェクトを作成")
    print("    2. Google Sheets API と Google Drive API を有効化")
    print("    3. APIとサービス → 認証情報 → OAuth クライアントID")
    print("       アプリケーションの種類: デスクトップアプリ")
    print(f"    4. JSON をダウンロードし、{client_file} としてこのディレクトリに配置")
    fail(f"{client_file} を配置してから再実行してください。")


def _explain_access_denied() -> None:
    print()
    warn("Google がアクセスを拒否しました (access_denied)")
    print("    次のどちらかの可能性が高いです。")
    print("    - 公開ステータスが「テスト中」で、自分がテストユーザーに入っていない")
    print("    - ユーザーの種類が「内部」なのに、組織外のアカウントで認証した")
    print("    https://console.cloud.google.com/auth/audience を開き、")
    print("    組織のプロジェクトなら「内部に公開」して組織のアカウントで認証するか、")
    print("    「アプリを公開」で公開ステータスを「本番環境」にしてください。")
    print("    (テストユーザーに自分を追加しても通りますが、")
    print("     その場合トークンが7日で失効します)")
    fail("公開設定を変更してから再実行してください。")


def run_google_auth(*, reauth: bool = False, port: int = 0) -> None:
    """Google の認証を済ませる。使える認証が既にあればそれを使う。"""
    step(2, "Google の認証")

    client_file = auth.client_file()
    token_path = auth.token_file()

    if not reauth and Path(token_path).exists():
        try:
            auth.load_credentials()
        except auth.CredentialsError as e:
            warn(str(e).splitlines()[0])
            print("    認証をやり直します。")
        else:
            ok(f"既存の認証を再利用: {token_path}")
            return

    if not Path(client_file).exists():
        _explain_missing_client(client_file)

    print("  ブラウザが開きます。Google アカウントでアクセスを許可してください。")
    print("  「このアプリは確認されていません」の警告は 詳細 → 移動 で進めます。")
    if port:
        print(f"  ポート {port} で待ち受けます。SSH 越しの場合は手元の端末で:")
        print(f"    ssh -L {port}:localhost:{port} <このホスト>")
        print("  を張ってから、表示される URL をブラウザで開いてください。")
    flow = auth.consent_flow(client_file)
    try:
        creds = flow.run_local_server(port=port, open_browser=not port)
    except Exception as e:
        if "access_denied" in str(e):
            _explain_access_denied()
        fail(f"Google の認証に失敗しました: {e}")
    auth.save_token(creds, token_path)
    ok(f"認証完了: {token_path}")


# ── Step 3: Spreadsheet & Drive folder ──


def ask_parent_folder(drive: DriveService, given: str = "") -> str | None:
    """Ask where the bot's folder should be created; None means My Drive.

    A shared drive is the reason to ask. Files made inside one belong to the
    organisation rather than to the account running the bot, so losing that
    account no longer loses the logs — but the bot cannot put a folder there
    unless it is told which one.
    """
    raw = given.strip()
    if not raw:
        print("      保存先の親フォルダがあれば、その URL か ID を貼ってください。")
        print("      共有ドライブに置くと、ファイルの所有者が組織になります。")
        print("      空のまま Enter を押すと、このアカウントのマイドライブに作ります。")
        raw = input("      親フォルダ (省略可): ").strip()
    if not raw:
        return None

    folder_id = folder_id_from(raw)

    try:
        meta = describe(drive, folder_id, "id,name,mimeType,driveId")
    except Exception as e:
        warn(f"そのフォルダを開けませんでした: {e}")
        warn("マイドライブに作成します。あとから移動できます。")
        return None

    if meta.get("mimeType") != FOLDER_MIME:
        warn("フォルダではないようです。マイドライブに作成します。")
        return None

    where = "共有ドライブ内" if meta.get("driveId") else "マイドライブ内"
    ok(f"親フォルダ: {meta['name']}（{where}）")
    return meta["id"]


def _ensure_workspace_folder(
    env_file: EnvFile, env: dict[str, str], drive: DriveService, workspace: str, parent: str
) -> None:
    # The folder comes first so the spreadsheet can be created inside it.
    # Private channel spreadsheets and attachment folders already live here,
    # so this keeps everything the bot owns in one place.
    if env.get("GOOGLE_DRIVE_FOLDER_ID"):
        ok(f"既存の Drive フォルダを使用: {env['GOOGLE_DRIVE_FOLDER_ID']}")
        if parent:
            # Moving an existing archive is not something to do as a side
            # effect of a flag: the files already written stay where they are.
            warn("--parent は無視しました。保存先は .env の値が優先されます。")
            warn("  移したい場合は .env の GOOGLE_DRIVE_FOLDER_ID を消して再実行してください。")
        return

    parent_id = ask_parent_folder(drive, parent)
    folder_name = WORKSPACE_FOLDER_NAME.format(workspace=workspace)
    folder_id, created = find_or_create(drive, folder_name, FOLDER_MIME, parent=parent_id)
    env["GOOGLE_DRIVE_FOLDER_ID"] = folder_id
    # Saved as soon as it is known. Anything created here exists in Drive
    # whether or not the rest of this step succeeds, and an id left only in
    # memory is one the next run has to find again by name.
    env_file.write(env)
    ok(f"Drive フォルダを{'作成' if created else '検出'}: {folder_name}")
    print(f"      https://drive.google.com/drive/folders/{folder_id}")


def _ensure_index(
    env: dict[str, str], drive: DriveService, workspace: str, layout: StorageLayout
) -> str:
    spreadsheet_id = env.get("GOOGLE_SPREADSHEET_ID") or find_index_spreadsheet(
        drive, layout.public_folder_id
    )
    if spreadsheet_id:
        ok(f"既存のpublicスプレッドシートを使用: {spreadsheet_id}")
        return spreadsheet_id

    ss_name = INDEX_SPREADSHEET_NAME.format(workspace=workspace)
    spreadsheet_id, created = find_or_create(
        drive,
        ss_name,
        SPREADSHEET_MIME,
        parent=layout.public_folder_id,
    )
    ok(f"スプレッドシートを{'作成' if created else '検出'}: {ss_name}")
    print(f"      https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit")
    return spreadsheet_id


def _tidy_index(
    drive: DriveService, creds: Credentials, spreadsheet_id: str, layout: StorageLayout
) -> None:
    # Spreadsheets made before this was folded in sit at My Drive root.
    try:
        if move_into_folder(drive, spreadsheet_id, layout.public_folder_id):
            ok("スプレッドシートを public フォルダに移動")
    except Exception as e:
        warn(f"スプレッドシートをフォルダに移動できませんでした: {e}")

    # Google leaves an empty default sheet behind; make it the guide.
    try:
        if ensure_guide(creds, spreadsheet_id, in_shared_drive=layout.in_shared_drive):
            ok("説明タブ「📖 このシートについて」を作成")
        else:
            ok("説明タブは作成済み")
    except Exception as e:
        warn(f"説明タブを作成できませんでした: {e}")


def _stamp_channels(drive: DriveService, folder_id: str, channels: dict[str, str]) -> None:
    try:
        stamped = stamp_channel_ids(
            drive,
            folder_id,
            channels,
            on_error=lambda name, e: warn(f"{name} にチャンネルIDを付与できませんでした: {e}"),
        )
        if stamped:
            ok(f"{stamped} 件にチャンネルIDを付与（リネーム追従のため）")
    except Exception as e:
        warn(f"チャンネルIDを付与できませんでした: {e}")


def provision_google_resources(
    env_file: EnvFile,
    env: dict[str, str],
    workspace: str,
    channels: dict[str, str] | None = None,
    parent: str = "",
) -> dict[str, str]:
    """保存先のフォルダ・索引スプレッドシート・説明タブを用意する。"""
    step(3, "スプレッドシートと Drive フォルダの準備")

    creds = auth.load_credentials()
    drive = drive_service(creds)

    _ensure_workspace_folder(env_file, env, drive, workspace, parent)

    folder_id = env["GOOGLE_DRIVE_FOLDER_ID"]
    layout = prepare_layout(drive, folder_id)
    ok("public / private フォルダを準備")
    if layout.in_shared_drive:
        ok("保存先は共有ドライブです")
        print("      ファイルの所有者は共有ドライブ（組織）になります。")
        print("      private channel は限定公開済みの親フォルダの権限を継承します。")

    spreadsheet_id = _ensure_index(env, drive, workspace, layout)
    _tidy_index(drive, creds, spreadsheet_id, layout)
    _stamp_channels(drive, folder_id, channels or {})
    return env


# ── Step 4: End-to-end check ──


def verify(env_file: EnvFile, env: dict[str, str]) -> None:
    """保存した設定で、索引スプレッドシートと Drive フォルダを開けるか確かめる。"""
    step(4, "書き込みテスト")

    # load_settings() reads os.environ, so make the freshly written values visible to it.
    os.environ.update(env)
    load_dotenv(env_file.path, override=True)

    try:
        settings = load_settings()
        creds = auth.load_credentials()
        drive = drive_service(creds)
        drive.bind_drive(shared_drive_id(drive, settings.google_drive_folder_id))
        spreadsheet_id = locate_index(
            drive,
            settings.google_drive_folder_id,
            settings.google_spreadsheet_id,
        )
        ok(f"スプレッドシートを開けました: {spreadsheet_title(creds, spreadsheet_id)}")
        folder = describe(drive, settings.google_drive_folder_id, "name")
        ok(f"Drive フォルダにアクセスできました: {folder.get('name')}")
    except Exception as e:
        fail(f"アクセスに失敗しました: {e}")


# ── 保存先の事前チェック（--check-drive） ──


def _open_target(target: str) -> tuple[Credentials, DriveService, str]:
    try:
        creds = auth.load_credentials()
    except Exception as e:
        fail(
            f"Google の認証がまだです（{e}）\n  先に `uv run setup.py` の Step 2 を通してください。"
        )
    drive = drive_service(creds)

    folder_id = folder_id_from(target)

    try:
        meta = describe(drive, folder_id, "id,name,mimeType,driveId,capabilities")
    except Exception as e:
        fail(
            f"フォルダを開けませんでした: {e}\n"
            f"  この Google アカウントがフォルダのメンバーになっているか確認してください。"
        )
    if meta.get("mimeType") != FOLDER_MIME:
        fail("フォルダではありません。フォルダの URL を指定してください。")

    drive_id = meta.get("driveId")
    drive.bind_drive(drive_id)
    where = "共有ドライブ内" if drive_id else "マイドライブ内"
    ok(f"フォルダを開けました: {meta['name']}（{where}）")
    if not drive_id:
        warn("共有ドライブではないため、ファイルの所有者はこのアカウントのままになります。")
    return creds, drive, folder_id


def _probe(drive: DriveService, creds: Credentials, folder_id: str) -> list[str]:
    """保存先で bot が必要な操作を試し、足りないものの説明を返す。"""
    probe = StorageProbe(drive, creds)
    problems: list[str] = []

    def attempt(label: str, action: Callable[[], object], hint: str) -> object | None:
        try:
            result = action()
        except Exception as e:
            print(f"  \033[31m✗\033[0m {label}")
            print(f"      {e}")
            problems.append(hint)
            return None
        ok(f"{label}")
        return result

    try:
        folder = attempt(
            "フォルダの作成",
            lambda: probe.create_folder(folder_id),
            "フォルダを作れません。権限が「閲覧者」になっていないか確認してください。",
        )
        parent = folder["id"] if folder else folder_id

        attempt(
            "スプレッドシートの作成",
            lambda: probe.create_spreadsheet(parent),
            "スプレッドシートを作れません。",
        )

        upload = attempt(
            "ファイルのアップロード（添付ファイル用）",
            lambda: probe.upload(parent),
            "ファイルをアップロードできません。",
        )

        if upload:
            attempt(
                "ゴミ箱への移動（削除されたメッセージの添付を片付ける）",
                lambda: probe.trash(upload["id"]),
                "ゴミ箱に移せません。権限を「コンテンツ管理者」以上にしてください。",
            )

        if folder:
            found = attempt(
                "作ったものを検索で見つけられるか",
                lambda: probe.list_children(parent),
                "検索が効きません。既存のシートを作り直してしまう恐れがあります。",
            )
            if found is not None and not found:
                print("  \033[31m✗\033[0m 検索が空を返しました")
                problems.append("検索が空を返します。作ったばかりのファイルが見つかりません。")
    finally:
        for file_id in probe.cleanup():
            warn(f"後片付けに失敗しました。手で削除してください: {file_id}")
    return problems


def check_drive(target: str) -> bool:
    """Try, on a real folder, everything the bot will need to do there.

    A shared drive can refuse the bot for several unrelated reasons — the
    account is not a member, or is one with too weak a role, or the domain
    does not allow members from outside it. Each surfaces as a different
    failure at a different point of the setup. Doing all of it up front, on
    throwaway files, turns that into one answer before anything is built.
    """
    print("\033[1m保存先の事前チェック\033[0m")
    creds, drive, folder_id = _open_target(target)
    problems = _probe(drive, creds, folder_id)

    print()
    if problems:
        print("\033[1;31m足りない権限があります\033[0m")
        for hint in dict.fromkeys(problems):
            print(f"  - {hint}")
        print()
        print("  共有ドライブ側で、この Google アカウントを")
        print("  \033[1m「コンテンツ管理者」\033[0m以上として追加してください。")
        print("  組織外のアカウントを追加できない設定になっている可能性もあります。")
        return False

    print("\033[1;32mすべて通りました\033[0m")
    print("  このフォルダを保存先にするには、続けて次を実行してください。")
    print(f"    uv run setup.py --profile <名前> --parent {folder_id}")
    print()
    print("  （このチェックは何も書き換えていません。作ったものは全部消しました）")
    return True


# ── 入口 ──


def main(argv: list[str] | None = None) -> None:
    """セットアップの入口（python setup.py）。"""
    parser = argparse.ArgumentParser(description="Slack Log Bot セットアップ")
    parser.add_argument(
        "--parent",
        metavar="URL",
        default="",
        help="保存先の親フォルダ（共有ドライブなど）。省略すると Step 3 で聞きます",
    )
    parser.add_argument(
        "--check-drive",
        metavar="URL",
        default="",
        help="保存先フォルダに必要な権限があるか、実際に作って消して確かめる",
    )
    parser.add_argument(
        "--profile",
        metavar="NAME",
        default="",
        help="複数の bot を1つのチェックアウトで動かす場合の名前"
        "（例: --profile leaders → profiles/leaders/ 配下に設定を作る）",
    )
    parser.add_argument("--reauth", action="store_true", help="Google の認証をやり直す")
    parser.add_argument(
        "--auth-port",
        type=int,
        default=0,
        metavar="PORT",
        help="Google 認証の待ち受けポートを固定する"
        "（ヘッドレス機で SSH ポートフォワードを使う場合）",
    )
    args = parser.parse_args(argv)

    if args.check_drive:
        if args.profile:
            apply_profile(args.profile)
        sys.exit(0 if check_drive(args.check_drive) else 1)

    env_file = EnvFile(".env")
    if args.profile:
        env_file = EnvFile(str(apply_profile(args.profile) / ".env"))

    print("\033[1mSlack Log Bot セットアップ\033[0m")
    if args.profile:
        print(f"  プロファイル: \033[1m{args.profile}\033[0m  ({PROFILE_ROOT}/{args.profile}/)")

    env = env_file.read()
    env, workspace, channels = check_slack(env)
    env_file.write(env)

    run_google_auth(reauth=args.reauth, port=args.auth_port)

    try:
        env = provision_google_resources(env_file, env, workspace, channels, args.parent)
    except RuntimeError as e:
        fail(f"保存先の準備に失敗しました: {e}")
    env_file.write(env)

    verify(env_file, env)

    print("\n\033[1;32mセットアップ完了\033[0m\n")
    print("  過去ログの取り込み:  uv run backfill.py --days 7")
    print("  リアルタイム収集:    uv run main.py")
    print()
    print("\033[33m注意:\033[0m ユーザーの種類が「外部」で公開ステータスが「テスト中」だと、")
    print("  Google の仕様でリフレッシュトークンが7日で失効します。継続運用する場合は")
    print("  https://console.cloud.google.com/auth/audience で次のどちらかにしてください。")
    print(
        "  - Google Workspace 組織のプロジェクトで、組織のアカウントで認証した場合: 「内部に公開」"
    )
    print("  - それ以外: 「アプリを公開」で「本番環境」へ")
