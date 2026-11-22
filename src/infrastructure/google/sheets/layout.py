"""ログのスプレッドシートの列構成と、行の見た目に関わる値。"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.domain.log_entry import LogEntry

HEADER_ROW = [
    "日時",
    "表示名",
    "メッセージ",
    "添付ファイル",
    "メッセージTS",
    "スレッドTS",
]

# Layouts written by earlier versions, each with the source index of every
# column that survives into HEADER_ROW. Dropped along the way: the channel
# (the spreadsheet is the channel), the display name (a second spelling of the
# username), and the permalink — free-plan messages stop resolving after 90
# days, which is exactly when this archive becomes the only copy, and the link
# is a plain function of the workspace, channel and TS anyway.
LEGACY_LAYOUTS = [
    (
        [
            "日時",
            "チャンネル",
            "表示名",
            "ユーザー名",
            "メッセージ",
            "添付ファイル",
            "パーマリンク",
            "メッセージTS",
            "スレッドTS",
        ],
        [0, 3, 4, 5, 7, 8],
    ),
    (
        [
            "日時",
            "ユーザー名",
            "メッセージ",
            "添付ファイル",
            "パーマリンク",
            "メッセージTS",
            "スレッドTS",
        ],
        [0, 1, 2, 3, 5, 6],
    ),
    # Same columns, but the name column held the @handle rather than the
    # display name people actually see in Slack.
    (
        ["日時", "ユーザー名", "メッセージ", "添付ファイル", "メッセージTS", "スレッドTS"],
        [0, 1, 2, 3, 4, 5],
    ),
]

# Column indices (1-indexed)
USERNAME_COLUMN = 2
MESSAGE_COLUMN = 3
ATTACHMENT_COLUMN = 4
TS_COLUMN = 5
THREAD_TS_COLUMN = 6

# Column widths (pixels). Total kept near 1000 so the sheet fits a laptop
# screen without horizontal scrolling.
COLUMN_WIDTHS = [130, 120, 450, 150, 90, 90]

# Rows grew to fit their content, so one long message could take thirty lines
# and push everything else off screen. Sheets has no maximum row height —
# pixelSize is exact — so each row's height is worked out from its message and
# capped here instead: short messages stay on one line, long ones open up to
# MAX_MESSAGE_LINES and no further.
#
# Beyond the cap the text is clipped visually only. It is all still there: the
# formula bar shows the whole message, and dragging the row's edge opens it up.
ROW_HEIGHT_BASE = 21  # padding above and below the text
ROW_HEIGHT_PER_LINE = 16  # one line of the 11pt message font
MAX_MESSAGE_LINES = 6
# Half-width characters that fit across the message column, allowing for the
# cell's padding. Full-width characters count as two.
MESSAGE_CHARS_PER_LINE = 66


def _visual_width(text: str) -> int:
    """Width in half-width units, so CJK counts double."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def estimate_lines(text: str) -> int:
    """How many lines a message takes once wrapped in the message column."""
    if not text:
        return 1
    lines = 0
    for paragraph in text.split("\n"):
        width = _visual_width(paragraph)
        lines += max(1, math.ceil(width / MESSAGE_CHARS_PER_LINE))
    return lines


def row_height_for(text: str) -> int:
    """メッセージの行の高さ（ピクセル）。上限の行数で頭打ちにする。"""
    lines = min(estimate_lines(text), MAX_MESSAGE_LINES)
    return ROW_HEIGHT_BASE + ROW_HEIGHT_PER_LINE * lines


# Height of an empty row, used for the unwritten part of the grid
DATA_ROW_HEIGHT = row_height_for("")

# Colors (RGB 0-1 float)
COLOR_HEADER_BG = {"red": 0.118, "green": 0.557, "blue": 0.243}  # #1e8e3e Green
COLOR_HEADER_FG = {"red": 1.0, "green": 1.0, "blue": 1.0}  # white
COLOR_THREAD_BG = {"red": 0.902, "green": 0.957, "blue": 0.918}  # #e6f4ea Light Green
COLOR_TS_FG = {"red": 0.55, "green": 0.55, "blue": 0.55}  # #8C8C8C gray
COLOR_LINK = {"red": 0.066, "green": 0.333, "blue": 0.8}  # #1155cc link blue
COLOR_AUTHOR_RULE = {"red": 0.75, "green": 0.75, "blue": 0.75}  # #bfbfbf

# Drive ids as they appear in an attachment link
DRIVE_ID_PATTERN = re.compile(r"/d/([A-Za-z0-9_-]{20,})|[?&]id=([A-Za-z0-9_-]{20,})")

JST = timezone(timedelta(hours=9))

# Collapsing an outline group only hides the rows; Sheets shows nothing in
# their place. The parent stays visible, so the count goes there — otherwise a
# folded thread is indistinguishable from a message that has no replies.
REPLY_COUNT_SUFFIX = "  💬{count}"
REPLY_COUNT_PATTERN = re.compile(r"\s*💬\d+$")


def strip_reply_count(text: str) -> str:
    """末尾の返信数の表示を取り除く。"""
    return REPLY_COUNT_PATTERN.sub("", text)


def with_reply_count(text: str, count: int) -> str:
    """Append the reply count to a parent message, replacing any stale one."""
    base = strip_reply_count(text)
    if count <= 0:
        return base
    return base + REPLY_COUNT_SUFFIX.format(count=count)


def column_letter(index: int) -> str:
    """1-indexed column number to its spreadsheet letter."""
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def appended_row_number(response: dict | None) -> int | None:
    """Extract the row number a write landed on from a Sheets API response.

    The response carries updatedRange like "'general'!A42:I42"; worksheet.row_count
    is the size of the grid (1000 by default), not the last populated row.
    """
    try:
        updated_range = response["updates"]["updatedRange"]
    except (TypeError, KeyError):
        return None
    first_cell = updated_range.split("!")[-1].split(":")[0]
    digits = re.sub(r"[^0-9]", "", first_cell)
    return int(digits) if digits else None


def ts_to_datetime(ts: str) -> str:
    """Slack のタイムスタンプを、日本時間の日時の文字列にする。"""
    try:
        dt = datetime.fromtimestamp(float(ts), tz=JST)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return ts


def display_text(entry: LogEntry) -> str:
    """Return the message as it appears in the cell, marker included.

    Row heights are worked out from this rather than the raw text: the
    marker adds a line to a multi-line reply, and a height measured
    without it comes up one line short.
    """
    # A reply carries no marker in the text: the green background and the
    # outline group it sits in already say what it is, and a glyph in the
    # message column is one more thing to strip when the text is reused.
    return entry.text


def build_row(entry: LogEntry) -> list[str]:
    """ログの 1 行（シートに書く値の並び）を作る。"""
    return [
        ts_to_datetime(entry.ts),
        entry.author,
        entry.text,
        "\n".join(name for name, _ in entry.attachments),
        entry.ts,
        entry.thread_ts or "",
    ]


def channel_spreadsheet_name(channel_name: str) -> str:
    """プライベートチャンネルのスプレッドシートの名前。"""
    return f"Slack Log - #{channel_name}"
