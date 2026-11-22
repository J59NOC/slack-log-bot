"""チャンネルと、その公開範囲。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Channel:
    """ログを取る Slack チャンネル。

    is_private はログの置き場所と共有範囲を決める。公開チャンネルは索引
    スプレッドシートのタブに、プライベートチャンネルは専用のスプレッドシートと
    フォルダに置かれ、そのチャンネルのメンバーにだけ共有される。
    """

    id: str
    name: str
    is_private: bool = False


class PrivateChannels:
    """一度プライベートと分かったチャンネルを、プロセスが終わるまで覚えておく。

    プライベートから公開へは戻さない。Slack への問い合わせが失敗すると
    チャンネルは公開扱いで返ってくるため、その都度の結果をそのまま信じると、
    一度の失敗でプライベートチャンネルのログが公開側に書き込まれる。
    """

    def __init__(self) -> None:
        """まだ何も覚えていない状態で始める。"""
        self._ids: set[str] = set()

    def classify(self, channel_id: str, name: str, *, reported_private: bool = False) -> Channel:
        """Slack の申告と、これまでに覚えた内容を合わせてチャンネルを作る。"""
        if reported_private and channel_id:
            self._ids.add(channel_id)
        return Channel(channel_id, name, channel_id in self._ids)

    def __contains__(self, channel_id: str) -> bool:
        """このチャンネルをプライベートと覚えているか。"""
        return channel_id in self._ids
