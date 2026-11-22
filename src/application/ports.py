"""ユースケースが使う外部への口（ポート）。実装は infrastructure にある。

ログと添付ファイルの保管先は domain/repositories.py に、ここには収集元
（Slack）側の口を置く。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterator

    from src.domain.channel import Channel
    from src.domain.people import SlackUser
    from src.domain.post import SlackPost


class DownloadFailedError(Exception):
    """添付ファイルを Slack から取得できなかった。"""


class SlackWorkspace(Protocol):
    """Slack ワークスペースの情報。実装は問い合わせ結果をキャッシュしてよい。"""

    def channel(self, channel_id: str) -> Channel:
        """チャンネルの名前と公開範囲（Slack が今そう言っているもの）。"""

    def channel_name_of(self, channel_id: str) -> str:
        """チャンネルの名前（本文中の <#C...> を名前にするのに使う）。"""

    def forget_channel(self, channel_id: str) -> None:
        """覚えているチャンネル情報を捨てる（改名の後で読み直すため）。"""

    def user(self, user_id: str) -> SlackUser:
        """ユーザーの表示名・ハンドル・メールアドレス。"""

    def username_of(self, user_id: str) -> str:
        """ユーザーの @ハンドル（本文中の <@U...> を名前にするのに使う）。"""

    def cached_user(self, user_id: str) -> SlackUser | None:
        """キャッシュにあるユーザー情報。問い合わせはしない。"""

    def forget_user(self, user_id: str) -> None:
        """覚えているユーザー情報を捨てる（表示名の変更の後で読み直すため）。"""

    def member_emails(self, channel_id: str) -> list[str]:
        """メールアドレスの分かるメンバー全員。"""

    def forget_members(self, channel_id: str) -> None:
        """覚えているメンバー一覧を捨てる（参加・退出の後で読み直すため）。"""

    def history(self, channel: Channel, oldest_ts: str) -> Iterator[SlackPost]:
        """oldest_ts 以降の投稿（返信は含まない）。"""

    def replies(self, channel: Channel, parent_ts: str) -> list[SlackPost]:
        """スレッドの返信（親は含まない）。"""


class BotIdentity(Protocol):
    """Bot 自身の識別。"""

    def user_ids(self) -> tuple[str, ...]:
        """Bot 自身のユーザー ID。自分の投稿をログから外すのに使う。"""

    def handle(self) -> str:
        """Slack で bot を呼ぶときの名前。"""


class FileDownloader(Protocol):
    """添付ファイルの取得。"""

    def download(self, url: str) -> bytes:
        """取得できなければ DownloadFailedError。"""
