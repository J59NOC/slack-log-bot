"""Trash the duplicate attachment copies left behind by repeated collection runs.

Every run downloaded and re-uploaded the attachments of messages it already
had. Their rows were deduped at write time, so only the first upload was ever
linked from a spreadsheet; the rest are orphans taking up Drive quota.

Files still linked from a spreadsheet are never touched, so the links in the
sheets keep resolving. Duplicates are moved to the Drive trash rather than
deleted, so a mistake is recoverable for 30 days.

Usage:
    python -m scripts.cleanup_duplicate_files            # 一覧を出すだけ (dry run)
    python -m scripts.cleanup_duplicate_files --apply    # 実際にゴミ箱へ移動
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

import gspread

# `python scripts/cleanup_duplicate_files.py` と直接起動されたときも src を import できるように
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.infrastructure.google.auth import load_credentials
from src.infrastructure.google.drive import (
    ATTACHMENTS_FOLDER_NAME,
    FOLDER_MIME,
    drive_service,
)
from src.infrastructure.google.sheets.layout import ATTACHMENT_COLUMN
from src.infrastructure.google.sheets.retry import retry as _retry
from src.infrastructure.settings import load_settings

if TYPE_CHECKING:
    from src.infrastructure.google.drive import DriveService

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DRIVE_ID_PATTERN = re.compile(r"/d/([A-Za-z0-9_-]{20,})|[?&]id=([A-Za-z0-9_-]{20,})")

# フォルダごとに、重複の多いファイル名をいくつまで表示するか
SHOWN_NAMES = 8


class IncompleteScanError(RuntimeError):
    """Raised when the set of still-referenced files could not be built in full."""


def linked_file_ids(gc: gspread.Client, drive: DriveService, root_folder_id: str) -> set[str]:
    """Collect every Drive file id referenced from the bot's spreadsheets.

    Only the attachment column is read. Message bodies can contain Drive URLs
    a person pasted into Slack, which have nothing to do with the uploads.

    Any failure aborts. Deciding what to delete from a partial picture is how
    a rate-limited read turns into a deleted file that a sheet still links to.
    """
    ids: set[str] = set()
    query = (
        f"'{root_folder_id}' in parents and "
        f"mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false"
    )
    sheets = (
        drive.files()
        .list(q=query, fields="files(id, name)", pageSize=1000)
        .execute()
        .get("files", [])
    )

    for meta in sheets:
        try:
            spreadsheet = gc.open_by_key(meta["id"])
            for worksheet in _retry(spreadsheet.worksheets):
                # The guide tab is two columns wide; asking for column F there
                # is out of range rather than empty.
                if worksheet.col_count < ATTACHMENT_COLUMN:
                    continue
                for cell in _retry(worksheet.col_values, ATTACHMENT_COLUMN):
                    for match in DRIVE_ID_PATTERN.finditer(cell):
                        ids.add(match.group(1) or match.group(2))
        except Exception as e:
            msg = (
                f"{meta['name']} を読めませんでした: {e}\n"
                f"  参照中のファイルを取り違える恐れがあるため中断します。"
                f"時間をおいて再実行してください。"
            )
            raise IncompleteScanError(msg) from e

    logger.info("スプレッドシートから参照されているファイル: %s 件", len(ids))
    return ids


def channel_folders(drive: DriveService, root_folder_id: str) -> list[dict]:
    """添付ファイルの、チャンネルごとのフォルダ。"""
    attachments = (
        drive.files()
        .list(
            q=f"name = '{ATTACHMENTS_FOLDER_NAME}' and '{root_folder_id}' in parents "
            f"and mimeType = '{FOLDER_MIME}' and trashed = false",
            fields="files(id, name)",
            pageSize=1,
        )
        .execute()
        .get("files", [])
    )
    if not attachments:
        return []
    return (
        drive.files()
        .list(
            q=f"'{attachments[0]['id']}' in parents and mimeType = '{FOLDER_MIME}' "
            f"and trashed = false",
            fields="files(id, name)",
            pageSize=1000,
        )
        .execute()
        .get("files", [])
    )


def removable_copies(files: list[dict], linked: set[str]) -> list[dict]:
    """同じ名前のファイルのうち、消してよい複製。"""
    by_name: dict[str, list[dict]] = defaultdict(list)
    for f in files:
        by_name[f["name"]].append(f)

    removable: list[dict] = []
    for copies in by_name.values():
        if len(copies) <= 1:
            continue
        # Keep everything a spreadsheet points at. If nothing is linked,
        # keep the oldest, which is the copy the first run would have
        # linked before the row was written.
        keep = {f["id"] for f in copies if f["id"] in linked}
        if not keep:
            keep = {copies[0]["id"]}
        removable.extend(f for f in copies if f["id"] not in keep)
    return removable


def _report_folder(folder_name: str, removable: list[dict]) -> None:
    size = sum(int(f.get("size", 0)) for f in removable)
    logger.info("\n%s: 重複 %s 件 (%.1f MB)", folder_name, len(removable), size / 1024 / 1024)
    counts: dict[str, int] = defaultdict(int)
    for f in removable:
        counts[f["name"]] += 1
    for name, n in sorted(counts.items(), key=lambda kv: -kv[1])[:SHOWN_NAMES]:
        logger.info("    %s x%s", name, n)
    if len(counts) > SHOWN_NAMES:
        logger.info("    ... 他 %s 種類", len(counts) - SHOWN_NAMES)


def _trash(drive: DriveService, file: dict) -> bool:
    try:
        drive.files().update(fileId=file["id"], body={"trashed": True}).execute()
    except Exception as e:
        logger.warning("    ! %s を移動できませんでした: %s", file["name"], e)
        return False
    return True


def _restore_if_trashed(drive: DriveService, file_id: str) -> bool:
    """ゴミ箱に入っていたら戻す。戻したら True。調べられなければ何もしない。"""
    try:
        if drive.files().get(fileId=file_id, fields="trashed").execute().get("trashed"):
            drive.files().update(fileId=file_id, body={"trashed": False}).execute()
            return True
    except Exception as e:
        logger.debug("Could not check %s: %s", file_id, e)
    return False


def main() -> int:
    """重複を一覧し、--apply ならゴミ箱へ移す。"""
    parser = argparse.ArgumentParser(description="重複した添付ファイルをゴミ箱へ移動")
    parser.add_argument("--apply", action="store_true", help="実際に移動する（既定は一覧のみ）")
    args = parser.parse_args()

    settings = load_settings()
    creds = load_credentials()
    drive = drive_service(creds)
    gc = gspread.authorize(creds)
    root = settings.google_drive_folder_id

    try:
        linked = linked_file_ids(gc, drive, root)
    except IncompleteScanError as e:
        logger.error("中断: %s", e)
        return 1

    folders = channel_folders(drive, root)
    if not folders:
        logger.info("添付フォルダが見つかりません。")
        return 0

    trashed = 0
    total_dupes = 0
    total_bytes = 0
    for folder in sorted(folders, key=lambda f: f["name"]):
        files = (
            drive.files()
            .list(
                q=f"'{folder['id']}' in parents and trashed = false",
                fields="files(id, name, size, createdTime)",
                orderBy="createdTime",
                pageSize=1000,
            )
            .execute()
            .get("files", [])
        )
        removable = removable_copies(files, linked)
        if not removable:
            continue

        total_dupes += len(removable)
        total_bytes += sum(int(f.get("size", 0)) for f in removable)
        _report_folder(folder["name"], removable)
        if args.apply:
            trashed += sum(_trash(drive, f) for f in removable)

    logger.info("\n%s", "─" * 50)
    if not total_dupes:
        logger.info("重複はありませんでした。")
        return 0

    logger.info("重複合計: %s 件 / %.1f MB", total_dupes, total_bytes / 1024 / 1024)
    if not args.apply:
        logger.info("これは一覧表示のみです。実行するには --apply を付けてください。")
        return 0

    # Belt and braces: confirm nothing a sheet points at ended up in the trash,
    # and put it back if it did.
    restored = sum(_restore_if_trashed(drive, file_id) for file_id in linked)

    logger.info("ゴミ箱へ移動: %s 件", trashed)
    if restored:
        logger.warning("参照中のファイル %s 件を誤って移動していたため元に戻しました。", restored)
    logger.info("30日以内なら Drive のゴミ箱から復元できます。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
