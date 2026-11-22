"""ログに記録する 1 行と、その並べ方。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Iterable


class Attachment(NamedTuple):
    """保存済みの添付ファイル。名前とリンクの組で、ログには名前がリンクとして載る。"""

    name: str
    url: str


@dataclass(frozen=True)
class LogEntry:
    """ログの 1 行になるメッセージ。"""

    # The display name is what people see in Slack; the handle is a second
    # spelling of the same person and reads as noise in a log of who said what.
    author: str
    text: str
    ts: str
    thread_ts: str | None = None
    attachments: tuple[Attachment, ...] = ()

    @property
    def is_reply(self) -> bool:
        """スレッドへの返信か。親メッセージは自分の ts を thread_ts に持つ。"""
        return bool(self.thread_ts) and self.thread_ts != self.ts

    @property
    def thread_key(self) -> str:
        """同じスレッドの行をまとめるためのキー。"""
        return self.thread_ts or self.ts


def arrange_for_log(
    entries: Iterable[LogEntry], recorded_ts: set[str]
) -> tuple[list[LogEntry], int]:
    """まだ記録していないメッセージを、書き込む順に並べる。

    返り値は (書き込む行, 重複として飛ばした件数)。並びは「親、その返信、
    次の親…」で、スレッドの中も外も投稿順になる。
    """
    entries = list(entries)

    # Deduplicate within the batch as well as against the sheet: the same
    # message can be collected twice in one pass, from the channel history
    # and from its thread.
    fresh: list[LogEntry] = []
    seen = set(recorded_ts)
    for entry in entries:
        if entry.ts in seen:
            continue
        seen.add(entry.ts)
        fresh.append(entry)
    skipped = len(entries) - len(fresh)

    threads: dict[str, list[LogEntry]] = {}
    for entry in fresh:
        threads.setdefault(entry.thread_key, []).append(entry)
    for group in threads.values():
        group.sort(key=lambda e: float(e.ts))

    ordered = [
        entry
        for _key, group in sorted(threads.items(), key=lambda item: float(item[0]))
        for entry in group
    ]
    return ordered, skipped
