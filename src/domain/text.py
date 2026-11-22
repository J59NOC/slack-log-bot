"""Slack の本文を、Slack 上で見えるとおりの文字列にする。"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


def render_slack_text(
    text: str,
    username_of: Callable[[str], str],
    channel_name_of: Callable[[str], str],
) -> str:
    """Turn Slack's raw markup into what a reader sees in Slack.

    Message text arrives with mentions encoded as ids — <@U012ABC>, <#C012ABC>,
    <!here> — so a log of it is unreadable without expanding them.

    ラベルの無い <@U...> と <#C...> は、渡された関数で名前を引く。
    """
    if not text:
        return text

    def user(match: re.Match) -> str:
        user_id, _, label = match.group(1).partition("|")
        if label:
            return f"@{label}"
        return f"@{username_of(user_id)}"

    def channel(match: re.Match) -> str:
        channel_id, _, label = match.group(1).partition("|")
        if label:
            return f"#{label}"
        return f"#{channel_name_of(channel_id)}"

    def special(match: re.Match) -> str:
        # <!here>, <!channel>, <!subteam^S123|@group>, <!date^...^fallback>
        body = match.group(1)
        if "|" in body:
            label = body.split("|", 1)[1]
            return label if label.startswith("@") else f"@{label}"
        return f"@{body.split('^')[0]}"

    def link(match: re.Match) -> str:
        url, _, label = match.group(1).partition("|")
        return label or url

    text = re.sub(r"<@([^>]+)>", user, text)
    text = re.sub(r"<#([^>]+)>", channel, text)
    text = re.sub(r"<!([^>]+)>", special, text)
    text = re.sub(r"<((?:https?|mailto):[^>]+)>", link, text)

    # Slack escapes these three in message text
    return text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
