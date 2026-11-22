"""チャンネル名・表示名の変更をログに追従させる。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.application.channels import ChannelResolver
    from src.application.ports import SlackWorkspace
    from src.domain.repositories import AttachmentStore, ChannelLogRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthorRename:
    """ある人の表示名の変更（変更前と変更後）。"""

    user_id: str
    old_name: str
    new_name: str


class RenameService:
    """チャンネル名・表示名の変更をログに追従させるユースケース。"""

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

    def channel_renamed(self, channel_id: str, new_name: str, previous_name: str = "") -> bool:
        """Keep the channel's spreadsheet and folder with it when it is renamed."""
        self._workspace.forget_channel(channel_id)
        channel = self._channels.known(channel_id, new_name)
        moved = False
        try:
            moved |= self._log.rename_channel(channel, previous_name)
        except Exception as e:
            logger.error("Failed to follow spreadsheet rename for %s: %s", channel_id, e)
        try:
            moved |= self._store.rename_channel(channel)
        except Exception as e:
            logger.error("Failed to follow Drive rename for %s: %s", channel_id, e)
        if moved:
            logger.info("Followed channel rename to #%s", new_name)
        return moved

    def author_changed(self, user_id: str) -> AuthorRename | None:
        """プロフィールが変わったユーザーの、表示名の変更を調べる。

        Slack shows a renamed person's current name on their old messages;
        leaving the sheet on the old one would split one person into two, each
        with their own colour. 変更前の名前はキャッシュにしか無いので、
        キャッシュに無い人の変更は追えない。
        """
        previous = self._workspace.cached_user(user_id)
        self._workspace.forget_user(user_id)
        if previous is None:
            return None

        new_name = self._workspace.user(user_id).display_name
        if not new_name or new_name == previous.display_name:
            return None
        return AuthorRename(user_id, previous.display_name, new_name)

    def apply_author_rename(self, rename: AuthorRename) -> None:
        """記録済みの行の表示名を書き換える（行が多いと時間がかかる）。"""
        try:
            rows = self._log.rename_author(rename.old_name, rename.new_name)
            logger.info("Display name %s -> %s (%s row(s))", rename.old_name, rename.new_name, rows)
        except Exception as e:
            logger.error("Failed to follow the rename of %s: %s", rename.user_id, e)
