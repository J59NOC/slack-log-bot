"""リアルタイムの記録: 投稿・編集・削除をその場でログに反映する。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.domain.log_entry import LogEntry
from src.domain.text import render_slack_text

if TYPE_CHECKING:
    from src.application.attachments import AttachmentArchiver
    from src.application.channels import ChannelResolver
    from src.application.ports import BotIdentity, SlackWorkspace
    from src.domain.channel import Channel
    from src.domain.post import SlackPost, SourceFile
    from src.domain.repositories import ChannelLogRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingFiles:
    """記録した投稿のうち、まだ保存していない添付ファイル。"""

    channel: Channel
    ts: str
    files: tuple[SourceFile, ...]
    member_emails: list[str]


class ArchiveService:
    """リアルタイムの記録のユースケース（投稿・編集・削除）。"""

    def __init__(
        self,
        workspace: SlackWorkspace,
        bot: BotIdentity,
        channels: ChannelResolver,
        log: ChannelLogRepository,
        attachments: AttachmentArchiver,
    ) -> None:
        """使う部品（Slack の情報・bot の識別・チャンネルの解決・ログ・添付の保存）を受け取る。"""
        self._workspace = workspace
        self._bot = bot
        self._channels = channels
        self._log = log
        self._attachments = attachments

    def render(self, text: str) -> str:
        """Slack の本文を、Slack 上で見えるとおりの文字列にする。"""
        return render_slack_text(text, self._workspace.username_of, self._workspace.channel_name_of)

    def record(self, channel_id: str, post: SlackPost) -> PendingFiles | None:
        """投稿を記録する。添付ファイルがあれば、後で attach_files に渡すものを返す。

        添付ファイルの保存は時間がかかるので、行を先に書いてから別に行う。
        """
        if post.is_bookkeeping:
            return None
        if not post.user_id or post.is_by(self._bot.user_ids()):
            return None

        channel = self._channels.lookup(channel_id)
        # The channel's sheet is shared with exactly this channel's members.
        member_emails = self._workspace.member_emails(channel_id)

        author = self._workspace.user(post.user_id).display_name
        text = self.render(post.text)

        # Log message immediately (without waiting for file uploads)
        try:
            self._log.insert(
                channel,
                LogEntry(author, text, post.ts, post.thread_ts),
                member_emails,
            )
        except Exception as e:
            logger.error("Failed to log message to Sheets: %s", e)

        if post.files:
            return PendingFiles(channel, post.ts, post.files, member_emails)
        return None

    def attach_files(self, pending: PendingFiles) -> None:
        """添付ファイルを保存し、記録済みの行に載せる。"""
        stored = self._attachments.store(pending.channel, pending.files, pending.member_emails)
        if stored:
            self._log.link_attachments(pending.channel, pending.ts, stored, pending.member_emails)

    def apply_edit(self, channel_id: str, ts: str, author_id: str, text: str) -> None:
        """Follow an edit made in Slack, so the row keeps saying what the message says."""
        if not ts or not author_id or author_id in self._bot.user_ids():
            return

        channel = self._channels.lookup(channel_id)
        try:
            self._log.update_text(
                channel,
                ts,
                self.render(text),
                self._workspace.member_emails(channel_id),
            )
        except Exception as e:
            logger.error("Failed to apply an edit in #%s: %s", channel.name, e)

    def apply_deletion(self, channel_id: str, ts: str) -> None:
        """Drop a message from the log once it is deleted in Slack.

        Slack reports the deletion but not who wrote it, so the row is found by
        its timestamp. A timestamp that matches nothing is the bot's own post or
        a message from before it joined, and there is nothing to remove.

        Anything it attached goes to the Drive trash for the same reason the
        row goes — the trash, so a deletion made in error is still recoverable
        for a while.
        """
        if not ts:
            return

        channel = self._channels.lookup(channel_id)
        try:
            file_ids = self._log.remove(
                channel,
                ts,
                self._workspace.member_emails(channel_id),
            )
            if file_ids:
                self._attachments.discard(file_ids)
        except Exception as e:
            logger.error("Failed to remove a deleted message in #%s: %s", channel.name, e)

    def url_for(self, channel: Channel) -> str | None:
        """チャンネルのログの URL。まだ無ければ None。"""
        return self._log.url_for(channel)
