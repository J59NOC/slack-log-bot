"""過去の履歴をまとめて収集する（バックフィル・週次収集）。

Realtime backfill, the one-off backfill script and the weekly collection all
walk the same history-and-threads loop. Keeping three copies meant every
change had to be made three times, and a change missed in one of them would
only show up as one entry point behaving differently from the others.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from src.domain.log_entry import LogEntry
from src.domain.text import render_slack_text

if TYPE_CHECKING:
    from src.application.attachments import AttachmentArchiver
    from src.application.channels import ChannelResolver
    from src.application.ports import BotIdentity, SlackWorkspace
    from src.domain.channel import Channel
    from src.domain.post import SlackPost
    from src.domain.repositories import ChannelLogRepository

logger = logging.getLogger(__name__)


def oldest_ts_for(days: int, now: datetime | None = None) -> str:
    """Days 日前の Slack タイムスタンプ。"""
    oldest = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    return str(oldest.timestamp())


class CollectionService:
    """過去の履歴をまとめて集めるユースケース（バックフィル・週次収集）。"""

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

    def collect(
        self,
        channel: Channel,
        member_emails: list[str],
        oldest_ts: str,
        skip_user_ids: tuple[str, ...] = (),
    ) -> tuple[int, int]:
        """oldest_ts 以降を集めて、未記録のものをログに追記する。(追記数, 重複数)"""
        # Attachments of messages already recorded would be downloaded and
        # re-uploaded on every run: the rows get deduped at write time, the
        # Drive files do not, so each run leaves another copy behind.
        known_ts = self._log.recorded_ts(channel, member_emails)
        entries = self.gather(channel, oldest_ts, known_ts, member_emails, skip_user_ids)
        return self._log.append_new(channel, entries, member_emails)

    def backfill_channel(self, channel_id: str, days: int) -> tuple[int, int]:
        """Collect past messages for a single channel (Slack からの backfill コマンド)."""
        channel = self._channels.lookup(channel_id)
        member_emails = self._workspace.member_emails(channel_id)
        new_count, skip_count = self.collect(
            channel,
            member_emails,
            oldest_ts_for(days),
            self._bot.user_ids(),
        )
        logger.info("Backfill #%s: %s new, %s skipped", channel.name, new_count, skip_count)
        return new_count, skip_count

    def gather(
        self,
        channel: Channel,
        oldest_ts: str,
        known_ts: set[str],
        member_emails: list[str] | None = None,
        skip_user_ids: tuple[str, ...] = (),
    ) -> list[LogEntry]:
        """Every message and thread reply in the channel since oldest_ts.

        `known_ts` is only used to skip attachment uploads; the entries
        themselves are returned either way and deduped when written, so a reply
        arriving under an already-recorded parent is still picked up.
        """
        collected: list[LogEntry] = []
        for post in self._workspace.history(channel, oldest_ts):
            if post.is_bookkeeping or post.is_by(skip_user_ids):
                continue

            # Not None: a message broadcast to the channel from a thread comes
            # back from history carrying its thread_ts, and appears again in
            # that thread's replies. Forcing None here made the two copies look
            # like different rows — one a parent, one a reply — and both were
            # kept.
            entry = self._entry(channel, post, post.thread_ts, known_ts, member_emails)
            if entry is None:
                continue
            collected.append(entry)

            if post.reply_count > 0:
                collected.extend(
                    self._replies(
                        channel,
                        entry.ts,
                        known_ts,
                        member_emails,
                        skip_user_ids,
                    )
                )
        return collected

    def _replies(
        self,
        channel: Channel,
        parent_ts: str,
        known_ts: set[str],
        member_emails: list[str] | None,
        skip_user_ids: tuple[str, ...],
    ) -> list[LogEntry]:
        replies = []
        for reply in self._workspace.replies(channel, parent_ts):
            if reply.is_bookkeeping or reply.is_by(skip_user_ids):
                continue
            entry = self._entry(channel, reply, parent_ts, known_ts, member_emails)
            if entry is not None:
                replies.append(entry)
        return replies

    def _entry(
        self,
        channel: Channel,
        post: SlackPost,
        thread_ts: str | None,
        known_ts: set[str],
        member_emails: list[str] | None,
    ) -> LogEntry | None:
        if not post.user_id:
            return None

        author = self._workspace.user(post.user_id).display_name

        # Attachments of a message already recorded would be uploaded to Drive
        # again: the row is deduped at write time, the file is not.
        attachments = []
        if post.ts not in known_ts:
            attachments = self._attachments.store(channel, post.files, member_emails)

        return LogEntry(
            author=author,
            text=render_slack_text(
                post.text, self._workspace.username_of, self._workspace.channel_name_of
            ),
            ts=post.ts,
            thread_ts=thread_ts,
            attachments=tuple(attachments),
        )
