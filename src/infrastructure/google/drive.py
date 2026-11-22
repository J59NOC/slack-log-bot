"""Google Drive の共通操作: クライアント、フォルダ、共有。

ここは設定（.env）を読まない。setup.py が、設定を作っている途中でも使えるように。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from googleapiclient.discovery import build

from src.infrastructure.google.auth import load_credentials

if TYPE_CHECKING:
    from collections.abc import Callable

    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import Resource

logger = logging.getLogger(__name__)

FOLDER_MIME = "application/vnd.google-apps.folder"
PUBLIC_FOLDER_NAME = "public"
PRIVATE_FOLDER_NAME = "private"
ATTACHMENTS_FOLDER_NAME = "添付ファイル"

# Requests that need telling a shared drive exists. A files() or permissions()
# call without supportsAllDrives fails as a 404 on a file that plainly exists,
# and files().list quietly returns nothing at all — the confusing shape of
# failure, so the flags are filled in here rather than at forty call sites
# where the one that gets forgotten is the one that breaks.
_ALL_DRIVES_METHODS = {
    "files": {"get", "list", "create", "update", "copy", "delete"},
    "permissions": {"get", "list", "create", "update", "delete"},
}


class _AllDrives:
    """A Drive collection that passes the shared-drive flags on every call."""

    def __init__(
        self, inner: Resource, methods: set[str], *, is_files: bool, drive_id: str = ""
    ) -> None:
        """包む API の集まり（files / permissions）と、補う対象のメソッドを受け取る。"""
        self._inner = inner
        self._methods = methods
        self._is_files = is_files
        self._drive_id = drive_id

    def __getattr__(self, name: str) -> Callable[..., object]:
        """共有ドライブ用の引数を補ってから呼ぶメソッドを返す。"""
        method = getattr(self._inner, name)
        if name not in self._methods:
            return method

        def call(**kwargs: object) -> object:
            kwargs.setdefault("supportsAllDrives", True)
            if name == "list" and self._is_files:
                kwargs.setdefault("includeItemsFromAllDrives", True)
                if self._drive_id:
                    # Naming the drive is the documented way to search one.
                    # A parent clause alone runs against the default corpus,
                    # which need not reach into a shared drive — and a search
                    # that comes back empty reads as "not created yet", so the
                    # bot builds a second spreadsheet beside the first.
                    kwargs.setdefault("corpora", "drive")
                    kwargs.setdefault("driveId", self._drive_id)
            return method(**kwargs)

        return call


class DriveService:
    """The Drive client, wrapping the two collections that need the flags."""

    def __init__(self, service: Resource) -> None:
        """build("drive", "v3") で作ったクライアントを受け取る。"""
        self._service = service
        self._drive_id = ""

    def bind_drive(self, drive_id: str | None) -> None:
        """Search inside this shared drive from here on. None for My Drive."""
        self._drive_id = drive_id or ""

    def files(self) -> _AllDrives:
        """ファイルの操作（共有ドライブ用の引数を補う）。"""
        return _AllDrives(
            self._service.files(),
            _ALL_DRIVES_METHODS["files"],
            is_files=True,
            drive_id=self._drive_id,
        )

    def permissions(self) -> _AllDrives:
        """共有設定の操作（共有ドライブ用の引数を補う）。"""
        return _AllDrives(
            self._service.permissions(), _ALL_DRIVES_METHODS["permissions"], is_files=False
        )

    def __getattr__(self, name: str) -> object:
        """それ以外（files / permissions 以外）は、元のクライアントのものをそのまま使う。"""
        return getattr(self._service, name)


def wrap_drive(service: Resource) -> DriveService:
    """build("drive", "v3") の戻り値を、共有ドライブ対応のクライアントにする。"""
    return DriveService(service)


def drive_service(credentials: Credentials | None = None) -> DriveService:
    """Return a Drive client that works in My Drive and in a shared drive alike."""
    return wrap_drive(build("drive", "v3", credentials=credentials or load_credentials()))


def shared_drive_id(service: DriveService, file_id: str) -> str | None:
    """Return the shared drive holding this file, or None when it sits in My Drive.

    Which one it is decides who can read the logs. In My Drive the bot shares
    each channel's file with that channel's members and takes the share back
    when they leave. A shared drive answers that question itself: membership
    is the drive's, it cannot be narrowed per file, and a share the bot added
    on top could not be removed again. So the bot stops sharing and lets the
    drive decide.
    """
    try:
        return service.files().get(fileId=file_id, fields="driveId").execute().get("driveId")
    except Exception as e:
        logger.warning("Could not tell whether %s is in a shared drive: %s", file_id, e)
        return None


def ensure_folder(service: DriveService, name: str, parent_id: str) -> str:
    """Return a named child folder, creating it when necessary."""
    try:
        query = (
            f"name = '{name}' and '{parent_id}' in parents and "
            f"mimeType = '{FOLDER_MIME}' and trashed = false"
        )
        found = (
            service.files().list(q=query, fields="files(id)", pageSize=1).execute().get("files", [])
        )
        if found:
            return found[0]["id"]
        created = (
            service.files()
            .create(
                body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
                fields="id",
            )
            .execute()
        )
        logger.info("Created Drive folder: %s", name)
        return created["id"]
    except Exception as e:
        msg = f"Driveフォルダ '{name}' を準備できません: {e}"
        raise RuntimeError(msg) from e


def find_file(service: DriveService, parent_id: str, mime_type: str, name: str = "") -> str | None:
    """Find one direct child file, optionally by name."""
    query = f"'{parent_id}' in parents and mimeType = '{mime_type}' and trashed = false"
    if name:
        query += f" and name = '{name}'"
    try:
        files = (
            service.files().list(q=query, fields="files(id)", pageSize=1).execute().get("files", [])
        )
        return files[0]["id"] if files else None
    except Exception as e:
        msg = f"Driveファイルを検索できません: {e}"
        raise RuntimeError(msg) from e


def move_into_folder(service: DriveService, file_id: str, folder_id: str) -> bool:
    """Move a file into folder_id without changing its id."""
    try:
        meta = service.files().get(fileId=file_id, fields="parents").execute()
        parents = meta.get("parents", [])
        if folder_id in parents:
            return False
        service.files().update(
            fileId=file_id,
            addParents=folder_id,
            removeParents=",".join(parents),
            fields="id",
        ).execute()
    except Exception as e:
        msg = f"Driveファイル {file_id} を保存先へ移動できません: {e}"
        raise RuntimeError(msg) from e
    return True


# Drive appProperty tying a channel's folder and spreadsheet to its Slack
# channel, so a rename does not strand the files under the old name.
CHANNEL_ID_PROPERTY = "slackChannelId"


# Drive roles, weakest first, so a share is only ever raised.
ROLE_RANK = {
    "reader": 1,
    "commenter": 2,
    "writer": 3,
    "fileOrganizer": 4,
    "organizer": 5,
    "owner": 6,
}


def sync_permissions(
    service: DriveService,
    file_id: str,
    member_emails: list[str],
    revoke: list[str] | None = None,
    role: str = "reader",
) -> tuple[int, int]:
    """Bring a file's sharing in line with a channel's membership.

    Returns (granted, revoked). Someone already holding a stronger role than
    `role` keeps it, so a share set up by hand is not quietly downgraded. A
    weaker one is raised, which is how a file shared before the wanted role
    changed catches up without a migration.

    `revoke` is an explicit list rather than "anyone not in member_emails" —
    a member whose email cannot be resolved would otherwise look like someone
    to remove, and the whole channel would lose access over a failed lookup.
    """
    try:
        existing = (
            service.permissions()
            .list(fileId=file_id, fields="permissions(id,type,role,emailAddress)")
            .execute()
            .get("permissions", [])
        )
    except Exception as e:
        logger.warning("Could not read permissions for %s: %s", file_id, e)
        return (0, 0)

    by_email = {
        p.get("emailAddress", "").lower(): p
        for p in existing
        if p.get("type") == "user" and p.get("emailAddress")
    }

    granted = 0
    for email in member_emails:
        current = by_email.get(email.lower())
        try:
            if current is None:
                service.permissions().create(
                    fileId=file_id,
                    body={"type": "user", "role": role, "emailAddress": email},
                    sendNotificationEmail=False,
                ).execute()
                granted += 1
            elif ROLE_RANK.get(current.get("role"), 0) < ROLE_RANK.get(role, 0):
                service.permissions().update(
                    fileId=file_id,
                    permissionId=current["id"],
                    body={"role": role},
                ).execute()
        except Exception as e:
            logger.warning("Failed to share %s with %s: %s", file_id, email, e)

    revoked = 0
    for email in revoke or []:
        permission = by_email.get(email.lower())
        # Whoever left is dropped whatever role they hold, but the owner is in
        # this list too and cannot be removed from their own file.
        if not permission or permission.get("role") == "owner":
            continue
        try:
            service.permissions().delete(fileId=file_id, permissionId=permission["id"]).execute()
            revoked += 1
        except Exception as e:
            logger.warning("Failed to revoke %s on %s: %s", email, file_id, e)

    return (granted, revoked)
