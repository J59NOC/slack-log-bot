import io
import os
import unittest
import uuid

import gspread
from googleapiclient.http import MediaIoBaseUpload

from src.infrastructure.google.auth import load_credentials
from src.infrastructure.google.drive import drive_service, shared_drive_id
from src.infrastructure.settings import load_settings

INTEGRATION_ENABLED = os.environ.get("SLACK_LOG_INTEGRATION") == "1"


@unittest.skipUnless(
    INTEGRATION_ENABLED,
    "SLACK_LOG_INTEGRATION=1 を指定した実APIテストです",
)
class GoogleDriveIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = load_settings()
        self.credentials = load_credentials()
        self.drive = drive_service(self.credentials)
        self.shared_drive_id = shared_drive_id(self.drive, self.settings.google_drive_folder_id)
        self.drive.bind_drive(self.shared_drive_id)
        if not self.shared_drive_id:
            self.skipTest("共有ドライブの親フォルダが必要です")

        self.created_ids: list[str] = []
        self._outstanding_ids: list[str] = []

    def tearDown(self) -> None:
        for file_id in reversed(getattr(self, "created_ids", [])):
            self._remove(file_id)

    def _remove(self, file_id: str) -> None:
        """作ったファイルを消す。消せなければゴミ箱へ、それも駄目なら記録だけ残す。"""
        try:
            self.drive.files().delete(fileId=file_id).execute()
        except Exception:
            try:
                self.drive.files().update(fileId=file_id, body={"trashed": True}).execute()
            except Exception:
                self._outstanding_ids.append(file_id)

    def test_private_folder_and_children_use_real_drive_api(self) -> None:
        folder = (
            self.drive.files()
            .create(
                body={
                    "name": f"__slack-log-bot-integration-{uuid.uuid4().hex}__",
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [self.settings.google_drive_folder_id],
                },
                fields="id",
            )
            .execute()
        )
        folder_id = folder["id"]
        self.created_ids.append(folder_id)

        # The real Sheets client uses the same OAuth credential as Drive.
        spreadsheet = gspread.authorize(self.credentials).create(
            "__slack-log-bot-integration__", folder_id=folder_id
        )
        self.created_ids.append(spreadsheet.id)

        uploaded = (
            self.drive.files()
            .create(
                body={"name": "__slack-log-bot-integration__.txt", "parents": [folder_id]},
                media_body=MediaIoBaseUpload(
                    io.BytesIO(b"integration test"), mimetype="text/plain"
                ),
                fields="id,parents",
            )
            .execute()
        )
        self.created_ids.append(uploaded["id"])
        self.assertIn(folder_id, uploaded.get("parents", []))

        self.assertEqual(self._outstanding_ids, [])
