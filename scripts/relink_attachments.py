"""Turn the raw Drive URLs already in the attachment column into linked names.

Rows written before the attachment cell held rich text show a 60-character
Drive URL where a file name would do. The names are fetched from Drive by id,
so nothing is guessed.

A file that has been deleted from Drive keeps its URL: without a name there is
nothing to put in its place, and dropping the link would lose the only record
that the message had an attachment.

Usage:
    python -m scripts.relink_attachments            # 対象を表示するだけ
    python -m scripts.relink_attachments --apply    # 実際に書き換える
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import gspread

# `python scripts/relink_attachments.py` と直接起動されたときも src を import できるように
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.domain.log_entry import Attachment
from src.infrastructure.google.auth import load_credentials
from src.infrastructure.google.drive import drive_service, shared_drive_id
from src.infrastructure.google.sheets.formatting import attachment_cell_request
from src.infrastructure.google.sheets.layout import ATTACHMENT_COLUMN, HEADER_ROW
from src.infrastructure.google.sheets.retry import retry as _retry
from src.infrastructure.google.sheets.workbooks import log_spreadsheet_files
from src.infrastructure.settings import load_settings

if TYPE_CHECKING:
    from src.infrastructure.google.drive import DriveService

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DRIVE_ID_PATTERN = re.compile(r"/d/([A-Za-z0-9_-]{20,})|[?&]id=([A-Za-z0-9_-]{20,})")
PAUSE_BETWEEN_SHEETS = 1.5


def file_ids_in(cell: str) -> list[str]:
    """セルの文字列に含まれる Drive のファイル ID。"""
    return [m.group(1) or m.group(2) for m in DRIVE_ID_PATTERN.finditer(cell)]


def _name_of(drive: DriveService, file_id: str) -> str | None:
    try:
        return drive.files().get(fileId=file_id, fields="name").execute()["name"]
    except Exception as e:
        logger.warning("  ! %s: 名前を取得できません (%s)", file_id, str(e)[:60])
        return None


def lookup_names(drive: DriveService, file_ids: set[str]) -> dict[str, str]:
    """Map Drive file ids to their names, skipping any that no longer exist."""
    names = {file_id: _name_of(drive, file_id) for file_id in file_ids}
    return {file_id: name for file_id, name in names.items() if name is not None}


def pending_rows(values: list[list[str]]) -> list[tuple[int, list[str]]]:
    """添付セルにまだ URL が入っている行（行番号と、その中のファイル ID）。"""
    pending = []
    for row_number, row in enumerate(values[1:], start=2):
        cell = row[ATTACHMENT_COLUMN - 1] if len(row) >= ATTACHMENT_COLUMN else ""
        if "http" not in cell:
            continue
        ids = file_ids_in(cell)
        if ids:
            pending.append((row_number, ids))
    return pending


def relink_requests(
    worksheet_id: int, pending: list[tuple[int, list[str]]], names: dict[str, str]
) -> list[dict]:
    """添付セルを、ファイル名のリンクに書き換えるリクエスト。"""
    requests = []
    for row_number, ids in pending:
        attachments = [
            Attachment(names[i], f"https://drive.google.com/file/d/{i}/view")
            for i in ids
            if i in names
        ]
        if attachments:
            requests.append(attachment_cell_request(worksheet_id, row_number, attachments))
    return requests


def _process_worksheet(
    drive: DriveService,
    spreadsheet: gspread.Spreadsheet,
    worksheet: gspread.Worksheet,
    *,
    file_name: str,
    apply: bool,
) -> int:
    """1 つのタブを調べ（apply なら書き換え）、対象の行数を返す。"""
    if worksheet.col_count < len(HEADER_ROW):
        return 0
    values = _retry(worksheet.get_all_values)
    if not values or values[0][: len(HEADER_ROW)] != HEADER_ROW:
        return 0

    # Only rows whose attachment cell still holds a URL need touching.
    pending = pending_rows(values)
    if not pending:
        return 0

    all_ids = {i for _, ids in pending for i in ids}
    logger.info(
        "\n%s / %s: %s 行 / %s ファイル",
        file_name,
        worksheet.title,
        len(pending),
        len(all_ids),
    )
    if not apply:
        return len(pending)

    requests = relink_requests(worksheet.id, pending, lookup_names(drive, all_ids))
    if not requests:
        return 0
    _retry(spreadsheet.batch_update, {"requests": requests})
    logger.info("  %s 行をファイル名リンクに変換しました", len(requests))
    return len(requests)


def main() -> int:
    """添付セルの URL を一覧し、--apply ならファイル名リンクに書き換える。"""
    parser = argparse.ArgumentParser(description="添付セルをファイル名リンクに変換")
    parser.add_argument("--apply", action="store_true", help="実際に書き換える")
    args = parser.parse_args()

    settings = load_settings()
    creds = load_credentials()
    gc = gspread.authorize(creds)
    drive = drive_service(creds)
    root = settings.google_drive_folder_id
    # 共有ドライブなら、その中を検索するよう指定する（しないと一覧が空になりうる）
    drive.bind_drive(shared_drive_id(drive, root))

    files = log_spreadsheet_files(drive, root)

    total_rows = 0
    for meta in sorted(files, key=lambda f: f["name"]):
        spreadsheet = gc.open_by_key(meta["id"])
        for worksheet in _retry(spreadsheet.worksheets):
            total_rows += _process_worksheet(
                drive, spreadsheet, worksheet, file_name=meta["name"], apply=args.apply
            )
        time.sleep(PAUSE_BETWEEN_SHEETS)

    logger.info("\n%s", "─" * 50)
    if not total_rows:
        logger.info("変換が必要な行はありません。")
    elif args.apply:
        logger.info("変換完了: %s 行", total_rows)
    else:
        logger.info("対象: %s 行\n実行するには --apply を付けてください。", total_rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
