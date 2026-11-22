"""ログと添付ファイルの保管先（リポジトリ）の約束事。

実装は infrastructure にあり（Google スプレッドシートと Google Drive）、
アプリケーション層はここに書かれた操作だけを使う。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable

    from src.domain.channel import Channel
    from src.domain.log_entry import Attachment, LogEntry
    from src.domain.people import AccessChange


class ChannelLogRepository(Protocol):
    """チャンネルごとのメッセージログ。"""

    def recorded_ts(self, channel: Channel, member_emails: list[str] | None = None) -> set[str]:
        """記録済みメッセージの ts。"""

    def insert(
        self, channel: Channel, entry: LogEntry, member_emails: list[str] | None = None
    ) -> bool:
        """1 件を記録する。返信は親スレッドの末尾に入る。記録済みなら False。"""

    def append_new(
        self,
        channel: Channel,
        entries: Iterable[LogEntry],
        member_emails: list[str] | None = None,
    ) -> tuple[int, int]:
        """未記録のものだけをスレッド順に追記する。(追記数, 重複数) を返す。"""

    def link_attachments(
        self,
        channel: Channel,
        ts: str,
        attachments: list[Attachment],
        member_emails: list[str] | None = None,
    ) -> None:
        """記録済みの行に、後から保存できた添付ファイルを載せる。"""

    def update_text(
        self, channel: Channel, ts: str, text: str, member_emails: list[str] | None = None
    ) -> bool:
        """編集されたメッセージの本文を書き換える。"""

    def remove(
        self, channel: Channel, ts: str, member_emails: list[str] | None = None
    ) -> list[str] | None:
        """削除されたメッセージの行を消す。

        消した行が指していた添付ファイルの id を返す。行が無ければ None。
        """

    def rename_channel(self, channel: Channel, previous_name: str = "") -> bool:
        """改名されたチャンネルのログを新しい名前に付け替える。"""

    def rename_author(self, old_name: str, new_name: str) -> int:
        """表示名の変更を記録済みの行に反映する。書き換えた行数を返す。"""

    def url_for(self, channel: Channel) -> str | None:
        """ログの URL。まだ無ければ None。"""

    def sync_access(
        self, channel: Channel, member_emails: list[str], revoke: list[str] | None = None
    ) -> AccessChange:
        """ログの共有をチャンネルのメンバーに揃える。"""


class AttachmentStore(Protocol):
    """添付ファイルの保管先。"""

    def upload(
        self,
        channel: Channel,
        file_name: str,
        content: bytes,
        mime_type: str,
        member_emails: list[str] | None = None,
    ) -> Attachment:
        """チャンネルのフォルダに保存し、名前とリンクを返す。"""

    def trash(self, file_ids: list[str]) -> None:
        """ゴミ箱へ移す（しばらくは復元できる）。"""

    def sync_access(
        self, channel: Channel, member_emails: list[str], revoke: list[str] | None = None
    ) -> AccessChange:
        """チャンネルのフォルダの共有をメンバーに揃える。"""

    def rename_channel(self, channel: Channel) -> bool:
        """改名されたチャンネルのフォルダ名を付け替える。"""
