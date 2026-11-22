"""Slack App の設定確認（セットアップ用）: トークン・スコープ・チャンネル一覧。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

# WebClient は slack_sdk から属性で引く（テストが slack_sdk.WebClient を差し替える）
import slack_sdk
from slack_sdk.errors import SlackApiError

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator

# Scopes the bot needs; must stay in sync with slack-app-manifest.yml
REQUIRED_SLACK_SCOPES = [
    "app_mentions:read",
    "channels:history",
    "channels:read",
    "groups:history",
    "groups:read",
    "users:read",
    "users:read.email",
    "files:read",
    "chat:write",
]


class TokenRejectedError(Exception):
    """Slack がトークンを拒否した。code は Slack のエラーコード（invalid_auth など）。"""

    def __init__(self, code: str) -> None:
        """Slack が返したエラーコードを受け取る。"""
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class BotAuth:
    """Bot トークンで接続できたときの、ワークスペースと権限。"""

    team: str
    user: str | None
    # 付与されているスコープ。空なら確認できなかった
    scopes: frozenset[str]


def check_app_token(app_token: str) -> None:
    """Socket Mode 用の App-Level Token が使えるか確かめる。拒否されたら TokenRejectedError。"""
    try:
        slack_sdk.WebClient().apps_connections_open(app_token=app_token)
    except SlackApiError as e:
        raise TokenRejectedError(e.response.get("error", "unknown")) from e


def authenticate_bot(bot_token: str) -> BotAuth:
    """Bot トークンで接続し、ワークスペースと付与スコープを返す。"""
    try:
        resp = slack_sdk.WebClient(token=bot_token).auth_test()
    except SlackApiError as e:
        raise TokenRejectedError(e.response.get("error", "unknown")) from e

    # Header names are case-insensitive, so normalise before looking one up.
    headers = {k.lower(): v for k, v in (resp.headers or {}).items()}
    raw = headers.get("x-oauth-scopes", "")
    return BotAuth(
        team=resp.get("team") or "workspace",
        user=resp.get("user"),
        scopes=frozenset(s.strip() for s in raw.split(",") if s.strip()),
    )


def missing_scopes(granted: Collection[str]) -> list[str]:
    """必要なスコープのうち、付与されていないもの。"""
    return [s for s in REQUIRED_SLACK_SCOPES if s not in granted]


def channel_pages(bot_token: str) -> Iterator[dict[str, str]]:
    """Bot から見えるチャンネルの id → 名前を、1 ページずつ返す。

    途中のページで失敗しても、それまでのページは呼び出し側に残る。
    """
    cursor = None
    while True:
        listing = slack_sdk.WebClient(token=bot_token).conversations_list(
            types="public_channel,private_channel", limit=200, cursor=cursor
        )
        yield {c["id"]: c["name"] for c in listing["channels"]}
        cursor = listing.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
