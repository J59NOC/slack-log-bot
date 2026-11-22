"""チャンネルのログがどのスプレッドシート・どのタブにあるか。

Public channels  → tabs in the index spreadsheet under the public folder
Private channels → one spreadsheet per channel in private/#channel/,
                   shared with that channel's members only
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import gspread

from src.domain.people import AccessChange
from src.infrastructure.google.drive import (
    CHANNEL_ID_PROPERTY,
    PRIVATE_FOLDER_NAME,
    PUBLIC_FOLDER_NAME,
    ensure_folder,
    find_file,
    move_into_folder,
    shared_drive_id,
    sync_permissions,
)
from src.infrastructure.google.sheets.formatting import apply_sheet_format
from src.infrastructure.google.sheets.guide import ensure_guide_sheet, write_channel_index
from src.infrastructure.google.sheets.layout import HEADER_ROW, channel_spreadsheet_name
from src.infrastructure.google.sheets.retry import retry

if TYPE_CHECKING:
    from collections.abc import Iterator

    from src.domain.channel import Channel
    from src.infrastructure.google.drive import DriveService

logger = logging.getLogger(__name__)

SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"


@dataclass(frozen=True)
class StorageFolders:
    """保存先のフォルダ（ルート・公開側・非公開側）と、置かれている共有ドライブ。"""

    root_id: str
    public_id: str
    private_root_id: str
    # 共有ドライブの ID。マイドライブなら None
    shared_drive_id: str | None


class ChannelWorkbooks:
    """チャンネルとスプレッドシート・タブの対応、共有、索引を受け持つ。"""

    @classmethod
    def open(
        cls,
        gc: gspread.Client,
        drive: DriveService,
        root_folder_id: str,
        spreadsheet_id: str = "",
    ) -> ChannelWorkbooks:
        """保存先のフォルダと索引スプレッドシートを用意して開く。"""
        drive_id = shared_drive_id(drive, root_folder_id)
        drive.bind_drive(drive_id)
        public_folder_id = ensure_folder(drive, PUBLIC_FOLDER_NAME, root_folder_id)
        private_root_id = ensure_folder(drive, PRIVATE_FOLDER_NAME, root_folder_id)
        spreadsheet_id = spreadsheet_id or find_file(drive, public_folder_id, SPREADSHEET_MIME)
        if spreadsheet_id:
            index_spreadsheet = gc.open_by_key(spreadsheet_id)
        else:
            index_spreadsheet = gc.create("Slack ログ 索引", folder_id=public_folder_id)
            logger.info("Created public spreadsheet under the public folder")
        move_into_folder(drive, index_spreadsheet.id, public_folder_id)

        folders = StorageFolders(root_folder_id, public_folder_id, private_root_id, drive_id)
        workbooks = cls(gc, drive, folders, index_spreadsheet)
        workbooks._prepare_index()
        return workbooks

    def __init__(
        self,
        gc: gspread.Client,
        drive: DriveService,
        folders: StorageFolders,
        index_spreadsheet: gspread.Spreadsheet,
    ) -> None:
        """使う API クライアント・保存先のフォルダ・索引スプレッドシートを受け取る。"""
        self.gc = gc
        self._drive = drive
        self.drive_folder_id = folders.root_id
        self.shared_drive_id = folders.shared_drive_id
        self.public_folder_id = folders.public_id
        self.private_root_id = folders.private_root_id
        # Holds the guide and the channel index, and the public channels' tabs.
        self.index_spreadsheet = index_spreadsheet
        self._sheet_cache: dict[str, gspread.Worksheet] = {}
        self._channel_spreadsheets: dict[str, gspread.Spreadsheet] = {}
        self._index_entries: dict[str, str] = {}
        self._formatted_sheets: set[str] = set()

    def _prepare_index(self) -> None:
        # Covers spreadsheets created before the guide existed. It is skipped
        # once present, and must never stop the bot from starting.
        try:
            if ensure_guide_sheet(self.index_spreadsheet, shared_drive=bool(self.shared_drive_id)):
                logger.info("Created guide sheet in the shared spreadsheet")
            # The index is intentionally public-channel-only. Refreshing it at
            # startup removes private-channel entries left by older versions.
            write_channel_index(self.index_spreadsheet, {})
        except Exception as e:
            logger.warning("Could not create guide sheet: %s", e)

    # ── 共有 ──

    def share_with_members(self, spreadsheet: gspread.Spreadsheet, member_emails: list[str]) -> int:
        """Grant a channel's members edit access. Returns how many were added.

        Edit rather than view: Sheets offers the outline +/- toggles only to
        someone who can edit, and a log whose threads cannot be folded is the
        wall of text the folding was there to fix.

        In a shared drive nobody is added here. Membership is the drive's, a
        share added on top of it could not be taken back, and the drive's own
        members can already edit.
        """
        if self.shared_drive_id:
            return 0
        self._restrict_resharing(spreadsheet.id)
        added = 0
        for email in member_emails:
            if self._share_as_writer(spreadsheet, email):
                added += 1
        return added

    @staticmethod
    def _share_as_writer(spreadsheet: gspread.Spreadsheet, email: str) -> bool:
        try:
            spreadsheet.share(email, perm_type="user", role="writer", notify=False)
        except Exception as e:
            logger.warning("Failed to share %s with %s: %s", spreadsheet.title, email, e)
            return False
        return True

    def sync_access(
        self,
        channel: Channel,
        member_emails: list[str],
        revoke: list[str] | None = None,
    ) -> AccessChange:
        """Match the channel spreadsheet's sharing to the channel's membership."""
        ss = self.spreadsheet_for(channel, member_emails)
        if self.shared_drive_id and not channel.is_private:
            return AccessChange(0, 0)
        granted = revoked = 0
        if not self.shared_drive_id:
            granted, revoked = sync_permissions(
                self._drive, ss.id, member_emails, revoke, role="writer"
            )
        if channel.is_private:
            folder_id = self.private_folder_for(channel, member_emails)
            folder_granted, folder_revoked = sync_permissions(
                self._drive, folder_id, member_emails, revoke, role="reader"
            )
            granted += folder_granted
            revoked += folder_revoked
        return AccessChange(granted, revoked)

    def _restrict_resharing(self, file_id: str) -> None:
        """Stop editors passing the file on.

        Edit access carries the right to share unless this is turned off, and
        one member of a private channel could otherwise hand its log to the
        whole workspace — which is the thing the per-channel split prevents.

        Not a shared drive's business: sharing there is the drive's setting,
        and the field is rejected on a file it owns.
        """
        if self.shared_drive_id:
            return
        try:
            self._drive.files().update(fileId=file_id, body={"writersCanShare": False}).execute()
        except Exception as e:
            logger.warning("Could not restrict re-sharing on %s: %s", file_id, e)

    # ── 探す・作る ──

    def _find_by_channel_id(self, channel_id: str, parent: str | None = None) -> dict | None:
        """Return the spreadsheet stamped with this channel id, if there is one."""
        if not channel_id:
            return None
        parent = parent or self.public_folder_id
        query = (
            f"appProperties has {{ key='{CHANNEL_ID_PROPERTY}' and value='{channel_id}' }} "
            f"and '{parent}' in parents and trashed = false"
        )
        try:
            files = (
                self._drive.files()
                .list(q=query, fields="files(id, name)", pageSize=1)
                .execute()
                .get("files", [])
            )
        except Exception as e:
            logger.warning("Could not search by channel id: %s", e)
            return None
        return files[0] if files else None

    def _find_by_name(self, name: str, parent: str | None = None) -> dict | None:
        parent = parent or self.public_folder_id
        query = (
            f"name = '{name}' and '{parent}' in parents and "
            f"mimeType = '{SPREADSHEET_MIME}' and trashed = false"
        )
        files = (
            self._drive.files()
            .list(q=query, fields="files(id, name)", pageSize=1)
            .execute()
            .get("files", [])
        )
        return files[0] if files else None

    def _stamp_channel_id(self, file_id: str, channel_id: str, name: str | None = None) -> None:
        """Record the channel id on the file, and optionally rename it."""
        body = {"appProperties": {CHANNEL_ID_PROPERTY: channel_id}}
        if name:
            body["name"] = name
        try:
            self._drive.files().update(fileId=file_id, body=body, fields="id").execute()
        except Exception as e:
            logger.warning("Could not stamp %s with the channel id: %s", file_id, e)

    def spreadsheet_for(
        self,
        channel: Channel,
        member_emails: list[str] | None = None,
    ) -> gspread.Spreadsheet:
        """Return the spreadsheet holding the channel's log.

        A private channel gets its own file rather than a tab in a shared one:
        Sheets grants access per file, so a tab cannot be handed to one group
        without handing over every other channel in the same spreadsheet.

        The file is found by the channel id stamped on it, not by its name: a
        renamed channel would otherwise not match its own spreadsheet, and a
        second one would be created with the history left behind in the first.
        """
        channel_name, channel_id = channel.name, channel.id
        if channel_name in self._channel_spreadsheets:
            return self._channel_spreadsheets[channel_name]

        if not channel.is_private:
            self._channel_spreadsheets[channel_name] = self.index_spreadsheet
            return self.index_spreadsheet

        ss_name = channel_spreadsheet_name(channel_name)
        folder_id = self.private_folder_for(channel, member_emails or [])
        found = self._find_by_channel_id(channel_id, folder_id)
        if found and found["name"] != ss_name:
            # The channel was renamed while nothing was watching.
            logger.info("Renaming %s -> %s", found["name"], ss_name)
            self._stamp_channel_id(found["id"], channel_id, ss_name)

        if not found:
            # Either a sheet from before ids were stamped, or a new channel.
            found = self._find_by_name(ss_name, folder_id)
            if found and channel_id:
                self._stamp_channel_id(found["id"], channel_id)

        if found:
            ss = self.gc.open_by_key(found["id"])
        else:
            ss = self.gc.create(ss_name, folder_id=folder_id)
            if channel_id:
                self._stamp_channel_id(ss.id, channel_id)
            shared = self.share_with_members(ss, member_emails or [])
            logger.info("Created spreadsheet: %s (shared with %s members)", ss_name, shared)
            if not shared:
                logger.warning(
                    "%s is not shared with anyone: no member email was "
                    "resolvable. Check the users:read.email scope.",
                    ss_name,
                )
        self._channel_spreadsheets[channel_name] = ss
        return ss

    def private_folder_for(self, channel: Channel, member_emails: list[str]) -> str:
        """Create the private channel folder under the private parent folder."""
        query = (
            f"appProperties has {{ key='{CHANNEL_ID_PROPERTY}' and value='{channel.id}' }} "
            f"and '{self.private_root_id}' in parents and "
            f"mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        )
        found = (
            self._drive.files()
            .list(q=query, fields="files(id, name)", pageSize=1)
            .execute()
            .get("files", [])
        )
        if found:
            folder_id = found[0]["id"]
        else:
            folder = (
                self._drive.files()
                .create(
                    body={
                        "name": f"#{channel.name}",
                        "mimeType": "application/vnd.google-apps.folder",
                        "parents": [self.private_root_id],
                        "appProperties": {CHANNEL_ID_PROPERTY: channel.id},
                    },
                    fields="id",
                )
                .execute()
            )
            folder_id = folder["id"]
            logger.info("Created private channel folder: #%s", channel.name)

        sync_permissions(self._drive, folder_id, member_emails, role="reader")

        return folder_id

    def worksheet_for(
        self,
        channel: Channel,
        member_emails: list[str] | None = None,
    ) -> gspread.Worksheet:
        """Return the tab holding the channel's log, created and formatted on first use."""
        channel_name = channel.name
        if channel_name in self._sheet_cache:
            return self._sheet_cache[channel_name]

        ss = self.spreadsheet_for(channel, member_emails)

        try:
            worksheet = ss.worksheet(channel_name)
        except gspread.exceptions.WorksheetNotFound:
            if ss is self.index_spreadsheet:
                worksheet = ss.add_worksheet(title=channel_name, rows=1000, cols=len(HEADER_ROW))
            else:
                worksheet = ss.sheet1
                retry(worksheet.update_title, channel_name, idempotent=False)

        is_new = False
        if worksheet.row_count == 0 or not retry(worksheet.row_values, 1):
            retry(worksheet.append_row, HEADER_ROW, idempotent=False)
            is_new = True

        if is_new or channel_name not in self._formatted_sheets:
            apply_sheet_format(worksheet, ss)
            self._formatted_sheets.add(channel_name)

        # 索引は公開チャンネルだけ。プライベートチャンネルの名前と URL を
        # 誰でも開ける索引に載せない。
        if not channel.is_private:
            self._register_in_index(channel_name, ss, worksheet.id)

        self._sheet_cache[channel_name] = worksheet
        return worksheet

    # ── 索引 ──

    def _register_in_index(
        self, channel_name: str, spreadsheet: gspread.Spreadsheet, worksheet_id: int
    ) -> None:
        """Record a public channel's tab URL in the index."""
        try:
            self._index_entries[channel_name] = f"{spreadsheet.url}#gid={worksheet_id}"
            write_channel_index(self.index_spreadsheet, self.index_entries())
        except Exception as e:
            logger.warning("Could not update the channel index: %s", e)

    def index_entries(self) -> dict[str, str]:
        """Public-channel entries currently discovered in this process."""
        return dict(self._index_entries)

    def index_url(self) -> str:
        """URL of the spreadsheet holding the guide and the channel index."""
        return self.index_spreadsheet.url

    # ── キャッシュ・一覧 ──

    def forget(self, channel_name: str | None = None) -> None:
        """Clear cached spreadsheets and tabs. If channel_name is None, clear all."""
        if channel_name:
            self._sheet_cache.pop(channel_name, None)
            self._channel_spreadsheets.pop(channel_name, None)
            self._formatted_sheets.discard(channel_name)
        else:
            self._sheet_cache.clear()
            self._channel_spreadsheets.clear()
            self._formatted_sheets.clear()

    def each_channel_sheet(self) -> Iterator[tuple[gspread.Spreadsheet, gspread.Worksheet]]:
        """Every channel spreadsheet in the folder, with its first worksheet."""
        query = (
            f"'{self.drive_folder_id}' in parents and "
            f"mimeType = '{SPREADSHEET_MIME}' and trashed = false"
        )
        try:
            files = (
                self._drive.files()
                .list(q=query, fields="files(id, name)", pageSize=1000)
                .execute()
                .get("files", [])
            )
        except Exception as e:
            logger.warning("Could not list channel spreadsheets: %s", e)
            return
        for meta in files:
            if not meta["name"].startswith("Slack Log - #"):
                continue
            try:
                spreadsheet = self.gc.open_by_key(meta["id"])
                yield spreadsheet, spreadsheet.sheet1
            except Exception as e:
                logger.warning("Could not open %s: %s", meta["name"], e)

    # ── 改名・URL ──

    def rename(self, channel: Channel, previous_name: str = "") -> bool:
        """Follow a channel rename so its history stays in one spreadsheet."""
        if channel.is_private:
            return self._rename_private(channel)
        return self._rename_public(channel.name, previous_name)

    def _rename_public(self, new_name: str, previous_name: str) -> bool:
        if not previous_name:
            return False
        try:
            worksheet = self.index_spreadsheet.worksheet(previous_name)
            retry(worksheet.update_title, new_name, idempotent=False)
        except gspread.exceptions.WorksheetNotFound:
            return False
        except Exception as e:
            logger.warning("Could not rename public channel tab: %s", e)
            return False
        self.forget()
        logger.info("Renamed public channel tab to #%s", new_name)
        return True

    def _find_private_spreadsheet(self, channel_id: str) -> dict | None:
        """チャンネル ID の付いた、プライベートチャンネルのスプレッドシートを探す。"""
        found = self._find_by_channel_id(channel_id)
        if not found:
            folders = (
                self._drive.files()
                .list(
                    q=(
                        f"appProperties has {{ key='{CHANNEL_ID_PROPERTY}' "
                        f"and value='{channel_id}' }} "
                        f"and '{self.private_root_id}' in parents and "
                        "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
                    ),
                    fields="files(id)",
                    pageSize=1,
                )
                .execute()
                .get("files", [])
            )
            if folders:
                found = self._find_by_channel_id(channel_id, folders[0]["id"])
        return found

    def _rename_private(self, channel: Channel) -> bool:
        channel_id, new_name = channel.id, channel.name
        found = self._find_private_spreadsheet(channel_id)
        if not found:
            return False
        new_title = channel_spreadsheet_name(new_name)
        if found["name"] != new_title:
            self._stamp_channel_id(found["id"], channel_id, new_title)
        try:
            spreadsheet = self.gc.open_by_key(found["id"])
            worksheet = spreadsheet.sheet1
            if worksheet.title != new_name:
                retry(worksheet.update_title, new_name, idempotent=False)
        except Exception as e:
            logger.warning("Could not rename the tab for %s: %s", new_name, e)
        # Caches are keyed by channel name, so the old key must go.
        self.forget()
        self._index_entries.clear()
        try:
            write_channel_index(self.index_spreadsheet, self.index_entries())
        except Exception as e:
            logger.warning("Could not refresh the channel index: %s", e)
        logger.info("Renamed the log spreadsheet to %s", new_title)
        return True

    def url_for(self, channel: Channel) -> str | None:
        """Return the spreadsheet URL for a channel, or None if it has none yet."""
        channel_name = channel.name
        if not channel.is_private:
            try:
                worksheet = self.index_spreadsheet.worksheet(channel_name)
            except gspread.exceptions.WorksheetNotFound:
                return None
            return f"{self.index_spreadsheet.url}#gid={worksheet.id}"

        if channel_name in self._channel_spreadsheets:
            return self._channel_spreadsheets[channel_name].url
        try:
            files = []
            for folder in (
                self._drive.files()
                .list(
                    q=(
                        f"'{self.private_root_id}' in parents and "
                        "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
                    ),
                    fields="files(id)",
                    pageSize=1000,
                )
                .execute()
                .get("files", [])
            ):
                files = (
                    self._drive.files()
                    .list(
                        q=(
                            f"name = '{channel_spreadsheet_name(channel_name)}' and "
                            f"'{folder['id']}' in parents and "
                            f"mimeType = '{SPREADSHEET_MIME}' and trashed = false"
                        ),
                        fields="files(id)",
                        pageSize=1,
                    )
                    .execute()
                    .get("files", [])
                )
                if files:
                    break
        except Exception as e:
            logger.warning("Could not look up the sheet for #%s: %s", channel_name, e)
            return None
        if not files:
            return None
        return f"https://docs.google.com/spreadsheets/d/{files[0]['id']}/edit"
