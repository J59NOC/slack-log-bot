"""チャンネル ID から、公開範囲を確定したチャンネルを得る。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.domain.channel import Channel, PrivateChannels

if TYPE_CHECKING:
    from src.application.ports import SlackWorkspace


class ChannelResolver:
    """Slack への問い合わせ結果に、プライベートだった記憶を重ねる。

    ログの置き場所はここで返したチャンネルの is_private で決まるので、
    チャンネルはすべてここを通して得る。
    """

    def __init__(
        self, workspace: SlackWorkspace, private_channels: PrivateChannels | None = None
    ) -> None:
        """Slack の情報と、プライベートだったチャンネルの記憶を受け取る。"""
        self._workspace = workspace
        self._private = private_channels or PrivateChannels()

    def lookup(self, channel_id: str) -> Channel:
        """Slack に問い合わせて（キャッシュがあればそれで）チャンネルを得る。"""
        reported = self._workspace.channel(channel_id)
        return self._private.classify(
            channel_id, reported.name, reported_private=reported.is_private
        )

    def classify(self, channel_id: str, name: str, *, is_private: bool) -> Channel:
        """一覧などで公開範囲が分かっているチャンネル。"""
        return self._private.classify(channel_id, name, reported_private=is_private)

    def known(self, channel_id: str, name: str) -> Channel:
        """問い合わせずに、覚えている公開範囲だけでチャンネルを作る。"""
        return self._private.classify(channel_id, name)
