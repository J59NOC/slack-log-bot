"""投稿者ごとの色。索引スプレッドシートの専用タブに割り当てを保存する。"""

from __future__ import annotations

import colorsys
import logging
from typing import TYPE_CHECKING

import gspread

from src.infrastructure.google.sheets.retry import retry

if TYPE_CHECKING:
    import threading

logger = logging.getLogger(__name__)

# Tints for the name cell, one per author. Hues skip the green band so a
# coloured name is never mistaken for the green a thread reply sits on, and
# every tint is pale enough to read black text against.
#
# Colours are handed out in order of first appearance and remembered, not
# hashed: hashing into N slots still collides by the birthday problem — with
# 20 people and 50 colours, two of them share a colour more often than not.
USER_COLOR_HUE_RANGES = [(0, 89), (161, 359)]
USER_COLOR_HUES = 25
USER_COLOR_TIERS = [(0.35, 0.92), (0.60, 0.83)]


def _build_user_palette() -> list[dict]:
    span = sum(high - low for low, high in USER_COLOR_HUE_RANGES)
    hues = []
    for i in range(USER_COLOR_HUES):
        position = span * i / USER_COLOR_HUES
        for low, high in USER_COLOR_HUE_RANGES:
            if position <= high - low:
                hues.append(low + position)
                break
            position -= high - low

    palette = []
    for saturation, lightness in USER_COLOR_TIERS:
        for hue in hues:
            red, green, blue = colorsys.hls_to_rgb(hue / 360, lightness, saturation)
            palette.append({"red": red, "green": green, "blue": blue})
    return palette


USER_COLORS = _build_user_palette()

# Tab in the index spreadsheet holding which colour each name was given, so
# the same person keeps theirs as people come and go.
COLOR_SHEET_TITLE = "🎨 表示名の色"
COLOR_SHEET_HEADER = ["表示名", "色番号"]


def next_color_index(assignments: dict[str, int], palette_size: int) -> tuple[int, bool]:
    """次に割り当てる色番号と、色が足りずに使い回したかどうか。"""
    taken = set(assignments.values())
    free = next((i for i in range(palette_size) if i not in taken), None)
    if free is not None:
        return free, False
    # More authors than colours: wrap round rather than fail, which is where a
    # duplicate can finally appear.
    return len(assignments) % palette_size, True


class AuthorColors:
    """Which colour each name was given, kept in the index spreadsheet.

    Stored rather than derived so a name keeps its colour for good, and
    shared through the spreadsheet so a second machine running the same
    workspace agrees.
    """

    def __init__(self, index_spreadsheet: gspread.Spreadsheet, lock: threading.RLock) -> None:
        """割り当てを保存する索引スプレッドシートと、ログの書き込みと共有するロックを受け取る。"""
        self._index = index_spreadsheet
        # ログの書き込みと同じロック。読み込んでから保存するまでの間に、
        # 別のスレッドが同じタブを書き換えないように。
        self._lock = lock
        self._assignments: dict[str, int] | None = None

    def _load(self) -> dict[str, int]:
        if self._assignments is not None:
            return self._assignments

        assignments: dict[str, int] = {}
        try:
            worksheet = self._index.worksheet(COLOR_SHEET_TITLE)
            for row in retry(worksheet.get_all_values)[1:]:
                if len(row) > 1 and row[0] and row[1].isdigit():
                    assignments[row[0]] = int(row[1])
        except gspread.exceptions.WorksheetNotFound:
            pass
        except Exception as e:
            logger.warning("Could not read the colour assignments: %s", e)

        self._assignments = assignments
        return assignments

    def _save(self, assignments: dict[str, int]) -> None:
        rows = [COLOR_SHEET_HEADER] + [
            [name, str(index)] for name, index in sorted(assignments.items(), key=lambda kv: kv[1])
        ]
        try:
            try:
                worksheet = self._index.worksheet(COLOR_SHEET_TITLE)
            except gspread.exceptions.WorksheetNotFound:
                worksheet = self._index.add_worksheet(
                    title=COLOR_SHEET_TITLE, rows=max(len(rows) + 20, 50), cols=2
                )
            retry(worksheet.clear, idempotent=False)
            retry(
                worksheet.update,
                rows,
                "A1",
                value_input_option="RAW",
                idempotent=False,
            )
        except Exception as e:
            logger.warning("Could not save the colour assignments: %s", e)

    def color_for(self, display_name: str) -> dict:
        """Return the colour for this author, assigning one the first time it is seen."""
        with self._lock:
            assignments = self._load()
            if display_name not in assignments:
                index, wrapped = next_color_index(assignments, len(USER_COLORS))
                if wrapped:
                    logger.warning("More than %s authors; colours now repeat", len(USER_COLORS))
                assignments[display_name] = index
                self._save(assignments)
            return USER_COLORS[assignments[display_name] % len(USER_COLORS)]

    def carry_over(self, old_name: str, new_name: str) -> None:
        """Keep an author's colour across a rename.

        The assignment is keyed by name, and a rename would otherwise read as
        a new person and take the next free one.
        """
        with self._lock:
            assignments = self._load()
            if old_name in assignments and new_name not in assignments:
                assignments[new_name] = assignments.pop(old_name)
                self._save(assignments)
