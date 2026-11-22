"""コマンドラインからの収集（週次収集とバックフィル）。

どちらも「bot が参加しているチャンネルの、過去 N 日分」を集めてログに追記する。
重複は書き込み時に除くので、期間が重なっても、何度実行しても問題ない。
"""

from __future__ import annotations

import argparse
import logging
import os
from typing import TYPE_CHECKING

from src.application.collection import oldest_ts_for
from src.bootstrap import services_for_cli
from src.infrastructure.settings import load_settings
from src.presentation.logging_config import configure_logging

if TYPE_CHECKING:
    from src.bootstrap import Services
    from src.infrastructure.slack.workspace import ChannelListing

logger = logging.getLogger(__name__)


def _member_channels(listed: list[ChannelListing], *, public_only: bool) -> list[ChannelListing]:
    channels = [ch for ch in listed if ch.is_member]
    if public_only:
        channels = [ch for ch in channels if not ch.is_private]
    return channels


def run_weekly(
    services: Services,
    *,
    channel_filter: str | None = None,
    days: int = 8,
    public_only: bool = False,
) -> None:
    """Bot が参加しているチャンネルの過去 days 日分を集め、共有もメンバーに揃える。"""
    # The bot's own replies ("collection started", "here is the log") are
    # posted with a bot token, which carries no subtype to filter on.
    bot_user_ids = services.bot.fetch_user_ids()

    channels = _member_channels(services.workspace.list_channels(), public_only=public_only)
    if channel_filter:
        channels = [ch for ch in channels if ch.name == channel_filter]
        if not channels:
            logger.error("Channel '%s' not found or bot is not a member.", channel_filter)
            return

    oldest_ts = oldest_ts_for(days)
    total_new = 0
    total_skipped = 0

    for listing in channels:
        channel = services.channels.classify(
            listing.id, listing.name, is_private=listing.is_private
        )
        label = "private" if channel.is_private else "public"
        logger.info("Collecting #%s [%s] (past %s days)...", channel.name, label, days)

        # Every channel's sheet is shared with that channel's members.
        member_emails = services.workspace.member_emails(channel.id)
        try:
            granted = services.access.share_with_members(channel, member_emails)
            if granted:
                logger.info("  #%s: shared with %s new member(s)", channel.name, granted)
        except Exception as e:
            logger.warning("  #%s: could not sync access: %s", channel.name, e)

        new_count, skip_count = services.collection.collect(
            channel,
            member_emails,
            oldest_ts,
            bot_user_ids,
        )
        logger.info("  #%s: %s new, %s duplicates skipped", channel.name, new_count, skip_count)
        total_new += new_count
        total_skipped += skip_count

    logger.info(
        "Weekly collection complete. New: %s, Skipped (duplicate): %s", total_new, total_skipped
    )


def _report_missing_channel(requested_name: str, listed: list[ChannelListing]) -> None:
    listed_match = next((ch for ch in listed if ch.name == requested_name), None)
    if listed_match:
        logger.error(
            "Channel '#%s' はSlack APIでは見えていますが、"
            "botが未参加です（channel_id=%s）。"
            "先に /invite @slack-log-bot-private を実行してください。",
            requested_name,
            listed_match.id,
        )
    else:
        logger.error(
            "Channel '#%s' はSlack APIの一覧にありません。"
            "チャンネル名、Slack App、SLACK_BOT_TOKENのworkspaceを確認してください。"
            "（API取得件数: %s）",
            requested_name,
            len(listed),
        )


def run_backfill(
    services: Services,
    *,
    channel_filter: str | None = None,
    days: int = 90,
    public_only: bool = False,
) -> None:
    """Bot が参加しているチャンネルの過去 days 日分を集める。"""
    # The bot's own replies ("collection started", "here is the log") are
    # posted with a bot token, which carries no subtype to filter on.
    bot_user_ids = services.bot.fetch_user_ids()

    listed = services.workspace.list_channels()
    channels = _member_channels(listed, public_only=public_only)

    if channel_filter:
        requested_name = channel_filter.lstrip("#")
        channels = [ch for ch in channels if ch.name == requested_name]
        if not channels:
            _report_missing_channel(requested_name, listed)
            return

    oldest_ts = oldest_ts_for(days)
    total_new = 0
    total_skipped = 0

    for listing in channels:
        channel = services.channels.classify(
            listing.id, listing.name, is_private=listing.is_private
        )
        label = "private" if channel.is_private else "public"
        logger.info("Backfilling #%s [%s]...", channel.name, label)

        # Every channel's sheet is shared with that channel's members.
        member_emails = services.workspace.member_emails(channel.id)

        new_count, skip_count = services.collection.collect(
            channel,
            member_emails,
            oldest_ts,
            bot_user_ids,
        )
        logger.info("  #%s: %s new, %s duplicates skipped", channel.name, new_count, skip_count)
        total_new += new_count
        total_skipped += skip_count

    logger.info("Backfill complete. New: %s, Skipped (duplicate): %s", total_new, total_skipped)


def weekly_main(argv: list[str] | None = None) -> None:
    """週次収集の入口（python collect_weekly.py）。"""
    parser = argparse.ArgumentParser(description="Weekly Slack message collection")
    parser.add_argument("--channel", type=str, help="Channel name (default: all)")
    parser.add_argument(
        "--public",
        action="store_true",
        help="Collect all public channels the bot is in",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=8,
        help="Days to look back (default: 8 = 7 days + 1 day overlap margin)",
    )
    args = parser.parse_args(argv)

    if args.channel and args.public:
        parser.error("--channel and --public cannot be used together")

    # 設定（.env）を読んでからログの出力先を決める。COLLECT_WEEKLY_LOG は
    # プロファイルの .env にも書けるので、先に決めるとそれが効かない。
    settings = load_settings()
    # A container's filesystem is read-only to the bot and its stdout is already
    # kept by Docker, so the log file can be turned off with COLLECT_WEEKLY_LOG="".
    configure_logging(os.environ.get("COLLECT_WEEKLY_LOG", "collect_weekly.log"))

    run_weekly(
        services_for_cli(settings),
        channel_filter=args.channel,
        days=args.days,
        public_only=args.public,
    )


def backfill_main(argv: list[str] | None = None) -> None:
    """バックフィルの入口（python backfill.py）。"""
    configure_logging()

    parser = argparse.ArgumentParser(description="Backfill Slack messages to Google Sheets")
    parser.add_argument("--channel", type=str, help="Channel name to backfill (default: all)")
    parser.add_argument(
        "--public",
        action="store_true",
        help="Backfill all public channels the bot is in",
    )
    parser.add_argument(
        "--days", type=int, default=90, help="Number of days to look back (default: 90)"
    )
    args = parser.parse_args(argv)

    if args.channel and args.public:
        parser.error("--channel and --public cannot be used together")

    run_backfill(
        services_for_cli(load_settings()),
        channel_filter=args.channel,
        days=args.days,
        public_only=args.public,
    )
