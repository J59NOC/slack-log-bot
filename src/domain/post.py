"""Slack から届いた投稿と添付ファイル（記録する前の、収集元の姿）。"""

from __future__ import annotations

from dataclasses import dataclass

# Joins and leaves are channel bookkeeping, not conversation. bot_message
# covers webhooks and apps; the bot's own replies are excluded separately,
# by author, since messages posted with a bot token carry no subtype.
IGNORED_SUBTYPES = ("bot_message", "channel_join", "channel_leave")


@dataclass(frozen=True)
class SourceFile:
    """Slack 上の添付ファイル。Drive に保存する前のもの。"""

    name: str | None
    size: int = 0
    mimetype: str = "application/octet-stream"
    # Slack の非公開ダウンロード URL。外部リンク（Slack に貼られた Google
    # スプレッドシートの URL など）はファイル扱いで届くが、これを持たない。
    download_url: str | None = None

    @property
    def stored_name(self) -> str:
        """Drive に保存するときの名前。"""
        return "unknown_file" if self.name is None else self.name

    def exceeds(self, max_size: int) -> bool:
        """保存できる大きさ（max_size バイト）を超えているか。"""
        return self.size > max_size

    @property
    def is_downloadable(self) -> bool:
        """Slack からダウンロードできる添付ファイルか。"""
        # External links (for example a Google Sheets URL pasted into Slack)
        # can appear in the file payload, but they are not downloadable with
        # the Slack token. Only Slack's private download URL is an attachment.
        return bool(self.download_url)


@dataclass(frozen=True)
class SlackPost:
    """Slack が伝えてきた 1 件の投稿。"""

    ts: str
    user_id: str = ""
    text: str = ""
    thread_ts: str | None = None
    subtype: str | None = None
    files: tuple[SourceFile, ...] = ()
    reply_count: int = 0

    @property
    def is_bookkeeping(self) -> bool:
        """参加・退出や外部アプリの投稿など、会話ではないもの。"""
        return self.subtype in IGNORED_SUBTYPES

    def is_by(self, user_ids: tuple[str, ...]) -> bool:
        """これらのユーザーの誰かの投稿か。"""
        return self.user_id in user_ids
