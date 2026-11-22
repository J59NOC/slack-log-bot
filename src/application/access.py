"""ログと添付ファイルの共有を、チャンネルのメンバーに揃える。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.domain.people import AccessChange

if TYPE_CHECKING:
    from src.application.channels import ChannelResolver
    from src.application.ports import SlackWorkspace
    from src.domain.channel import Channel
    from src.domain.repositories import AttachmentStore, ChannelLogRepository

logger = logging.getLogger(__name__)


class AccessService:
    """ログと添付ファイルの共有を、チャンネルのメンバーに揃えるユースケース。"""

    def __init__(
        self,
        workspace: SlackWorkspace,
        channels: ChannelResolver,
        log: ChannelLogRepository,
        store: AttachmentStore,
    ) -> None:
        """使う部品（Slack の情報・チャンネルの解決・ログ・添付の保管先）を受け取る。"""
        self._workspace = workspace
        self._channels = channels
        self._log = log
        self._store = store

    def sync(self, channel_id: str, revoke: list[str] | None = None) -> AccessChange:
        """Bring the channel's spreadsheet and folder in line with its membership.

        片方が失敗しても、もう片方は揃える。
        """
        self._workspace.forget_members(channel_id)
        channel = self._channels.lookup(channel_id)
        member_emails = self._workspace.member_emails(channel_id)
        change = AccessChange()
        try:
            change += self._log.sync_access(channel, member_emails, revoke)
        except Exception as e:
            logger.error("Failed to sync sheet access for #%s: %s", channel.name, e)
        try:
            change += self._store.sync_access(channel, member_emails, revoke)
        except Exception as e:
            logger.error("Failed to sync Drive access for #%s: %s", channel.name, e)
        return change

    def member_joined(self, channel_id: str, user_id: str) -> None:
        """Share the channel's log with whoever just joined."""
        channel = self._channels.lookup(channel_id)
        if not self._workspace.user(user_id).email:
            # Bots have no email, and neither do accounts the token cannot read.
            logger.info("Joined #%s: %s has no email, nothing to share", channel.name, user_id)
            return

        change = self.sync(channel_id)
        if change.granted:
            logger.info("Shared #%s with %s new member(s)", channel.name, change.granted)

    def member_left(self, channel_id: str, user_id: str) -> None:
        """Revoke the channel's log from whoever just left."""
        channel = self._channels.lookup(channel_id)
        email = self._workspace.user(user_id).email
        if not email:
            return

        change = self.sync(channel_id, revoke=[email])
        if change.revoked:
            logger.info("Revoked %s from #%s", email, channel.name)

    def share_with_members(self, channel: Channel, member_emails: list[str]) -> int:
        """ログとフォルダをメンバー全員に共有し、新しく共有した数を返す（週次収集用）。

        Joins are normally picked up live, but a weekly pass catches anything
        the bot missed while it was down. 失敗したら例外のまま返す。
        """
        granted = self._log.sync_access(channel, member_emails).granted
        granted += self._store.sync_access(channel, member_emails).granted
        return granted
