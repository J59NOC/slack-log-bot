"""Slack のユーザーと、ログの共有範囲の変化。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple


@dataclass(frozen=True)
class SlackUser:
    """Slack のユーザー。"""

    id: str
    # Slack で見えている名前。ログの「表示名」列にはこれが入る
    display_name: str
    # @ハンドル。本文中のメンションはこちらで表す
    username: str
    # 空なら共有できない（bot や、メールを読めないアカウント）
    email: str = ""


class AccessChange(NamedTuple):
    """共有を揃えた結果。何人に共有し、何人から外したか。"""

    granted: int = 0
    revoked: int = 0

    def __add__(self, other: AccessChange) -> AccessChange:  # type: ignore[override]
        """2 つの結果を足し合わせる。"""
        return AccessChange(self.granted + other.granted, self.revoked + other.revoked)
