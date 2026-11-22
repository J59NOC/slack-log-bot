"""Slack Log Bot - Socket Mode でイベントを受け、ユースケースを呼ぶ。

ここではイベントの形を読み解いて振り分けるだけで、何をどう記録するかは
application 層が決める。時間のかかる処理（添付ファイルの保存、バックフィル、
表示名の書き換え）は別スレッドで動かし、Slack への応答を待たせない。
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from src.bootstrap import connect_google, wire
from src.infrastructure.settings import load_settings
from src.infrastructure.slack.client import install_retry_handlers
from src.infrastructure.slack.payloads import slack_post
from src.presentation.commands import Backfill, ShowHelp, ShowUrl, parse_mention
from src.presentation.logging_config import configure_logging

if TYPE_CHECKING:
    from collections.abc import Callable

    from slack_bolt import Say
    from slack_sdk import WebClient

    from src.bootstrap import Services
    from src.domain.channel import Channel

# Slack から届くイベントの本文（JSON）
Event = dict[str, Any]

logger = logging.getLogger(__name__)


def _in_background(target: Callable[..., object], *args: object) -> None:
    threading.Thread(target=target, args=args, daemon=True).start()


class SlackEventHandlers:
    """Slack のイベントを受けて、対応するユースケースを呼ぶ。"""

    def __init__(self, services: Services) -> None:
        """呼び出すユースケース一式を受け取る。"""
        self._services = services

    def register(self, app: App) -> None:
        """Bolt の App にイベントハンドラを登録する。"""
        app.event("message")(self.handle_message)
        app.event("user_change")(self.handle_user_change)
        app.event("file_shared")(self.handle_file_shared)
        app.event("member_joined_channel")(self.handle_member_joined)
        app.event("member_left_channel")(self.handle_member_left)
        app.event("channel_rename")(self.handle_channel_rename)
        app.event("group_rename")(self.handle_channel_rename)
        app.event("app_mention")(self.handle_mention)

    def handle_message(self, event: Event) -> None:
        """投稿・編集・削除を記録に反映する。"""
        archive = self._services.archive
        subtype = event.get("subtype")
        channel_id = event.get("channel", "")
        if subtype == "message_changed":
            edited = event.get("message") or {}
            archive.apply_edit(
                channel_id,
                edited.get("ts", ""),
                edited.get("user", ""),
                edited.get("text", ""),
            )
            return
        if subtype == "message_deleted":
            ts = event.get("deleted_ts") or (event.get("previous_message") or {}).get("ts", "")
            archive.apply_deletion(channel_id, ts)
            return

        pending = archive.record(channel_id, slack_post(event))
        # Upload files in background and update attachment column
        if pending:
            _in_background(archive.attach_files, pending)

    def handle_user_change(self, event: Event) -> None:
        """Follow a display name change across the rows already written."""
        user_id = (event.get("user") or {}).get("id", "")
        if not user_id:
            return
        rename = self._services.renames.author_changed(user_id)
        if rename:
            _in_background(self._services.renames.apply_author_rename, rename)

    def handle_file_shared(self) -> None:
        """何もしない。添付は message イベントの files で受け取る。

        未処理のイベントとして警告が出ないように受けておく。
        """

    def handle_member_joined(self, event: Event) -> None:
        """Share the channel's log with whoever just joined."""
        channel_id, user_id = event.get("channel", ""), event.get("user", "")
        if channel_id and user_id:
            self._services.access.member_joined(channel_id, user_id)

    def handle_member_left(self, event: Event) -> None:
        """Revoke the channel's log from whoever just left."""
        channel_id, user_id = event.get("channel", ""), event.get("user", "")
        if channel_id and user_id:
            self._services.access.member_left(channel_id, user_id)

    def handle_channel_rename(self, event: Event) -> None:
        """Keep the channel's spreadsheet and folder with it when it is renamed."""
        channel = event.get("channel") or {}
        channel_id, new_name = channel.get("id", ""), channel.get("name", "")
        if channel_id and new_name:
            self._services.renames.channel_renamed(
                channel_id,
                new_name,
                event.get("previous_name", ""),
            )

    def handle_mention(self, event: Event, client: WebClient, say: Say) -> None:
        """Handle @bot mentions. See commands.py for the commands."""
        channel = self._services.channels.lookup(event.get("channel", ""))
        command = parse_mention(event.get("text", ""))

        if isinstance(command, Backfill):
            say(
                f":hourglass_flowing_sand: `#{channel.name}` の過去 {command.days} "
                f"日分のログ収集を開始します..."
            )
            _in_background(self._run_backfill, channel, command.days, client)
        elif isinstance(command, ShowUrl):
            self._show_url(channel, say)
        elif isinstance(command, ShowHelp):
            self._show_help(channel, say)
        else:
            say(
                f":thinking_face: 不明なコマンドです。"
                f"`@{self._services.bot.handle()} help` でコマンド一覧を確認できます。"
            )

    def _run_backfill(self, channel: Channel, days: int, client: WebClient) -> None:
        try:
            self._services.collection.backfill_channel(channel.id, days)
            client.chat_postMessage(
                channel=channel.id,
                text=f":white_check_mark: `#{channel.name}` のバックフィルが完了しました。",
            )
        except Exception as e:
            logger.error("Backfill failed: %s", e)
            client.chat_postMessage(
                channel=channel.id,
                text=f":x: バックフィル中にエラーが発生しました: {e}",
            )

    def _show_url(self, channel: Channel, say: Say) -> None:
        url = self._services.archive.url_for(channel)
        if url:
            say(f":memo: `#{channel.name}` のログはこちら:\n{url}")
        else:
            say(
                f":memo: `#{channel.name}` のログはまだ作成されていません。"
                f"メッセージが投稿されると自動的に作成されます。"
            )

    def _show_help(self, channel: Channel, say: Say) -> None:
        me = self._services.bot.handle()
        url = self._services.archive.url_for(channel)
        url_line = f"\n:link: {url}" if url else ""
        say(
            f":memo: *`#{channel.name}` のログBot*{url_line}\n\n"
            f"*コマンド一覧:*\n"
            f"• `@{me} url` — スプレッドシートURL表示\n"
            f"• `@{me} backfill` — 過去90日分のログを収集\n"
            f"• `@{me} backfill 30` — 過去N日分を収集"
        )


def register_handlers(app: App, services: Services) -> None:
    """Bolt の App にイベントハンドラを登録する。"""
    SlackEventHandlers(services).register(app)


def main() -> None:
    """Socket Mode で bot を起動する（python main.py）。"""
    configure_logging()
    settings = load_settings()

    app = App(token=settings.slack_bot_token)
    install_retry_handlers(app.client)
    register_handlers(app, wire(settings, app.client, connect_google()))

    logger.info("Starting Slack Log Bot...")
    logger.info("Bot is listening for messages via Socket Mode.")
    handler = SocketModeHandler(app, settings.slack_app_token)
    handler.start()
