"""bot へのメンションで使えるコマンドの解釈。

コマンド:
    @bot              → Show spreadsheet URL + help
    @bot url          → Show spreadsheet URL
    @bot backfill     → Collect past messages (default 90 days)
    @bot backfill 30  → Collect past 30 days
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass

DEFAULT_BACKFILL_DAYS = 90


@dataclass(frozen=True)
class ShowHelp:
    """コマンド一覧とログの URL を表示する。"""


@dataclass(frozen=True)
class ShowUrl:
    """ログの URL を表示する。"""


@dataclass(frozen=True)
class Backfill:
    """過去 days 日分を集める。"""

    days: int = DEFAULT_BACKFILL_DAYS


@dataclass(frozen=True)
class Unknown:
    """知らないコマンド。"""


MentionCommand = ShowHelp | ShowUrl | Backfill | Unknown


def parse_mention(text: str) -> MentionCommand:
    """メンションの本文からコマンドを読み取る。"""
    # Strip the mention to leave the command. Slack usually sends <@U012ABC>,
    # but the labelled form <@U012ABC|name> also occurs; without the optional
    # label the id stayed in the string and every command read as unknown.
    cleaned = re.sub(r"<@[A-Za-z0-9]+(\|[^>]*)?>", "", text).strip().lower()

    if cleaned.startswith("backfill"):
        parts = cleaned.split()
        days = DEFAULT_BACKFILL_DAYS
        # 日数が数字でなければ既定のまま
        if len(parts) > 1:
            with contextlib.suppress(ValueError):
                days = int(parts[1])
        return Backfill(days)
    if cleaned == "url":
        return ShowUrl()
    if cleaned in ("help", ""):
        return ShowHelp()
    return Unknown()
