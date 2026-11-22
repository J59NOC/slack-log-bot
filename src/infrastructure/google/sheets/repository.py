"""ChannelLogRepository の Google スプレッドシート実装。"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from src.domain.log_entry import Attachment, LogEntry, arrange_for_log
from src.infrastructure.google.sheets.colors import AuthorColors
from src.infrastructure.google.sheets.formatting import (
    add_group_request,
    apply_row_heights,
    apply_thread_groups,
    attachment_cell_request,
    author_style_requests,
    collapse_group_request,
    collapse_groups,
    format_thread_rows,
    reply_count_requests,
    row_height_requests,
    sync_reply_counts,
    thread_background_requests,
)
from src.infrastructure.google.sheets.layout import (
    ATTACHMENT_COLUMN,
    DRIVE_ID_PATTERN,
    MESSAGE_COLUMN,
    REPLY_COUNT_PATTERN,
    THREAD_TS_COLUMN,
    TS_COLUMN,
    USERNAME_COLUMN,
    appended_row_number,
    build_row,
    column_letter,
    display_text,
    ts_to_datetime,
)
from src.infrastructure.google.sheets.retry import retry

if TYPE_CHECKING:
    from collections.abc import Iterable

    import gspread

    from src.domain.channel import Channel
    from src.domain.people import AccessChange
    from src.infrastructure.google.sheets.workbooks import ChannelWorkbooks

logger = logging.getLogger(__name__)


class SheetsChannelLog:
    """チャンネルのログを、スプレッドシートの行として読み書きする。"""

    @classmethod
    def create(cls, workbooks: ChannelWorkbooks) -> SheetsChannelLog:
        """索引スプレッドシートに色の割り当てを置く、新しいリポジトリを作る。"""
        # Bolt dispatches events concurrently, and backfills run on their own
        # thread. Without this, two writers can interleave their read-then-append
        # and duplicate rows or clobber each other's insert positions.
        lock = threading.RLock()
        return cls(workbooks, AuthorColors(workbooks.index_spreadsheet, lock), lock)

    def __init__(
        self, workbooks: ChannelWorkbooks, colors: AuthorColors, lock: threading.RLock
    ) -> None:
        """スプレッドシートの対応・投稿者の色・書き込みのロックを受け取る。"""
        self._books = workbooks
        self._colors = colors
        self._lock = lock
        self._existing_ts: dict[str, set[str]] = {}
        # Last author written per channel, so a run of messages from one
        # person is not ruled off in the middle when they arrive one by one.
        self._last_author: dict[str, str] = {}

    @property
    def workbooks(self) -> ChannelWorkbooks:
        """チャンネルとスプレッドシートの対応。"""
        return self._books

    def _sheet(
        self, channel: Channel, member_emails: list[str] | None
    ) -> tuple[gspread.Worksheet, gspread.Spreadsheet]:
        worksheet = self._books.worksheet_for(channel, member_emails)
        spreadsheet = self._books.spreadsheet_for(channel)
        return worksheet, spreadsheet

    def _load_existing_ts(self, channel_name: str, worksheet: gspread.Worksheet) -> set[str]:
        if channel_name in self._existing_ts:
            return self._existing_ts[channel_name]
        try:
            ts_values = retry(worksheet.col_values, TS_COLUMN)
            existing = set(ts_values[1:]) if len(ts_values) > 1 else set()
        except Exception as e:
            logger.warning("Failed to load existing TS for #%s: %s", channel_name, e)
            existing = set()
        self._existing_ts[channel_name] = existing
        return existing

    def _forget_rows(self, channel_name: str | None = None) -> None:
        if channel_name:
            self._last_author.pop(channel_name, None)
            self._existing_ts.pop(channel_name, None)
        else:
            self._last_author.clear()
            self._existing_ts.clear()

    def clear_cache(self, channel_name: str | None = None) -> None:
        """Clear in-memory caches. If channel_name is None, clear all."""
        with self._lock:
            self._books.forget(channel_name)
            self._forget_rows(channel_name)

    # ── 読む ──

    def recorded_ts(
        self,
        channel: Channel,
        member_emails: list[str] | None = None,
    ) -> set[str]:
        """Message TSs already in the sheet for this channel.

        Collectors use this to skip re-downloading and re-uploading the
        attachments of messages they already have: dedup happens at write
        time, so those uploads are discarded, but Drive keeps every copy.
        """
        with self._lock:
            worksheet = self._books.worksheet_for(channel, member_emails)
            return set(self._load_existing_ts(channel.name, worksheet))

    def url_for(self, channel: Channel) -> str | None:
        """チャンネルのログの URL。まだ無ければ None。"""
        return self._books.url_for(channel)

    def sync_access(
        self,
        channel: Channel,
        member_emails: list[str],
        revoke: list[str] | None = None,
    ) -> AccessChange:
        """ログの共有を、チャンネルのメンバーに揃える。"""
        return self._books.sync_access(channel, member_emails, revoke)

    # ── 1 件ずつ書く（リアルタイム） ──

    def insert(
        self,
        channel: Channel,
        entry: LogEntry,
        member_emails: list[str] | None = None,
    ) -> bool:
        """1 件を記録する。返信は親スレッドの末尾に入る。記録済みなら False。"""
        channel_name = channel.name
        with self._lock:
            worksheet, spreadsheet = self._sheet(channel, member_emails)

            existing = self._load_existing_ts(channel_name, worksheet)
            if entry.ts in existing:
                return False

            row = build_row(entry)

            is_thread_reply = entry.is_reply
            inserted_row = None
            first_reply = False

            if is_thread_reply:
                found = self._find_thread_insert_position(worksheet, entry.thread_ts)
                if found:
                    insert_pos, first_reply = found
                    retry(
                        worksheet.insert_row,
                        row,
                        insert_pos,
                        value_input_option="RAW",
                        idempotent=False,
                    )
                    inserted_row = insert_pos
                else:
                    # Parent isn't in the sheet (e.g. posted before the bot joined),
                    # so the reply just goes at the end.
                    resp = retry(
                        worksheet.append_row,
                        row,
                        value_input_option="RAW",
                        idempotent=False,
                    )
                    inserted_row = appended_row_number(resp)
                    first_reply = True
            else:
                resp = retry(
                    worksheet.append_row,
                    row,
                    value_input_option="RAW",
                    idempotent=False,
                )
                inserted_row = appended_row_number(resp)

            # Height for this row, plus the reply background when it is one.
            # Bundled into a single batch so a message costs one extra call.
            if inserted_row:
                requests = row_height_requests(worksheet.id, inserted_row, [entry.text])
                requests.extend(
                    author_style_requests(
                        worksheet.id,
                        inserted_row,
                        [entry.author],
                        self._last_author.get(channel_name),
                        self._colors.color_for,
                    )
                )
                self._last_author[channel_name] = entry.author
                if entry.attachments:
                    requests.append(
                        attachment_cell_request(worksheet.id, inserted_row, entry.attachments)
                    )
                if is_thread_reply:
                    requests.extend(thread_background_requests(worksheet.id, inserted_row))
                    # Adding a group over the row merges it into the
                    # thread's existing group rather than making a second one.
                    requests.append(add_group_request(worksheet.id, inserted_row, inserted_row + 1))
                try:
                    retry(spreadsheet.batch_update, {"requests": requests})
                except Exception as e:
                    logger.warning("Failed to style row %s: %s", inserted_row, e)

                # Only the thread's first reply needs folding. A later one
                # merges into the group already there and keeps its state,
                # including a fold the reader opened by hand.
                if is_thread_reply and first_reply:
                    collapse_groups(spreadsheet, worksheet.id, [(inserted_row, inserted_row + 1)])

            existing.add(entry.ts)
            if is_thread_reply:
                sync_reply_counts(worksheet, spreadsheet)

        logger.info("Logged: #%s %s (%s)", channel_name, entry.author, ts_to_datetime(entry.ts))
        return True

    def _find_thread_insert_position(
        self, worksheet: gspread.Worksheet, thread_ts: str
    ) -> tuple[int, bool] | None:
        """Where this reply goes, and whether it is the thread's first.

        The first reply is the one that creates the thread's group; the ones
        after it merge into that group and inherit its folded state.
        """
        try:
            all_ts = retry(worksheet.col_values, TS_COLUMN)
            all_thread_ts = retry(worksheet.col_values, THREAD_TS_COLUMN)
        except Exception:
            return None

        max_len = max(len(all_ts), len(all_thread_ts))
        all_ts += [""] * (max_len - len(all_ts))
        all_thread_ts += [""] * (max_len - len(all_thread_ts))

        last_match_row = None
        last_was_parent = False
        for i in range(1, max_len):
            if all_ts[i] == thread_ts or all_thread_ts[i] == thread_ts:
                last_match_row = i + 1
                # The parent carries the thread_ts as its own ts; a reply does
                # not. Landing on the parent means no reply is there yet.
                last_was_parent = all_ts[i] == thread_ts

        if last_match_row:
            return last_match_row + 1, last_was_parent
        return None

    def link_attachments(
        self,
        channel: Channel,
        ts: str,
        attachments: list[Attachment],
        member_emails: list[str] | None = None,
    ) -> None:
        """Fill in the attachment cell once the background uploads finish."""
        if not attachments:
            return
        try:
            with self._lock:
                worksheet = self._books.worksheet_for(channel, member_emails)
                ts_values = retry(worksheet.col_values, TS_COLUMN)
                spreadsheet = self._books.spreadsheet_for(channel)
                for i, val in enumerate(ts_values):
                    if val == ts:
                        row_num = i + 1  # 1-indexed
                        retry(
                            spreadsheet.batch_update,
                            {
                                "requests": [
                                    attachment_cell_request(worksheet.id, row_num, attachments)
                                ]
                            },
                        )
                        logger.info("Updated attachments for ts=%s in #%s", ts, channel.name)
                        return
            logger.warning(
                "No row found for ts=%s in #%s, attachments not linked", ts, channel.name
            )
        except Exception as e:
            logger.error("Failed to update attachment links: %s", e)

    # ── まとめて書く（週次収集・バックフィル） ──

    def append_new(
        self,
        channel: Channel,
        entries: Iterable[LogEntry],
        member_emails: list[str] | None = None,
    ) -> tuple[int, int]:
        """未記録のものだけをスレッド順に追記する。(追記数, 重複数) を返す。"""
        with self._lock:
            return self._append_new_locked(channel, entries, member_emails)

    def _append_new_locked(
        self,
        channel: Channel,
        entries: Iterable[LogEntry],
        member_emails: list[str] | None,
    ) -> tuple[int, int]:
        channel_name = channel.name
        worksheet, spreadsheet = self._sheet(channel, member_emails)
        existing = self._load_existing_ts(channel_name, worksheet)

        # Build rows (ordered: parent, reply, reply, ..., next parent, ...)
        ordered, skip_count = arrange_for_log(entries, existing)
        if not ordered:
            return (0, skip_count)

        rows: list[list[str]] = []
        for entry in ordered:
            rows.append(build_row(entry))
            existing.add(entry.ts)

        # Batch append; the response tells us exactly where the rows landed
        resp = retry(
            worksheet.append_rows,
            rows,
            value_input_option="RAW",
            idempotent=False,
        )
        start_row = appended_row_number(resp)

        # Row heights and thread reply backgrounds
        if start_row:
            apply_row_heights(
                worksheet,
                spreadsheet,
                start_row,
                [display_text(e) for e in ordered],
            )
            attachment_requests = [
                attachment_cell_request(worksheet.id, start_row + i, entry.attachments)
                for i, entry in enumerate(ordered)
                if entry.attachments
            ]
            if attachment_requests:
                try:
                    retry(spreadsheet.batch_update, {"requests": attachment_requests})
                except Exception as e:
                    logger.warning("Failed to link attachment names: %s", e)
            format_thread_rows(worksheet, spreadsheet, start_row, [e.thread_ts for e in ordered])
            apply_thread_groups(worksheet, spreadsheet, start_row, ordered)
            self._apply_author_styles(
                channel_name, worksheet, spreadsheet, start_row, [e.author for e in ordered]
            )
            sync_reply_counts(worksheet, spreadsheet)
        else:
            logger.warning(
                "Could not determine written row range for #%s; skipping thread row colouring",
                channel_name,
            )

        new_count = len(rows)
        logger.info("Wrote %s messages (grouped) to #%s", new_count, channel_name)
        return (new_count, skip_count)

    def _apply_author_styles(
        self,
        channel_name: str,
        worksheet: gspread.Worksheet,
        spreadsheet: gspread.Spreadsheet,
        start_row: int,
        authors: list[str],
    ) -> None:
        """書き込んだ行の投稿者に色を付け、直前の投稿者として最後の 1 人を覚える。"""
        requests = author_style_requests(
            worksheet.id,
            start_row,
            authors,
            self._last_author.get(channel_name),
            self._colors.color_for,
        )
        if requests:
            try:
                retry(spreadsheet.batch_update, {"requests": requests})
            except Exception as e:
                logger.warning("Failed to style authors: %s", e)
        self._last_author[channel_name] = authors[-1]

    # ── 編集・削除 ──

    def update_text(
        self,
        channel: Channel,
        ts: str,
        text: str,
        member_emails: list[str] | None = None,
    ) -> bool:
        """Replace a recorded message's text after it was edited in Slack."""
        with self._lock:
            worksheet, spreadsheet = self._sheet(channel, member_emails)
            try:
                stamps = retry(worksheet.col_values, TS_COLUMN)
            except Exception as e:
                logger.warning("Could not read timestamps in #%s: %s", channel.name, e)
                return False

            row_number = next((i + 1 for i, value in enumerate(stamps) if i and value == ts), None)
            if row_number is None:
                return False

            # A parent keeps its reply count; that suffix belongs to the sheet,
            # not to the message, and the edit did not remove the replies.
            try:
                current = retry(worksheet.cell, row_number, MESSAGE_COLUMN).value or ""
            except Exception:
                current = ""
            match = REPLY_COUNT_PATTERN.search(current)
            wanted = text + (match.group(0) if match else "")

            try:
                retry(
                    worksheet.update_cell,
                    row_number,
                    MESSAGE_COLUMN,
                    wanted,
                    idempotent=False,
                )
                retry(
                    spreadsheet.batch_update,
                    {"requests": row_height_requests(worksheet.id, row_number, [wanted])},
                )
            except Exception as e:
                logger.warning("Could not update the edited message: %s", e)
                return False
            logger.info("Updated an edited message in #%s (ts=%s)", channel.name, ts)
            return True

    def remove(
        self,
        channel: Channel,
        ts: str,
        member_emails: list[str] | None = None,
    ) -> list[str] | None:
        """Take a message out of the log after it was deleted in Slack.

        The row goes rather than being struck through. A message is usually
        deleted because it should not have been posted, and a copy of it that
        everyone in the channel can still read in a spreadsheet is the thing
        the person was trying to undo.

        Returns the Drive ids the row's attachment cell pointed at, so they can
        go with it; None if there was no row to remove.
        """
        with self._lock:
            worksheet, spreadsheet = self._sheet(channel, member_emails)
            try:
                stamps = retry(worksheet.col_values, TS_COLUMN)
            except Exception as e:
                logger.warning("Could not read timestamps in #%s: %s", channel.name, e)
                return None

            row_number = next((i + 1 for i, value in enumerate(stamps) if i and value == ts), None)
            if row_number is None:
                return None

            file_ids = self._linked_file_ids(spreadsheet, worksheet, row_number)

            try:
                retry(worksheet.delete_rows, row_number, idempotent=False)
            except Exception as e:
                logger.warning("Could not remove the deleted message's row: %s", e)
                return None

            self._existing_ts.get(channel.name, set()).discard(ts)
            logger.info("Removed a deleted message from #%s (ts=%s)", channel.name, ts)

            self._repair_after_removal(worksheet, spreadsheet, row_number)
            return file_ids

    def _linked_file_ids(
        self, spreadsheet: gspread.Spreadsheet, worksheet: gspread.Worksheet, row_number: int
    ) -> list[str]:
        """Drive ids that one row's attachment cell points at.

        Both spellings are read. Rows written before file names became links
        hold the URL as text; newer ones hold it only in the cell's rich text,
        where col_values cannot see it at all.

        Returns [] on any failure, which leaves the files alone. An attachment
        left behind is untidy; one thrown away because a read came back short
        is not recoverable from the sheet.
        """
        cell = f"{column_letter(ATTACHMENT_COLUMN)}{row_number}"
        try:
            response = retry(
                self._books.gc.http_client.request,
                "get",
                f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet.id}",
                params={
                    "ranges": f"'{worksheet.title}'!{cell}",
                    "fields": (
                        "sheets/data/rowData/values("
                        "formattedValue,"
                        "textFormatRuns/format/link/uri,"
                        "userEnteredFormat/textFormat/link/uri)"
                    ),
                },
            ).json()
            values = response["sheets"][0]["data"][0].get("rowData", [{}])[0].get("values", [])
        except Exception as e:
            logger.warning("Could not read the attachments on row %s: %s", row_number, e)
            return []
        if not values:
            return []

        value = values[0]
        uris = [
            run.get("format", {}).get("link", {}).get("uri", "")
            for run in value.get("textFormatRuns", [])
        ]
        # Sheets folds a run spanning the whole string into the cell's own
        # format and then reports no runs, so a single attachment lands here.
        uris.append(
            value.get("userEnteredFormat", {}).get("textFormat", {}).get("link", {}).get("uri", "")
        )
        uris.append(value.get("formattedValue", ""))

        ids = []
        for uri in uris:
            for match in DRIVE_ID_PATTERN.finditer(uri or ""):
                file_id = match.group(1) or match.group(2)
                if file_id not in ids:
                    ids.append(file_id)
        return ids

    def _half_open_group_requests(
        self, worksheet: gspread.Worksheet, spreadsheet: gspread.Spreadsheet
    ) -> list[dict]:
        """Re-fold a thread that a row removal left half open.

        Sheets keeps a group's range in step as rows are deleted, but a group
        worn down to a single row comes back reporting itself expanded while
        its row stays hidden: no +/- to press, and a reply that is simply
        missing. Only groups in that contradictory state are touched, so a
        thread someone opened by hand stays open.
        """
        try:
            response = retry(
                self._books.gc.http_client.request,
                "get",
                f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet.id}",
                params={
                    "ranges": f"'{worksheet.title}'",
                    "fields": "sheets(properties.sheetId,rowGroups,data.rowMetadata.hiddenByUser)",
                },
            ).json()
            sheet = next(
                s for s in response["sheets"] if s["properties"]["sheetId"] == worksheet.id
            )
        except Exception as e:
            logger.warning("Could not check the thread groups: %s", e)
            return []

        hidden = {
            index
            for index, meta in enumerate(sheet.get("data", [{}])[0].get("rowMetadata", []))
            if meta.get("hiddenByUser")
        }
        requests = []
        for group in sheet.get("rowGroups", []):
            if group.get("collapsed"):
                continue
            span = group["range"]
            rows = range(span["startIndex"], span["endIndex"])
            if any(row in hidden for row in rows):
                requests.append(
                    collapse_group_request(
                        worksheet.id, span["startIndex"] + 1, span["endIndex"] + 1
                    )
                )
        return requests

    def _repair_after_removal(
        self,
        worksheet: gspread.Worksheet,
        spreadsheet: gspread.Spreadsheet,
        row_number: int,
    ) -> None:
        """Put right what the removed row was carrying.

        Three things outlive it. A parent's reply count is one more than the
        thread now has, the rule that marks a change of speaker was drawn
        against a neighbour that is no longer there, and a thread worn down
        to one reply comes back half folded.
        """
        # Cosmetic, and it runs after the row is already gone, so nothing here
        # is allowed to raise into the caller and report a failed deletion.
        try:
            rows = retry(worksheet.get_all_values)[1:]
            requests = reply_count_requests(worksheet.id, rows)

            def author_at(index: int) -> str:
                row = rows[index]
                return row[USERNAME_COLUMN - 1] if len(row) >= USERNAME_COLUMN else ""

            # The row below moved up, so whether it opens a new speaker changed.
            moved_up = row_number - 2  # 0-indexed into rows, header dropped
            if 0 <= moved_up < len(rows):
                author = author_at(moved_up)
                previous = author_at(moved_up - 1) if moved_up else None
                requests.extend(
                    author_style_requests(
                        worksheet.id,
                        row_number,
                        [author],
                        previous,
                        self._colors.color_for,
                    )
                )

            if requests:
                retry(spreadsheet.batch_update, {"requests": requests})

            # Its own batch: updateDimensionGroup insists on an exact range
            # match, and a miss would take the rest of the tidy-up with it.
            folds = self._half_open_group_requests(worksheet, spreadsheet)
            if folds:
                retry(spreadsheet.batch_update, {"requests": folds})
                logger.info("Re-folded %s thread(s) after the removal", len(folds))
        except Exception as e:
            logger.warning("Could not tidy up after the removed row: %s", e)

    # ── 改名 ──

    def rename_channel(self, channel: Channel, previous_name: str = "") -> bool:
        """Follow a channel rename so its history stays in one spreadsheet."""
        with self._lock:
            moved = self._books.rename(channel, previous_name)
            if moved:
                # Caches are keyed by channel name, so the old key must go.
                self._forget_rows()
            return moved

    def rename_author(self, old_name: str, new_name: str) -> int:
        """Rewrite an author's name across every channel sheet.

        Slack shows a renamed person's current name on their old messages, so
        the log follows suit rather than leaving the sheet a mix of both. Rows
        carry no user id, so they are matched on the name they were written
        with — the cached one, before it changed.
        """
        if not old_name or not new_name or old_name == new_name:
            return 0

        with self._lock:
            self._colors.carry_over(old_name, new_name)

            updated = 0
            for spreadsheet, worksheet in self._books.each_channel_sheet():
                try:
                    names = retry(worksheet.col_values, USERNAME_COLUMN)
                except Exception as e:
                    logger.warning("Could not read names in %s: %s", spreadsheet.title, e)
                    continue
                rows = [i + 1 for i, name in enumerate(names) if i and name == old_name]
                if not rows:
                    continue
                try:
                    retry(
                        worksheet.batch_update,
                        [
                            {
                                "range": f"{column_letter(USERNAME_COLUMN)}{r}",
                                "values": [[new_name]],
                            }
                            for r in rows
                        ],
                        value_input_option="RAW",
                        idempotent=False,
                    )
                    updated += len(rows)
                except Exception as e:
                    logger.warning("Could not rename in %s: %s", spreadsheet.title, e)
            if updated:
                logger.info("Renamed %s -> %s in %s row(s)", old_name, new_name, updated)
            self._last_author.clear()
            return updated
