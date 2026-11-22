"""環境変数（.env）からの設定の読み込み。

import しただけでは何も読まない。load_settings() を呼んだときに初めて読み、
足りなければ終了する。setup.py のように、設定がまだ揃っていない段階で
Google まわりのモジュールだけを使いたい場面があるため。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# リポジトリのルート（profiles/ と client_secret.json の置き場所）。
# このファイルは src/infrastructure/ にあるので 2 つ上。
BASE_DIR = Path(__file__).resolve().parents[2]

# File download size limit (50MB)
MAX_FILE_SIZE = 50 * 1024 * 1024


@dataclass(frozen=True)
class Settings:
    """Bot の設定。load_settings() で .env と環境変数から作る。"""

    slack_bot_token: str
    google_drive_folder_id: str
    # APP_TOKEN is only required for Socket Mode (main.py), not for weekly collection
    slack_app_token: str = ""
    # The public spreadsheet is discovered under the public Drive folder. The
    # variable remains an optional compatibility override for older installations.
    google_spreadsheet_id: str = ""
    # トークンを保存するファイルの名前で、秘密の値そのものではない
    google_drive_token_file: str = "drive_token.json"  # noqa: S105
    google_oauth_client_file: str = "client_secret.json"
    timezone: str = "Asia/Tokyo"
    max_file_size: int = MAX_FILE_SIZE
    profile: str = ""


PROFILE_ROOT = "profiles"


def profile_dir(profile: str) -> Path:
    """プロファイルの設定の置き場所。プロファイルが無ければリポジトリのルート。"""
    return BASE_DIR / PROFILE_ROOT / profile if profile else BASE_DIR


def apply_profile(name: str) -> Path:
    """Point this run's files at profiles/<name>/ instead of the root.

    One checkout, several bots: a public one and a private one want different
    Slack apps, different Google accounts and different Drive folders, and
    nothing but the code is shared between them. Keeping each set in its own
    directory means one `git pull` updates them all.

    The paths go into the environment because load_settings() and
    google/auth.py read them from there, and because the bot itself is pointed
    at a profile the same way — SLACK_LOG_PROFILE in its systemd unit.

    プロファイルのディレクトリを（無ければ作って）返す。セットアップ用。
    """
    directory = BASE_DIR / PROFILE_ROOT / name
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)  # a token and a bot token live here

    os.environ["SLACK_LOG_PROFILE"] = name
    os.environ["GOOGLE_DRIVE_TOKEN_FILE"] = str(directory / "drive_token.json")

    # An OAuth client is not account-specific, so a shared one at the root
    # serves every profile unless this one brought its own.
    own_client = directory / "client_secret.json"
    os.environ["GOOGLE_OAUTH_CLIENT_FILE"] = str(
        own_client if own_client.exists() else BASE_DIR / "client_secret.json"
    )
    return directory


def setup_hint(profile: str) -> str:
    """設定が足りないときに案内する、セットアップのコマンド。"""
    return (
        f"`uv run setup.py --profile {profile}` を実行するとセットアップできます。"
        if profile
        else "`uv run setup.py` を実行するとセットアップできます。"
    )


def _required(name: str, hint: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"[設定エラー] {name} が .env に設定されていません。\n  {hint}")
    return value


def load_settings() -> Settings:
    """プロファイルの .env を読み、設定を返す。必須の値が無ければ終了する。"""
    # Which set of credentials this process runs on. One checkout can drive
    # several bots — a public one and a private one, say — each with its own
    # Slack app, Google account and Drive folder, kept apart under profiles/.
    # Unset means the checkout's own root, which is what a single-bot install
    # has always been and stays.
    profile = os.environ.get("SLACK_LOG_PROFILE", "").strip()
    directory = profile_dir(profile)

    if profile and not directory.is_dir():
        msg = (
            f"[設定エラー] プロファイル '{profile}' が見つかりません（{directory}）。\n"
            f"  `uv run setup.py --profile {profile}` を実行すると作成できます。"
        )
        raise SystemExit(msg)

    # An explicit path so the bot does not depend on which directory it was
    # started from; with a profile there is more than one .env to choose between.
    load_dotenv(directory / ".env")

    hint = setup_hint(profile)
    slack_bot_token = _required("SLACK_BOT_TOKEN", hint)
    slack_app_token = os.environ.get("SLACK_APP_TOKEN", "")

    # Sheets and Drive share one OAuth2 user credential; see google/auth.py.
    google_spreadsheet_id = os.environ.get("GOOGLE_SPREADSHEET_ID", "").strip()
    google_drive_folder_id = _required("GOOGLE_DRIVE_FOLDER_ID", hint)

    def resolve(path: str) -> str:
        """Resolve a relative path from .env against this profile's directory, not the cwd."""
        return path if Path(path).is_absolute() else str(directory / path)

    def client_file() -> str:
        """Return the OAuth client for this profile, falling back to the shared one.

        A client is not tied to a Google account — one can authorise all three —
        so a client_secret.json at the checkout root serves every profile, and a
        profile needing its own just puts one beside its .env.
        """
        own = directory / "client_secret.json"
        if own.exists() or not profile:
            return str(own)
        return str(BASE_DIR / "client_secret.json")

    # The token is the account, so it never falls back to another profile's.
    token_file = resolve(os.environ.get("GOOGLE_DRIVE_TOKEN_FILE") or "drive_token.json")
    oauth_client_file = (
        resolve(os.environ["GOOGLE_OAUTH_CLIENT_FILE"])
        if os.environ.get("GOOGLE_OAUTH_CLIENT_FILE")
        else client_file()
    )
    # google/auth.py reads these from the environment, so hand it the resolved
    # paths; otherwise a profile's bot would pick up the checkout root's token.
    os.environ["GOOGLE_DRIVE_TOKEN_FILE"] = token_file
    os.environ["GOOGLE_OAUTH_CLIENT_FILE"] = oauth_client_file

    return Settings(
        slack_bot_token=slack_bot_token,
        slack_app_token=slack_app_token,
        google_spreadsheet_id=google_spreadsheet_id,
        google_drive_folder_id=google_drive_folder_id,
        google_drive_token_file=token_file,
        google_oauth_client_file=oauth_client_file,
        timezone=os.environ.get("TIMEZONE", "Asia/Tokyo"),
        profile=profile,
    )
