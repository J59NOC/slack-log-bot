"""Shared Google OAuth2 credentials for both Sheets and Drive.

Service accounts used to handle the spreadsheet reads and writes, but they have
no Drive storage quota, so file uploads and new spreadsheets had to go through a
second, user-owned OAuth2 credential anyway. Running both meant creating a
service account, downloading its key, and sharing every spreadsheet and folder
with its address by hand. One user credential covers all of it.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# トークンを保存するファイルの名前で、秘密の値そのものではない
DEFAULT_TOKEN_FILE = "drive_token.json"  # noqa: S105
DEFAULT_CLIENT_FILE = "client_secret.json"


class CredentialsError(RuntimeError):
    """Raised when the stored token is missing, unusable, or under-scoped."""


def token_file() -> str:
    """保存済みトークンの場所（環境変数 GOOGLE_DRIVE_TOKEN_FILE）。"""
    return os.environ.get("GOOGLE_DRIVE_TOKEN_FILE", DEFAULT_TOKEN_FILE)


def client_file() -> str:
    """OAuth クライアント（client_secret.json）の場所（環境変数 GOOGLE_OAUTH_CLIENT_FILE）。"""
    return os.environ.get("GOOGLE_OAUTH_CLIENT_FILE", DEFAULT_CLIENT_FILE)


def save_token(creds: Credentials, path: str | None = None) -> str:
    """Persist a credential, including its expiry so staleness is detectable."""
    path = path or token_file()
    data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or SCOPES),
    }
    if creds.expiry:
        data["expiry"] = creds.expiry.isoformat()
    with Path(path).open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    Path(path).chmod(0o600)
    return path


def load_credentials(required_scopes: list[str] | None = None) -> Credentials:
    """Load the stored OAuth2 credential, refreshing it if it has expired."""
    required_scopes = required_scopes or SCOPES
    path = token_file()

    if not Path(path).exists():
        msg = (
            f"Google の認証ファイル {path} がありません。\n"
            f"  python setup.py を実行してセットアップしてください。"
        )
        raise CredentialsError(msg)

    try:
        with Path(path).open(encoding="utf-8") as f:
            stored = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        msg = f"{path} を読み込めません: {e}\n  python setup.py で作り直してください。"
        raise CredentialsError(msg) from e

    granted = set(stored.get("scopes") or [])
    missing = [s for s in required_scopes if s not in granted]
    if missing:
        # Tokens issued before Sheets access was folded into this credential
        # only carry the Drive scope, so they have to be re-consented.
        msg = (
            f"{path} に必要な権限がありません: {', '.join(missing)}\n"
            f"  python setup.py を実行して認証をやり直してください。"
        )
        raise CredentialsError(msg)

    creds = Credentials.from_authorized_user_info(stored, required_scopes)

    if not creds.valid:
        if not creds.refresh_token:
            msg = (
                f"{path} の有効期限が切れており、更新もできません。\n"
                f"  python setup.py を実行して認証をやり直してください。"
            )
            raise CredentialsError(msg)
        try:
            creds.refresh(Request())
        except Exception as e:
            msg = (
                f"Google 認証トークンの更新に失敗しました: {e}\n"
                f"  OAuth同意画面が「テスト中」のままだとトークンは7日で失効します。\n"
                f"  python setup.py で再認証するか、同意画面を「本番環境」に切り替えてください。"
            )
            raise CredentialsError(msg) from e
        save_token(creds, path)
        logger.info("Refreshed Google OAuth2 token")

    return creds
