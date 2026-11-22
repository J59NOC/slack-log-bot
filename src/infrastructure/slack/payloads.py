"""Slack API / イベントの辞書を、ドメインの値に変換する（腐敗防止層）。

Slack のペイロードの形を知っているのはここだけにする。
"""

from __future__ import annotations

from src.domain.post import SlackPost, SourceFile


def source_file(info: dict) -> SourceFile:
    """Slack の添付ファイルの情報を、ドメインの値にする。"""
    return SourceFile(
        name=info.get("name"),
        size=info.get("size", 0),
        mimetype=info.get("mimetype", "application/octet-stream"),
        download_url=info.get("url_private_download"),
    )


def slack_post(message: dict) -> SlackPost:
    """conversations.history / replies の 1 件、または message イベント。"""
    return SlackPost(
        ts=message.get("ts", ""),
        user_id=message.get("user", ""),
        text=message.get("text", ""),
        thread_ts=message.get("thread_ts"),
        subtype=message.get("subtype"),
        files=tuple(source_file(f) for f in message.get("files", [])),
        reply_count=message.get("reply_count", 0),
    )


def is_private_channel(channel: dict) -> bool:
    """conversations.info / list のチャンネルがプライベートか。

    is_group は古い形式のプライベートチャンネル。
    """
    return channel.get("is_private", False) or channel.get("is_group", False)
