"""保守用スクリプト（scripts/）の、判断を受け持つ関数のテスト。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

# `python -m unittest tests.test_scripts` でも fakes を import できるように
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FOLDER_MIME, SHEET_MIME, CallLog, DriveWorld, FakeGspread
from src.infrastructure.google.attachment_store import attachment_folders
from src.infrastructure.google.drive import wrap_drive
from src.infrastructure.google.sheets.layout import HEADER_ROW
from src.infrastructure.google.sheets.workbooks import log_spreadsheet_files

# スクリプトは import 時にログの設定をする。テストの出力に混ざらないよう止めておく
with mock.patch("logging.basicConfig"):
    from scripts import cleanup_duplicate_files, relink_attachments

URL = "https://drive.google.com/file/d/{}/view"
ID_A = "a" * 25
ID_B = "b" * 25
ROOT = "root"


class RemovableCopiesTests(unittest.TestCase):
    def test_keeps_the_linked_copy(self) -> None:
        files = [
            {"id": "1", "name": "x.png"},
            {"id": "2", "name": "x.png"},
            {"id": "3", "name": "x.png"},
        ]
        removable = cleanup_duplicate_files.removable_copies(files, linked={"2"})
        self.assertEqual([f["id"] for f in removable], ["1", "3"])

    def test_keeps_the_oldest_when_nothing_is_linked(self) -> None:
        files = [{"id": "1", "name": "x.png"}, {"id": "2", "name": "x.png"}]
        removable = cleanup_duplicate_files.removable_copies(files, linked=set())
        self.assertEqual([f["id"] for f in removable], ["2"])

    def test_single_copies_are_left_alone(self) -> None:
        files = [{"id": "1", "name": "x.png"}, {"id": "2", "name": "y.png"}]
        self.assertEqual(cleanup_duplicate_files.removable_copies(files, linked=set()), [])

    def test_google_files_are_never_removable(self) -> None:
        # private/#チャンネル/ には、そのチャンネルのスプレッドシートも入っている
        files = [
            {"id": "1", "name": "Slack Log - #x", "mimeType": SHEET_MIME},
            {"id": "2", "name": "Slack Log - #x", "mimeType": SHEET_MIME},
            {"id": "3", "name": "x.png", "mimeType": "image/png"},
        ]
        self.assertEqual(cleanup_duplicate_files.removable_copies(files, linked=set()), [])


class CurrentLayoutTests(unittest.TestCase):
    """今の構成（public/ の索引、private/#チャンネル/）と、以前の構成（ルート直下）を見ること。"""

    def setUp(self) -> None:
        self.world = DriveWorld(CallLog(), ROOT)
        self.drive = wrap_drive(self.world.service())
        self.gc = FakeGspread(self.world.log, self.world)
        self.ids: dict[str, str] = {}

        public = self._folder("public", ROOT)
        index = self.gc.add_spreadsheet("Slack ログ 索引", public)
        index.sheets[0].title = "📖 このシートについて"
        index.sheets[0].col_count = 2
        general = self._folder("#general", self._folder("添付ファイル", public))
        # リンクされているのは新しい方。古い方を残す既定の動きでは守れない
        self._file("public-copy", "x.png", general)
        self._file("public", "x.png", general)
        self._log_tab(index.add_worksheet("general", rows=1000, cols=6), "public")

        secret = self._folder("#secret", self._folder("private", ROOT))
        self._file("private-copy", "y.pdf", secret)
        self._file("private", "y.pdf", secret)
        private_ss = self.gc.add_spreadsheet("Slack Log - #secret", secret)
        self._log_tab(private_ss.sheets[0], "private")

        self._file("legacy", "z.txt", self._folder("#old", self._folder("添付ファイル", ROOT)))
        legacy_ss = self.gc.add_spreadsheet("Slack Log - #old", ROOT)
        self._log_tab(legacy_ss.sheets[0], "legacy", rich=False)
        self.gc.add_spreadsheet("メモ", ROOT)

    def _folder(self, name: str, parent: str) -> str:
        return self.world.add_file(self.world.new_id(), name, FOLDER_MIME, [parent])["id"]

    def _file(self, key: str, name: str, parent: str) -> None:
        self.ids[key] = self.world.add_file(self.world.new_id(), name, "image/png", [parent])["id"]

    def _log_tab(self, worksheet: object, key: str, *, rich: bool = True) -> None:
        """ログのタブにして、self.ids[key] のファイルへのリンクを1行入れる。

        今の bot はファイル名を表示し、URL はリッチテキストのリンクにだけ持つ
        （rich=True）。以前の行は URL を文字のまま持っている（rich=False）。
        """
        url = URL.format(self.ids[key])
        cell = "x.png" if rich else url
        worksheet.values = [HEADER_ROW, ["", "Alice", "", cell, "1", ""]]
        if rich:
            worksheet.runs[(2, 4)] = [{"startIndex": 0, "format": {"link": {"uri": url}}}]

    def test_log_spreadsheets_cover_public_private_and_legacy(self) -> None:
        names = {f["name"] for f in log_spreadsheet_files(self.drive, ROOT)}
        self.assertEqual(names, {"Slack ログ 索引", "Slack Log - #secret", "Slack Log - #old"})

    def test_attachment_folders_cover_public_private_and_legacy(self) -> None:
        names = {f["name"] for f in attachment_folders(self.drive, ROOT)}
        self.assertEqual(
            names, {"public/添付ファイル/#general", "private/#secret", "添付ファイル/#old"}
        )

    def test_links_are_read_from_every_log_spreadsheet(self) -> None:
        linked = cleanup_duplicate_files.linked_file_ids(self.gc, self.drive, ROOT)
        self.assertEqual(linked, {self.ids["public"], self.ids["private"], self.ids["legacy"]})

    def test_only_unlinked_copies_are_removable(self) -> None:
        linked = cleanup_duplicate_files.linked_file_ids(self.gc, self.drive, ROOT)
        removable = []
        for folder in attachment_folders(self.drive, ROOT):
            files = (
                self.drive.files()
                .list(
                    q=f"'{folder['id']}' in parents and trashed = false",
                    fields="files(id, name, mimeType, size, createdTime)",
                )
                .execute()["files"]
            )
            removable += cleanup_duplicate_files.removable_copies(files, linked)
        self.assertEqual(
            sorted(f["id"] for f in removable),
            sorted([self.ids["public-copy"], self.ids["private-copy"]]),
        )

    def test_nothing_is_created_in_an_empty_folder(self) -> None:
        world = DriveWorld(CallLog(), ROOT)
        drive = wrap_drive(world.service())
        self.assertEqual(log_spreadsheet_files(drive, ROOT), [])
        self.assertEqual(attachment_folders(drive, ROOT), [])
        self.assertEqual(list(world.files), [ROOT])


class RelinkTests(unittest.TestCase):
    def test_pending_rows_are_those_still_holding_a_url(self) -> None:
        values = [
            ["日時", "表示名", "メッセージ", "添付ファイル", "メッセージTS", "スレッドTS"],
            ["", "", "", URL.format(ID_A) + "\n" + URL.format(ID_B), "1", ""],
            ["", "", "", "x.png", "2", ""],
            ["", "", ""],
        ]
        self.assertEqual(relink_attachments.pending_rows(values), [(2, [ID_A, ID_B])])

    def test_files_without_a_name_keep_their_url(self) -> None:
        requests = relink_attachments.relink_requests(
            7, [(2, [ID_A, ID_B]), (3, [ID_B])], {ID_A: "a.png"}
        )
        self.assertEqual(len(requests), 1)
        cell = requests[0]["updateCells"]
        self.assertEqual(cell["start"]["rowIndex"], 1)
        self.assertEqual(cell["rows"][0]["values"][0]["userEnteredValue"]["stringValue"], "a.png")


if __name__ == "__main__":
    unittest.main()
