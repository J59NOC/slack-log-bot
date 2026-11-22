"""部品の組み立て（コンポジションルート）。

外部のクライアント（Slack・Google）を作ってインフラ層の実装に渡し、ユース
ケースに注入する。どの実装を使うかを知っているのはここだけ。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import gspread
from slack_sdk import WebClient

from src.application.access import AccessService
from src.application.archive import ArchiveService
from src.application.attachments import AttachmentArchiver
from src.application.channels import ChannelResolver
from src.application.collection import CollectionService
from src.application.renames import RenameService
from src.infrastructure.google import auth
from src.infrastructure.google.attachment_store import DriveAttachmentStore
from src.infrastructure.google.drive import DriveService, drive_service
from src.infrastructure.google.sheets.repository import SheetsChannelLog
from src.infrastructure.google.sheets.workbooks import ChannelWorkbooks
from src.infrastructure.slack.client import SlackBotIdentity, install_retry_handlers
from src.infrastructure.slack.files import SlackFileDownloader
from src.infrastructure.slack.workspace import SlackWorkspace

if TYPE_CHECKING:
    from src.infrastructure.settings import Settings


@dataclass(frozen=True)
class Services:
    """組み立て済みのユースケースと、それが使う部品。"""

    workspace: SlackWorkspace
    bot: SlackBotIdentity
    channels: ChannelResolver
    log: SheetsChannelLog
    store: DriveAttachmentStore
    archive: ArchiveService
    collection: CollectionService
    access: AccessService
    renames: RenameService


@dataclass(frozen=True)
class GoogleClients:
    """Google の API クライアント（Sheets と、用途ごとの Drive）。"""

    sheets: gspread.Client
    # Drive のクライアントは使う側ごとに分ける。googleapiclient の通信（httplib2）は
    # スレッドセーフではないので、それぞれが自分のロックの中でだけ使う
    # （ログ側は書き込みのロック、添付側は DriveAttachmentStore のロック）。
    log_drive: DriveService
    attachment_drive: DriveService


def connect_google() -> GoogleClients:
    """Google の認証情報で、Sheets と Drive のクライアントを作る。"""
    # Sheets and Drive share one OAuth2 user credential; see google/auth.py.
    credentials = auth.load_credentials()
    return GoogleClients(
        sheets=gspread.authorize(credentials),
        log_drive=drive_service(credentials),
        attachment_drive=drive_service(credentials),
    )


def slack_web_client(settings: Settings) -> WebClient:
    """CLI 用の Slack クライアント。"""
    client = WebClient(token=settings.slack_bot_token)
    install_retry_handlers(client)
    return client


def wire(settings: Settings, slack_client: WebClient, google: GoogleClients) -> Services:
    """作成済みのクライアントから、ユースケース一式を組み立てる。

    保存先のフォルダや索引スプレッドシートの用意（API 呼び出し）もここで行う。
    """
    workbooks = ChannelWorkbooks.open(
        google.sheets,
        google.log_drive,
        settings.google_drive_folder_id,
        settings.google_spreadsheet_id,
    )
    log = SheetsChannelLog.create(workbooks)
    store = DriveAttachmentStore.open(google.attachment_drive, settings.google_drive_folder_id)

    workspace = SlackWorkspace(slack_client)
    bot = SlackBotIdentity(slack_client)
    channels = ChannelResolver(workspace)
    attachments = AttachmentArchiver(
        SlackFileDownloader(settings.slack_bot_token),
        store,
        settings.max_file_size,
    )
    return Services(
        workspace=workspace,
        bot=bot,
        channels=channels,
        log=log,
        store=store,
        archive=ArchiveService(workspace, bot, channels, log, attachments),
        collection=CollectionService(workspace, bot, channels, log, attachments),
        access=AccessService(workspace, channels, log, store),
        renames=RenameService(workspace, channels, log, store),
    )


def services_for_cli(settings: Settings) -> Services:
    """CLI（週次収集・バックフィル）用に、本物のクライアントで一式を組み立てる。"""
    client = slack_web_client(settings)
    return wire(settings, client, connect_google())
