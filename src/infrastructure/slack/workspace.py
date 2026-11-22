"""Slack ワークスペースの情報（チャンネル・ユーザー・メンバー・履歴）を引く。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.domain.channel import Channel
from src.domain.people import SlackUser
from src.infrastructure.slack.payloads import is_private_channel, slack_post

if TYPE_CHECKING:
    from collections.abc import Iterator

    from slack_sdk import WebClient

    from src.domain.post import SlackPost

logger = logging.getLogger(__name__)

# Member lists change over time, so their cache expires. Channel and user info
# are effectively immutable for our purposes and are cached for the process life.
MEMBER_EMAIL_CACHE_TTL = 600  # seconds

PAGE_SIZE = 200


@dataclass(frozen=True)
class ChannelListing:
    """conversations.list の 1 件。"""

    id: str
    name: str
    is_private: bool
    is_member: bool


class SlackWorkspace:
    """SlackWorkspace ポートの Slack Web API 実装。問い合わせ結果はキャッシュする。"""

    def __init__(self, client: WebClient) -> None:
        """問い合わせに使う Slack クライアントを受け取る。"""
        self.client = client
        # Cache: channel_id -> Channel
        self._channels: dict[str, Channel] = {}
        # Cache: user_id -> SlackUser
        self._users: dict[str, SlackUser] = {}
        # Cache: channel_id -> (cached_at_monotonic, list of member emails)
        self._member_emails: dict[str, tuple[float, list[str]]] = {}

    # ── チャンネル ──

    def channel(self, channel_id: str) -> Channel:
        """Get channel info including name and is_private flag.

        問い合わせに失敗すると、id を名前にした公開チャンネルとして返す。
        """
        if channel_id in self._channels:
            return self._channels[channel_id]
        try:
            result = self.client.conversations_info(channel=channel_id)
            ch = result["channel"]
            info = Channel(channel_id, ch["name"], is_private=bool(is_private_channel(ch)))
        except Exception as e:
            logger.error("Failed to get channel info %s: %s", channel_id, e)
            return Channel(channel_id, channel_id, is_private=False)
        self._channels[channel_id] = info
        return info

    def channel_name_of(self, channel_id: str) -> str:
        """チャンネルの名前。"""
        return self.channel(channel_id).name

    def forget_channel(self, channel_id: str) -> None:
        """Drop the cached name so a rename is picked up without a restart."""
        self._channels.pop(channel_id, None)

    def list_channels(self) -> list[ChannelListing]:
        """Public and private channels visible to the bot."""
        channels = []
        cursor = None
        while True:
            resp = self.client.conversations_list(
                types="public_channel,private_channel",
                limit=PAGE_SIZE,
                cursor=cursor,
            )
            channels.extend(resp["channels"])
            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
        return [
            ChannelListing(
                ch["id"],
                ch["name"],
                bool(is_private_channel(ch)),
                bool(ch.get("is_member")),
            )
            for ch in channels
        ]

    # ── ユーザー ──

    def user(self, user_id: str) -> SlackUser:
        """Resolve a Slack user ID to its display name, username and email."""
        if user_id in self._users:
            return self._users[user_id]
        try:
            result = self.client.users_info(user=user_id)
            user = result["user"]
            profile = user.get("profile", {})
            display_name = (
                profile.get("display_name")
                or profile.get("real_name")
                or user.get("real_name")
                or user.get("name")
                or user_id
            )
            resolved = SlackUser(
                user_id,
                display_name,
                user.get("name", user_id),
                profile.get("email", ""),
            )
        except Exception as e:
            logger.error("Failed to resolve user %s: %s", user_id, e)
            return SlackUser(user_id, user_id, user_id, "")
        self._users[user_id] = resolved
        return resolved

    def username_of(self, user_id: str) -> str:
        """ユーザーの @ハンドル。"""
        return self.user(user_id).username

    def cached_user(self, user_id: str) -> SlackUser | None:
        """キャッシュにある（＝変更前の）ユーザー情報。問い合わせはしない。"""
        return self._users.get(user_id)

    def forget_user(self, user_id: str) -> None:
        """Drop a cached profile so a renamed person is re-read on next use."""
        self._users.pop(user_id, None)

    # ── メンバー ──

    def forget_members(self, channel_id: str) -> None:
        """Drop the cached member list so the next read reflects a join or leave."""
        self._member_emails.pop(channel_id, None)

    def member_emails(self, channel_id: str) -> list[str]:
        """Get Google-compatible email addresses for all members of a channel.

        Cached with a TTL so that members who join later still get access granted.
        """
        cached = self._member_emails.get(channel_id)
        if cached and (time.monotonic() - cached[0]) < MEMBER_EMAIL_CACHE_TTL:
            return cached[1]

        member_ids = []
        cursor = None
        while True:
            try:
                resp = self.client.conversations_members(
                    channel=channel_id, limit=PAGE_SIZE, cursor=cursor
                )
                member_ids.extend(resp.get("members", []))
                cursor = resp.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
            except Exception as e:
                logger.error("Failed to get channel members: %s", e)
                # Keep serving the stale list rather than silently dropping members
                # from the share list on a transient API failure.
                if cached:
                    return cached[1]
                break

        emails = []
        for uid in member_ids:
            email = self.user(uid).email
            if email:
                emails.append(email)
            else:
                logger.warning("No email for user %s, skipping permission grant", uid)

        self._member_emails[channel_id] = (time.monotonic(), emails)
        return emails

    # ── 履歴 ──

    def history(self, channel: Channel, oldest_ts: str) -> Iterator[SlackPost]:
        """oldest_ts 以降のチャンネルの投稿（スレッドの返信は含まない）。

        ページは必要になった時点で読む。取得に失敗したらそこで打ち切る。
        """
        cursor = None
        while True:
            try:
                resp = self.client.conversations_history(
                    channel=channel.id, oldest=oldest_ts, limit=PAGE_SIZE, cursor=cursor
                )
            except Exception as e:
                logger.error("Failed to fetch history for #%s: %s", channel.name, e)
                return

            for message in resp.get("messages", []):
                yield slack_post(message)

            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                return

    def replies(self, channel: Channel, parent_ts: str) -> list[SlackPost]:
        """スレッドの返信。親は含まない。失敗したら空。"""
        try:
            resp = self.client.conversations_replies(
                channel=channel.id, ts=parent_ts, limit=PAGE_SIZE
            )
        except Exception as e:
            logger.error("Failed to fetch thread replies: %s", e)
            return []
        # The first entry is the parent, which the caller already has.
        return [slack_post(m) for m in resp.get("messages", [])[1:]]
