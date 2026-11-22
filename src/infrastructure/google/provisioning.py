"""セットアップ時の Google Drive / スプレッドシートの準備と点検。

ここは設定（.env）を読まない。setup.py が、設定を作っている途中で使うため。
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

# authorize は gspread から属性で引く（テストが gspread.authorize を差し替える）
import gspread
from googleapiclient.http import MediaIoBaseUpload

from src.infrastructure.google.drive import (
    ATTACHMENTS_FOLDER_NAME,
    CHANNEL_ID_PROPERTY,
    FOLDER_MIME,
    PRIVATE_FOLDER_NAME,
    PUBLIC_FOLDER_NAME,
    ensure_folder,
    find_file,
    shared_drive_id,
)
from src.infrastructure.google.sheets.guide import ensure_guide_sheet

if TYPE_CHECKING:
    from collections.abc import Callable

    from google.oauth2.credentials import Credentials

    from src.infrastructure.google.drive import DriveService

SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"

# One folder holds everything the bot owns for a workspace: the index
# spreadsheet, the per-channel spreadsheets, and the attachments folder.
#
# The workspace name is part of both, because one Google account can run the
# bot for several Slack workspaces. Without it the second workspace would find
# the first one's folder by name and adopt it, and two channels that happen to
# share a name would then write into the same spreadsheet.
WORKSPACE_FOLDER_NAME = "Slack ログ - {workspace}"
INDEX_SPREADSHEET_NAME = "Slack ログ 索引 - {workspace}"


def folder_id_from(raw: str) -> str:
    """Return the folder id out of a Drive URL, or the id if that is what was given."""
    match = re.search(r"/folders/([A-Za-z0-9_-]+)", raw)
    return match.group(1) if match else raw.split("?", maxsplit=1)[0].strip("/")


def describe(drive: DriveService, file_id: str, fields: str) -> dict:
    """ファイルの情報（fields で指定したもの）を引く。"""
    return drive.files().get(fileId=file_id, fields=fields).execute()


def _quoted(value: str) -> str:
    r"""Drive の検索式（q）に入れる文字列の \ と ' をエスケープする。

    ワークスペース名は "Bob's Team" のように ' を含みうる。
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


def find_or_create(
    drive: DriveService, name: str, mime_type: str, parent: str | None = None
) -> tuple[str, bool]:
    """Return (file_id, created). Reuses an existing item with the same name."""
    query = f"name = '{_quoted(name)}' and mimeType = '{mime_type}' and trashed = false"
    if parent:
        query += f" and '{parent}' in parents"
    found = drive.files().list(q=query, fields="files(id, name)", pageSize=1).execute()
    files = found.get("files", [])
    if files:
        return files[0]["id"], False

    metadata = {"name": name, "mimeType": mime_type}
    if parent:
        metadata["parents"] = [parent]
    created = drive.files().create(body=metadata, fields="id").execute()
    return created["id"], True


@dataclass(frozen=True)
class StorageLayout:
    """保存先の中身: 公開側のフォルダと、共有ドライブかどうか。"""

    public_folder_id: str
    in_shared_drive: bool


def prepare_layout(drive: DriveService, folder_id: str) -> StorageLayout:
    """保存先の下に public / private を用意する。共有ドライブかどうかも調べる。"""
    drive_id = shared_drive_id(drive, folder_id)
    drive.bind_drive(drive_id)
    public_folder_id = ensure_folder(drive, PUBLIC_FOLDER_NAME, folder_id)
    ensure_folder(drive, PRIVATE_FOLDER_NAME, folder_id)
    return StorageLayout(public_folder_id, in_shared_drive=bool(drive_id))


def find_index_spreadsheet(drive: DriveService, public_folder_id: str) -> str | None:
    """公開側のフォルダにある索引スプレッドシート。無ければ None。"""
    return find_file(drive, public_folder_id, SPREADSHEET_MIME)


def ensure_guide(credentials: Credentials, spreadsheet_id: str, *, in_shared_drive: bool) -> bool:
    """Google leaves an empty default sheet behind; make it the guide.

    書いたら True、もうあれば False。
    """
    spreadsheet = gspread.authorize(credentials).open_by_key(spreadsheet_id)
    return ensure_guide_sheet(spreadsheet, shared_drive=in_shared_drive)


def _stamp(drive: DriveService, file_id: str, channel_id: str) -> Exception | None:
    """ファイルにチャンネル ID を付ける。失敗したらその例外を返す。"""
    try:
        drive.files().update(
            fileId=file_id,
            body={"appProperties": {CHANNEL_ID_PROPERTY: channel_id}},
            fields="id",
        ).execute()
    except Exception as e:
        return e
    return None


def stamp_channel_ids(
    drive: DriveService,
    root_folder_id: str,
    channels: dict[str, str],
    on_error: Callable[[str, Exception], object] | None = None,
) -> int:
    """Record each channel's id on the files that hold its log.

    The bot stamps a file the first time it touches that channel, which can be
    a long wait for a quiet one — and until then a rename leaves the file
    unfindable, because the only handle on it is the name that just changed.
    Doing every channel up front closes that window.

    付与できなかったファイルは on_error(ファイル名, 例外) で知らせる。
    """
    stamped = 0
    wanted = {}
    for channel_id, name in channels.items():
        wanted[f"Slack Log - #{name}"] = channel_id
        wanted[f"#{name}"] = channel_id

    query = f"'{root_folder_id}' in parents and trashed = false"
    files = (
        drive.files()
        .list(q=query, fields="files(id, name, appProperties)", pageSize=1000)
        .execute()
        .get("files", [])
    )

    # Channel folders live one level down, under the attachments folder.
    attachments = next((f for f in files if f["name"] == ATTACHMENTS_FOLDER_NAME), None)
    if attachments:
        files += (
            drive.files()
            .list(
                q=f"'{attachments['id']}' in parents and trashed = false",
                fields="files(id, name, appProperties)",
                pageSize=1000,
            )
            .execute()
            .get("files", [])
        )

    for meta in files:
        channel_id = wanted.get(meta["name"])
        if not channel_id:
            continue
        if meta.get("appProperties", {}).get(CHANNEL_ID_PROPERTY) == channel_id:
            continue
        error = _stamp(drive, meta["id"], channel_id)
        if error is None:
            stamped += 1
        elif on_error:
            on_error(meta["name"], error)
    return stamped


def locate_index(drive: DriveService, root_folder_id: str, spreadsheet_id: str = "") -> str:
    """書き込みテスト用: 保存先から索引スプレッドシートを探す。見つからなければ例外。"""
    public_folder = find_file(drive, root_folder_id, FOLDER_MIME, PUBLIC_FOLDER_NAME)
    if not public_folder:
        msg = "public フォルダが見つかりません"
        raise RuntimeError(msg)
    spreadsheet_id = spreadsheet_id or find_file(drive, public_folder, SPREADSHEET_MIME)
    if not spreadsheet_id:
        msg = "public スプレッドシートが見つかりません"
        raise RuntimeError(msg)
    return spreadsheet_id


def spreadsheet_title(credentials: Credentials, spreadsheet_id: str) -> str:
    """スプレッドシートを開いて、その名前を返す（開けることの確認を兼ねる）。"""
    return gspread.authorize(credentials).open_by_key(spreadsheet_id).title


class StorageProbe:
    """Try, on a real folder, everything the bot will need to do there.

    使い捨てのファイルで試し、作ったものは cleanup() で消す。各操作は失敗すると
    例外をそのまま投げる（何が足りないかは呼び出し側が伝える）。
    """

    NAME = "__slack-log-bot-check__"

    def __init__(self, drive: DriveService, credentials: Credentials) -> None:
        """試すのに使う Drive クライアントと認証情報を受け取る。"""
        self.drive = drive
        self.credentials = credentials
        self.created: list[str] = []

    def create_folder(self, parent: str) -> dict:
        """フォルダを作れるか。"""
        folder = (
            self.drive.files()
            .create(
                body={"name": self.NAME, "mimeType": FOLDER_MIME, "parents": [parent]},
                fields="id",
            )
            .execute()
        )
        self.created.append(folder["id"])
        return folder

    def create_spreadsheet(self, parent: str) -> gspread.Spreadsheet:
        """スプレッドシートを作れるか。"""
        sheet = gspread.authorize(self.credentials).create(self.NAME, folder_id=parent)
        self.created.append(sheet.id)
        return sheet

    def upload(self, parent: str) -> dict:
        """ファイルをアップロードできるか（添付ファイル用）。"""
        uploaded = (
            self.drive.files()
            .create(
                body={"name": f"{self.NAME}.txt", "parents": [parent]},
                media_body=MediaIoBaseUpload(io.BytesIO(b"check"), mimetype="text/plain"),
                fields="id",
            )
            .execute()
        )
        self.created.append(uploaded["id"])
        return uploaded

    def trash(self, file_id: str) -> dict:
        """ゴミ箱へ移せるか（削除されたメッセージの添付を片付ける）。"""
        return self.drive.files().update(fileId=file_id, body={"trashed": True}).execute()

    def list_children(self, parent: str) -> list[dict]:
        """作ったものを検索で見つけられるか。"""
        return (
            self.drive.files()
            .list(
                q=f"'{parent}' in parents and trashed = false",
                fields="files(id,name)",
                pageSize=10,
            )
            .execute()
            .get("files", [])
        )

    def cleanup(self) -> list[str]:
        """作ったものを消す。消せなかった（ゴミ箱にも移せなかった）id を返す。"""
        return [file_id for file_id in reversed(self.created) if not self._discard(file_id)]

    def _discard(self, file_id: str) -> bool:
        """消す。消せなければゴミ箱へ移す。どちらもできなければ False。"""
        try:
            self.drive.files().delete(fileId=file_id).execute()
        except Exception:
            try:
                self.drive.files().update(fileId=file_id, body={"trashed": True}).execute()
            except Exception:
                return False
        return True
