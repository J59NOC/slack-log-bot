"""添付ファイルを Slack から取得して保管先に保存する。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.application.ports import DownloadFailedError, FileDownloader

if TYPE_CHECKING:
    from collections.abc import Iterable

    from src.domain.channel import Channel
    from src.domain.log_entry import Attachment
    from src.domain.post import SourceFile
    from src.domain.repositories import AttachmentStore

logger = logging.getLogger(__name__)


class AttachmentArchiver:
    """添付ファイルを Slack から取得し、保管先に保存する。"""

    def __init__(
        self, downloader: FileDownloader, store: AttachmentStore, max_file_size: int
    ) -> None:
        """取得に使うもの・保管先・保存する上限サイズ（バイト）を受け取る。"""
        self._downloader = downloader
        self._store = store
        self._max_file_size = max_file_size

    def store(
        self,
        channel: Channel,
        files: Iterable[SourceFile],
        member_emails: list[str] | None = None,
    ) -> list[Attachment]:
        """保存できたものだけを、名前とリンクの組で返す。"""
        stored = []
        for source in files:
            attachment = self._store_one(channel, source, member_emails)
            if attachment:
                stored.append(attachment)
        return stored

    def _store_one(
        self,
        channel: Channel,
        source: SourceFile,
        member_emails: list[str] | None,
    ) -> Attachment | None:
        if source.exceeds(self._max_file_size):
            logger.warning("Skipping large file: %s (%s bytes)", source.name, source.size)
            return None

        if not source.is_downloadable:
            logger.info("Skipping external Slack file: %s", source.name)
            return None

        try:
            content = self._downloader.download(source.download_url)
        except DownloadFailedError as e:
            logger.error("Failed to download from Slack: %s", e)
            return None

        try:
            return self._store.upload(
                channel,
                source.stored_name,
                content,
                source.mimetype,
                member_emails,
            )
        except Exception as e:
            logger.error("Failed to upload to Drive: %s", e)
            return None

    def discard(self, file_ids: list[str]) -> None:
        """削除されたメッセージの添付をゴミ箱へ移す。"""
        self._store.trash(file_ids)
