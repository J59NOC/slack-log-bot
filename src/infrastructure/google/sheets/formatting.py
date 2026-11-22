"""ログのシートの書式。batch_update に渡すリクエストの組み立てと適用。

リクエストを作る関数は API を呼ばない（純粋関数）。apply_* / sync_* / collapse_*
が実際に送る。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.infrastructure.google.sheets.layout import (
    ATTACHMENT_COLUMN,
    COLOR_AUTHOR_RULE,
    COLOR_HEADER_BG,
    COLOR_HEADER_FG,
    COLOR_LINK,
    COLOR_THREAD_BG,
    COLOR_TS_FG,
    COLUMN_WIDTHS,
    DATA_ROW_HEIGHT,
    HEADER_ROW,
    MESSAGE_COLUMN,
    THREAD_TS_COLUMN,
    TS_COLUMN,
    USERNAME_COLUMN,
    row_height_for,
    with_reply_count,
)
from src.infrastructure.google.sheets.retry import retry

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    import gspread

    from src.domain.log_entry import Attachment, LogEntry

logger = logging.getLogger(__name__)


# ── シート全体 ──


def sheet_format_requests(sheet_id: int, row_count: int, *, new_sheet: bool = True) -> list[dict]:
    """Visual formatting for a log worksheet (applied once on creation).

    new_sheet が False（記録済みの行があるシート）のときは、データ行の高さを
    揃えない。行ごとの高さはメッセージの長さに合わせて付けてあり、揃えると
    複数行のメッセージがすべて 1 行に切り詰められる。
    """
    requests = []

    # 1. Freeze header row
    requests.append(
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 1},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        }
    )

    # 2. Column widths
    for i, width in enumerate(COLUMN_WIDTHS):
        requests.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": i,
                        "endIndex": i + 1,
                    },
                    "properties": {"pixelSize": width},
                    "fields": "pixelSize",
                }
            }
        )

    # 3. Header row: background color + white bold text + center aligned
    requests.append(
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": COLOR_HEADER_BG,
                        "textFormat": {
                            "bold": True,
                            "foregroundColor": COLOR_HEADER_FG,
                            "fontSize": 10,
                        },
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE",
                    }
                },
                "fields": (
                    "userEnteredFormat(backgroundColor,textFormat,"
                    "horizontalAlignment,verticalAlignment)"
                ),
            }
        }
    )

    # 4. Header row height
    requests.append(
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": 0,
                    "endIndex": 1,
                },
                "properties": {"pixelSize": 36},
                "fields": "pixelSize",
            }
        }
    )

    # 5. Text wrapping + larger font on message column (E)
    requests.append(
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "startColumnIndex": MESSAGE_COLUMN - 1,
                    "endColumnIndex": MESSAGE_COLUMN,
                },
                "cell": {
                    "userEnteredFormat": {
                        "wrapStrategy": "WRAP",
                        "verticalAlignment": "TOP",
                        "textFormat": {"fontSize": 11},
                    }
                },
                "fields": "userEnteredFormat(wrapStrategy,verticalAlignment,textFormat.fontSize)",
            }
        }
    )

    # 6. TS columns (H, I): gray smaller font
    requests.append(
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "startColumnIndex": TS_COLUMN - 1,
                    "endColumnIndex": THREAD_TS_COLUMN,
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {
                            "foregroundColor": COLOR_TS_FG,
                            "fontSize": 8,
                        },
                    }
                },
                "fields": "userEnteredFormat.textFormat",
            }
        }
    )

    # 7. Default vertical alignment for all data cells
    requests.append(
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": MESSAGE_COLUMN - 1,
                },
                "cell": {"userEnteredFormat": {"verticalAlignment": "MIDDLE"}},
                "fields": "userEnteredFormat.verticalAlignment",
            }
        }
    )

    # 8. Fixed height for data rows
    if new_sheet:
        requests.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": 1,
                        "endIndex": max(row_count, 2),
                    },
                    "properties": {"pixelSize": DATA_ROW_HEIGHT},
                    "fields": "pixelSize",
                }
            }
        )

    # 9. Set basic filter (auto-filter) on header
    requests.append(
        {
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "startColumnIndex": 0,
                        "endColumnIndex": len(HEADER_ROW),
                    }
                }
            }
        }
    )
    return requests


def apply_sheet_format(
    worksheet: gspread.Worksheet, spreadsheet: gspread.Spreadsheet, *, new_sheet: bool = True
) -> None:
    """Apply visual formatting to a worksheet (called once on creation).

    プロセスを起動し直すたびに既存のシートにもかけ直すので、そのときは
    new_sheet=False で、行ごとの高さを残す。
    """
    requests = sheet_format_requests(worksheet.id, worksheet.row_count, new_sheet=new_sheet)
    try:
        retry(spreadsheet.batch_update, {"requests": requests})
        logger.info("Applied formatting to sheet: %s", worksheet.title)
    except Exception as e:
        logger.warning("Failed to apply formatting: %s", e)


# ── 行の高さ ──


def row_height_request(sheet_id: int, first_row: int, after_row: int, height: int) -> dict:
    """続いた行の高さを 1 つの値に揃えるリクエスト（行番号は 1 始まり）。"""
    return {
        "updateDimensionProperties": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "ROWS",
                "startIndex": first_row - 1,  # 1-indexed row -> 0-indexed
                "endIndex": after_row - 1,
            },
            "properties": {"pixelSize": height},
            "fields": "pixelSize",
        }
    }


def row_height_requests(sheet_id: int, start_row: int, texts: list[str]) -> list[dict]:
    """Per-row heights, with runs of the same height merged into one request."""
    requests = []
    run_height = None
    run_start = start_row

    for offset, text in enumerate(texts):
        height = row_height_for(text)
        if height != run_height:
            if run_height is not None:
                requests.append(
                    row_height_request(sheet_id, run_start, start_row + offset, run_height)
                )
            run_height, run_start = height, start_row + offset

    if run_height is not None:
        requests.append(row_height_request(sheet_id, run_start, start_row + len(texts), run_height))
    return requests


def apply_row_heights(
    worksheet: gspread.Worksheet,
    spreadsheet: gspread.Spreadsheet,
    start_row: int,
    texts: list[str],
) -> None:
    """行の高さを、メッセージの長さに合わせて設定する。"""
    requests = row_height_requests(worksheet.id, start_row, texts)
    if not requests:
        return
    try:
        retry(spreadsheet.batch_update, {"requests": requests})
    except Exception as e:
        logger.warning("Failed to set row heights: %s", e)


# ── 返信数 ──


def reply_count_requests(sheet_id: int, rows: list[list[str]]) -> list[dict]:
    """Refresh the reply count shown on each parent message.

    Counts come from the sheet rather than the batch being written: a
    thread gains replies over time, and the parent may have been written
    long before them.
    """
    counts: dict[str, int] = {}
    for row in rows:
        thread_ts = row[THREAD_TS_COLUMN - 1] if len(row) >= THREAD_TS_COLUMN else ""
        ts = row[TS_COLUMN - 1] if len(row) >= TS_COLUMN else ""
        if thread_ts and thread_ts != ts:
            counts[thread_ts] = counts.get(thread_ts, 0) + 1

    requests = []
    for offset, row in enumerate(rows):
        ts = row[TS_COLUMN - 1] if len(row) >= TS_COLUMN else ""
        thread_ts = row[THREAD_TS_COLUMN - 1] if len(row) >= THREAD_TS_COLUMN else ""
        if not ts or (thread_ts and thread_ts != ts):
            continue
        current = row[MESSAGE_COLUMN - 1] if len(row) >= MESSAGE_COLUMN else ""
        wanted = with_reply_count(current, counts.get(ts, 0))
        if wanted == current:
            continue
        requests.append(
            {
                "updateCells": {
                    "rows": [{"values": [{"userEnteredValue": {"stringValue": wanted}}]}],
                    "fields": "userEnteredValue",
                    "start": {
                        "sheetId": sheet_id,
                        "rowIndex": offset + 1,  # +1 for the header
                        "columnIndex": MESSAGE_COLUMN - 1,
                    },
                }
            }
        )
    return requests


def sync_reply_counts(worksheet: gspread.Worksheet, spreadsheet: gspread.Spreadsheet) -> None:
    """親メッセージに付けている返信数を、シートの今の状態に合わせて直す。"""
    try:
        rows = retry(worksheet.get_all_values)[1:]
        requests = reply_count_requests(worksheet.id, rows)
        if requests:
            retry(spreadsheet.batch_update, {"requests": requests})
    except Exception as e:
        logger.warning("Failed to refresh reply counts: %s", e)


# ── 投稿者 ──


def author_style_requests(
    sheet_id: int,
    start_row: int,
    authors: list[str],
    previous_author: str | None,
    color_for: Callable[[str], dict],
) -> list[dict]:
    """Colour each author's name cell, and rule off where the author changes.

    The colour goes on the name cell alone: the row's own background is
    already carrying whether the message is a thread reply.
    """
    requests = []

    run_author = None
    run_start = start_row
    for offset, author in enumerate([*authors, None]):
        if author != run_author:
            if run_author is not None:
                requests.append(
                    {
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": run_start - 1,
                                "endRowIndex": start_row + offset - 1,
                                "startColumnIndex": USERNAME_COLUMN - 1,
                                "endColumnIndex": USERNAME_COLUMN,
                            },
                            "cell": {
                                "userEnteredFormat": {"backgroundColor": color_for(run_author)}
                            },
                            "fields": "userEnteredFormat.backgroundColor",
                        }
                    }
                )
            run_author, run_start = author, start_row + offset

    # A line above the first message of each new author, so a run of
    # messages from one person reads as one block.
    preceding = previous_author
    for offset, author in enumerate(authors):
        if preceding is not None and author != preceding:
            requests.append(
                {
                    "updateBorders": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": start_row + offset - 1,
                            "endRowIndex": start_row + offset,
                            "startColumnIndex": 0,
                            "endColumnIndex": len(HEADER_ROW),
                        },
                        "top": {"style": "SOLID", "color": COLOR_AUTHOR_RULE},
                    }
                }
            )
        preceding = author
    return requests


# ── スレッド ──


def thread_runs(start_row: int, entries: Sequence[LogEntry]) -> list[tuple[int, int]]:
    """(first_row, after_row) for each run of consecutive reply rows.

    A run covers only the replies, never the parent: collapsing hides the
    replies and leaves the message they answer in view. Runs are built from
    consecutive reply rows, which is how a thread is written — parent
    first, then its replies.
    """
    runs = []
    run_start = None
    for offset, entry in enumerate(entries):
        if entry.is_reply:
            if run_start is None:
                run_start = start_row + offset
        elif run_start is not None:
            runs.append((run_start, start_row + offset))
            run_start = None
    if run_start is not None:
        runs.append((run_start, start_row + len(entries)))
    return runs


def group_span(sheet_id: int, first_row: int, after_row: int) -> dict:
    """行のグループの範囲。1 始まりの行番号を、API の 0 始まりの形にする。"""
    return {
        "sheetId": sheet_id,
        "dimension": "ROWS",
        "startIndex": first_row - 1,  # 1-indexed row -> 0-indexed
        "endIndex": after_row - 1,
    }


def add_group_request(sheet_id: int, first_row: int, after_row: int) -> dict:
    """行のグループ（スレッドを折りたたむ単位）を作るリクエスト。"""
    return {"addDimensionGroup": {"range": group_span(sheet_id, first_row, after_row)}}


def collapse_group_request(sheet_id: int, first_row: int, after_row: int) -> dict:
    """行のグループを折りたたむリクエスト。"""
    return {
        "updateDimensionGroup": {
            "dimensionGroup": {
                "range": group_span(sheet_id, first_row, after_row),
                "depth": 1,
                "collapsed": True,
            },
            "fields": "collapsed",
        }
    }


def thread_group_requests(sheet_id: int, start_row: int, entries: Sequence[LogEntry]) -> list[dict]:
    """Outline groups over each thread's replies, so a thread can be folded."""
    return [
        add_group_request(sheet_id, first, after)
        for first, after in thread_runs(start_row, entries)
    ]


def collapse_groups(
    spreadsheet: gspread.Spreadsheet,
    sheet_id: int,
    runs: list[tuple[int, int]],
) -> None:
    """Fold thread groups, in a batch of their own.

    Never in the batch that creates them. addDimensionGroup silently
    merges a new group into an adjacent one, while updateDimensionGroup
    insists on an exact range match — so the collapse can name a range
    that no longer exists. batch_update is atomic, and sharing a batch
    would take the group creation and the row formatting down with it.

    A range that fails to match is a group that got merged, and a merge
    inherits the collapsed state of what it merged into, so there is
    nothing left to do for it.
    """
    if not runs:
        return
    requests = [collapse_group_request(sheet_id, a, b) for a, b in runs]
    try:
        retry(spreadsheet.batch_update, {"requests": requests})
    except Exception as e:
        logger.debug("Collapsing thread groups together failed: %s", e)
    else:
        return
    # One bad range rejects the whole batch, so retry them individually
    # rather than leaving every other thread on this sheet open.
    for request in requests:
        _collapse_one(spreadsheet, request)


def _collapse_one(spreadsheet: gspread.Spreadsheet, request: dict) -> None:
    try:
        retry(spreadsheet.batch_update, {"requests": [request]})
    except Exception as e:
        logger.debug("Thread group left open: %s", e)


def apply_thread_groups(
    worksheet: gspread.Worksheet,
    spreadsheet: gspread.Spreadsheet,
    start_row: int,
    entries: Sequence[LogEntry],
) -> None:
    """書き込んだ行のスレッドをグループにして、折りたたむ。"""
    runs = thread_runs(start_row, entries)
    if not runs:
        return
    try:
        retry(
            spreadsheet.batch_update,
            {"requests": [add_group_request(worksheet.id, a, b) for a, b in runs]},
        )
    except Exception as e:
        logger.warning("Failed to group thread replies: %s", e)
        return
    collapse_groups(spreadsheet, worksheet.id, runs)


def thread_background_requests(sheet_id: int, row_number: int) -> list[dict]:
    """Thread reply background for one row, either side of the name cell.

    The name cell is left out rather than painted and repainted: it carries
    the author's colour, and whichever of the two was written last used to
    win. Skipping the column makes the order stop mattering.
    """
    spans = [(0, USERNAME_COLUMN - 1), (USERNAME_COLUMN, len(HEADER_ROW))]
    return [
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": row_number - 1,  # 0-indexed
                    "endRowIndex": row_number,
                    "startColumnIndex": first,
                    "endColumnIndex": last,
                },
                "cell": {"userEnteredFormat": {"backgroundColor": COLOR_THREAD_BG}},
                "fields": "userEnteredFormat.backgroundColor",
            }
        }
        for first, last in spans
        if last > first
    ]


def format_thread_rows(
    worksheet: gspread.Worksheet,
    spreadsheet: gspread.Spreadsheet,
    start_row: int,
    thread_stamps: Iterable[str | None],
) -> None:
    """Apply background color to rows that carry a thread TS, after a batch write.

    thread_stamps は書き込んだ行ごとのスレッド TS（無ければ空）。
    """
    requests = []
    for i, thread_ts in enumerate(thread_stamps):
        if thread_ts:
            requests.extend(thread_background_requests(worksheet.id, start_row + i))

    if requests:
        try:
            retry(spreadsheet.batch_update, {"requests": requests})
        except Exception as e:
            logger.warning("Failed to format thread rows: %s", e)


# ── 添付ファイル ──


def attachment_cell_request(
    sheet_id: int, row_number: int, attachments: Iterable[Attachment]
) -> dict:
    """Write the attachment cell as linked file names rather than raw URLs.

    A Drive URL is 60-odd unreadable characters; the file name says what it
    is. Rich text is used instead of a HYPERLINK formula because a formula
    yields one link per cell, and a message can carry several files.

    With a single attachment Sheets folds the one run, which spans the
    whole string, into the cell's own textFormat and reports no runs at
    all. The link works either way — worth knowing before concluding from
    an empty textFormatRuns that the link failed to apply.
    """
    attachments = list(attachments)
    text = "\n".join(name for name, _ in attachments)
    runs = []
    position = 0
    for name, url in attachments:
        runs.append(
            {
                "startIndex": position,
                "format": {
                    "link": {"uri": url},
                    "underline": True,
                    "foregroundColor": COLOR_LINK,
                },
            }
        )
        position += len(name)
        # The separating newline carries no link, so the run has to end.
        runs.append({"startIndex": position, "format": {}})
        position += 1

    return {
        "updateCells": {
            "rows": [
                {
                    "values": [
                        {
                            "userEnteredValue": {"stringValue": text},
                            "textFormatRuns": runs[:-1],  # drop the trailing reset
                        }
                    ]
                }
            ],
            "fields": "userEnteredValue,textFormatRuns",
            "start": {
                "sheetId": sheet_id,
                "rowIndex": row_number - 1,
                "columnIndex": ATTACHMENT_COLUMN - 1,
            },
        }
    }
