"""添付ファイルを Google Drive のチャンネル別フォルダに保存する。"""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

from googleapiclient.http import MediaIoBaseUpload

from src.domain.log_entry import Attachment
from src.domain.people import AccessChange
from src.infrastructure.google.drive import (
    ATTACHMENTS_FOLDER_NAME,
    CHANNEL_ID_PROPERTY,
    FOLDER_MIME,
    PRIVATE_FOLDER_NAME,
    PUBLIC_FOLDER_NAME,
    ensure_folder,
    find_file,
    list_children,
    shared_drive_id,
    sync_permissions,
)

if TYPE_CHECKING:
    from src.domain.channel import Channel
    from src.infrastructure.google.drive import DriveService

logger = logging.getLogger(__name__)


def attachment_folders(drive: DriveService, root_folder_id: str) -> list[dict]:
    """Every channel folder under the storage folder that holds attachments.

    public/添付ファイル/#チャンネル/、private/#チャンネル/、それに以前の構成の
    添付ファイル/#チャンネル/（ルート直下）。name は "private/#hoge" のように、
    どこのフォルダか分かる形にして返す。

    Nothing is created, and a failed call is raised rather than skipped.
    """

    def folder(parent_id: str | None, name: str) -> str | None:
        return find_file(drive, parent_id, FOLDER_MIME, name) if parent_id else None

    public_id = folder(root_folder_id, PUBLIC_FOLDER_NAME)
    places = [
        (ATTACHMENTS_FOLDER_NAME, folder(root_folder_id, ATTACHMENTS_FOLDER_NAME)),
        (
            f"{PUBLIC_FOLDER_NAME}/{ATTACHMENTS_FOLDER_NAME}",
            folder(public_id, ATTACHMENTS_FOLDER_NAME),
        ),
        (PRIVATE_FOLDER_NAME, folder(root_folder_id, PRIVATE_FOLDER_NAME)),
    ]

    folders = []
    for label, parent_id in places:
        if parent_id:
            folders += [
                {"id": f["id"], "name": f"{label}/{f['name']}"}
                for f in list_children(drive, parent_id, FOLDER_MIME)
            ]
    return folders


class DriveAttachmentStore:
    """AttachmentStore の Google Drive 実装。

    公開チャンネルの添付は public/添付ファイル/#チャンネル/ に、プライベート
    チャンネルの添付は private/#チャンネル/ に置く。
    """

    @classmethod
    def open(cls, service: DriveService, root_folder_id: str) -> DriveAttachmentStore:
        """保存先が共有ドライブかどうかを調べてから開く。"""
        drive_id = shared_drive_id(service, root_folder_id)
        service.bind_drive(drive_id)
        if drive_id:
            logger.info(
                "Attachments live in a shared drive; private channel folders use "
                "Drive limited access and are shared per channel."
            )
        return cls(service, root_folder_id, drive_id)

    def __init__(
        self, service: DriveService, root_folder_id: str, drive_id: str | None = None
    ) -> None:
        """Drive クライアント・保存先のルートフォルダ・共有ドライブの ID を受け取る。"""
        self.service = service
        self.root_folder_id = root_folder_id
        self.shared_drive_id = drive_id
        # Cache: channel_name -> folder_id
        self._channel_folders: dict[str, str] = {}
        self._attachments_root: str | None = None
        self._public_root: str | None = None
        self._private_root: str | None = None

    def _share_with_emails(self, file_id: str, emails: list[str]) -> int:
        """Share a file/folder with specific email addresses. Returns the count."""
        shared = 0
        for email in emails:
            if self._share_as_reader(file_id, email):
                shared += 1
        return shared

    def _share_as_reader(self, file_id: str, email: str) -> bool:
        try:
            self.service.permissions().create(
                fileId=file_id,
                body={"type": "user", "role": "reader", "emailAddress": email},
                sendNotificationEmail=False,
            ).execute()
        except Exception as e:
            logger.warning("Failed to share with %s: %s", email, e)
            return False
        return True

    def _find_folder(self, name: str, parent: str) -> str | None:
        query = (
            f"name = '{name}' and '{parent}' in parents and "
            f"mimeType = '{FOLDER_MIME}' and trashed = false"
        )
        results = self.service.files().list(q=query, fields="files(id, name)", pageSize=1).execute()
        files = results.get("files", [])
        return files[0]["id"] if files else None

    def _get_attachments_root(self) -> str:
        """Return the public folder that holds public channel attachments."""
        if self._attachments_root:
            return self._attachments_root

        self._attachments_root = ensure_folder(
            self.service, ATTACHMENTS_FOLDER_NAME, self._get_public_root()
        )
        return self._attachments_root

    def _get_public_root(self) -> str:
        if not self._public_root:
            self._public_root = ensure_folder(self.service, PUBLIC_FOLDER_NAME, self.root_folder_id)
        return self._public_root

    def _get_private_root(self) -> str:
        if not self._private_root:
            self._private_root = ensure_folder(
                self.service, PRIVATE_FOLDER_NAME, self.root_folder_id
            )
        return self._private_root

    def _find_by_channel_id(self, channel_id: str, parent: str) -> dict | None:
        if not channel_id:
            return None
        query = (
            f"appProperties has {{ key='{CHANNEL_ID_PROPERTY}' and value='{channel_id}' }} "
            f"and '{parent}' in parents and trashed = false"
        )
        try:
            files = (
                self.service.files()
                .list(q=query, fields="files(id, name)", pageSize=1)
                .execute()
                .get("files", [])
            )
        except Exception as e:
            logger.warning("Could not search folders by channel id: %s", e)
            return None
        return files[0] if files else None

    def _find_channel_folder_anywhere(self, channel_id: str) -> dict | None:
        if not channel_id:
            return None
        query = (
            f"appProperties has {{ key='{CHANNEL_ID_PROPERTY}' and value='{channel_id}' }} "
            f"and mimeType = '{FOLDER_MIME}' and trashed = false"
        )
        files = (
            self.service.files()
            .list(q=query, fields="files(id, name)", pageSize=1)
            .execute()
            .get("files", [])
        )
        return files[0] if files else None

    def _stamp_channel_id(self, file_id: str, channel_id: str, name: str | None = None) -> None:
        body = {"appProperties": {CHANNEL_ID_PROPERTY: channel_id}}
        if name:
            body["name"] = name
        try:
            self.service.files().update(fileId=file_id, body=body, fields="id").execute()
        except Exception as e:
            logger.warning("Could not stamp folder %s: %s", file_id, e)

    def rename_channel(self, channel: Channel) -> bool:
        """Follow a channel rename so uploads keep landing in the same folder."""
        found = self._find_channel_folder_anywhere(channel.id)
        if not found:
            return False
        if found["name"] != f"#{channel.name}":
            self._stamp_channel_id(found["id"], channel.id, f"#{channel.name}")
        self._channel_folders.clear()
        return True

    def _get_or_create_channel_folder(
        self,
        channel: Channel,
        member_emails: list[str] | None = None,
    ) -> str:
        """Get or create the channel's folder under the attachments folder.

        Located by the channel id stamped on it, so a renamed channel keeps
        uploading into the folder that already holds its files.
        """
        channel_name, channel_id = channel.name, channel.id
        if channel_name in self._channel_folders:
            return self._channel_folders[channel_name]

        attachments_root = (
            self._get_private_root() if channel.is_private else self._get_attachments_root()
        )
        folder_name = f"#{channel_name}"

        stamped = self._find_by_channel_id(channel_id, attachments_root)
        if stamped:
            if stamped["name"] != folder_name:
                self._stamp_channel_id(stamped["id"], channel_id, folder_name)
            self._channel_folders[channel_name] = stamped["id"]
            return stamped["id"]

        folder_id = self._find_folder(folder_name, attachments_root)
        if folder_id and channel_id:
            self._stamp_channel_id(folder_id, channel_id)

        if not folder_id:
            # Adopt an old root-level channel folder so existing uploads remain
            # in the same Drive folder after the layout change.
            legacy_id = self._find_folder(folder_name, self.root_folder_id)
            if legacy_id:
                self.service.files().update(
                    fileId=legacy_id,
                    addParents=attachments_root,
                    removeParents=self.root_folder_id,
                    fields="id",
                ).execute()
                folder_id = legacy_id
                logger.info("Moved %s into the channel folder area", folder_name)

        if not folder_id:
            folder = (
                self.service.files()
                .create(
                    body={
                        "name": folder_name,
                        "mimeType": FOLDER_MIME,
                        "parents": [attachments_root],
                        "appProperties": {CHANNEL_ID_PROPERTY: channel_id} if channel_id else {},
                    },
                    fields="id",
                )
                .execute()
            )
            folder_id = folder["id"]

            # Access follows the channel, not whether Slack calls it private:
            # the spreadsheet linking to these files is members-only too.
            shared = (
                self._share_with_emails(folder_id, member_emails or []) if channel.is_private else 0
            )
            logger.info("Created Drive folder: %s (shared with %s members)", folder_name, shared)

        self._channel_folders[channel_name] = folder_id
        return folder_id

    def sync_access(
        self,
        channel: Channel,
        member_emails: list[str],
        revoke: list[str] | None = None,
    ) -> AccessChange:
        """Match the channel folder's readers to the channel's membership."""
        folder_id = self._get_or_create_channel_folder(channel, member_emails)
        if self.shared_drive_id and not channel.is_private:
            return AccessChange(0, 0)
        return AccessChange(*sync_permissions(self.service, folder_id, member_emails, revoke))

    def upload(
        self,
        channel: Channel,
        file_name: str,
        content: bytes,
        mime_type: str,
        member_emails: list[str] | None = None,
    ) -> Attachment:
        """Upload a file to the channel's folder. Returns (file name, link).

        The name travels with the link so the sheet can show the filename with
        the URL behind it.
        """
        folder_id = self._get_or_create_channel_folder(channel, member_emails)

        file_metadata = {
            "name": file_name,
            "parents": [folder_id],
        }
        media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=False)
        uploaded = (
            self.service.files()
            .create(body=file_metadata, media_body=media, fields="id,webViewLink")
            .execute()
        )

        # The parent folder's permissions already cover the file; sharing it
        # again would double the Drive calls for no added access.

        logger.info(
            "Uploaded to Drive: #%s/%s -> %s", channel.name, file_name, uploaded["webViewLink"]
        )
        return Attachment(file_name, uploaded["webViewLink"])

    def trash(self, file_ids: list[str]) -> None:
        """Send files to the Drive trash.

        The trash rather than a deletion, so one made in error is still
        recoverable for a while.
        """
        for file_id in file_ids:
            self._trash_one(file_id)

    def _trash_one(self, file_id: str) -> None:
        try:
            self.service.files().update(fileId=file_id, body={"trashed": True}).execute()
            logger.info("Sent %s to the Drive trash with it", file_id)
        except Exception as e:
            logger.warning("Could not trash the attachment %s: %s", file_id, e)
