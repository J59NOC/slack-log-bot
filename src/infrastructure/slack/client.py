"""Slack クライアントの設定と、bot 自身の識別。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from slack_sdk.http_retry.builtin_handlers import (
    ConnectionErrorRetryHandler,
    RateLimitErrorRetryHandler,
)

if TYPE_CHECKING:
    from slack_sdk import WebClient

logger = logging.getLogger(__name__)


def install_retry_handlers(client: WebClient) -> None:
    """Make a Slack client retry on rate limits and connection errors.

    The SDK honours the Retry-After header, which is far more accurate than
    sprinkling fixed sleeps through the collection loops.
    """
    installed = {type(h) for h in client.retry_handlers}
    for handler_cls in (ConnectionErrorRetryHandler, RateLimitErrorRetryHandler):
        if handler_cls not in installed:
            client.retry_handlers.append(handler_cls(max_retry_count=3))


class SlackBotIdentity:
    """bot 自身のユーザー ID と呼び名。最初に必要になったときに一度だけ引く。"""

    def __init__(self, client: WebClient) -> None:
        """問い合わせに使う Slack クライアントを受け取る。"""
        self.client = client
        self._user_ids: tuple[str, ...] | None = None
        self._handle: str | None = None

    def user_ids(self) -> tuple[str, ...]:
        """Return the bot's own user id, so its replies stay out of the log.

        Messages posted with a bot token carry no subtype, so the bot_message
        filter never sees them and its "collection started" notices end up
        recorded as conversation.
        """
        if self._user_ids is None:
            try:
                self._user_ids = (self.client.auth_test().get("user_id", ""),)
            except Exception as e:
                # Not remembered: an empty answer kept for the life of the
                # process would let every later post of the bot's into the log.
                logger.warning("Could not resolve the bot's own user id: %s", e)
                return ()
        return self._user_ids

    def fetch_user_ids(self) -> tuple[str, ...]:
        """user_ids() と同じだが、引けなければ例外にする（CLI 用）。

        自分の投稿を除けないまま収集を進めるより、止まるほうがよい。
        """
        self._user_ids = (self.client.auth_test().get("user_id", ""),)
        return self._user_ids

    def handle(self) -> str:
        """Return how to address the bot, as Slack currently spells it.

        Read from Slack rather than hard-coded: the app can be renamed at any
        time, and help text naming a handle that no longer works is worse than
        no help text.
        """
        if self._handle is None:
            try:
                user_id = self.client.auth_test().get("user_id", "")
                profile = self.client.users_info(user=user_id)["user"]
                self._handle = (
                    profile.get("profile", {}).get("display_name")
                    or profile.get("real_name")
                    or profile.get("name")
                    or "bot"
                )
            except Exception as e:
                logger.warning("Could not resolve the bot's own name: %s", e)
                self._handle = "bot"
        return self._handle
