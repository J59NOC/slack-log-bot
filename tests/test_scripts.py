"""保守用スクリプト（scripts/）の、判断を受け持つ関数のテスト。"""

from __future__ import annotations

import unittest
from unittest import mock

# スクリプトは import 時にログの設定をする。テストの出力に混ざらないよう止めておく
with mock.patch("logging.basicConfig"):
    from scripts import cleanup_duplicate_files, relink_attachments

URL = "https://drive.google.com/file/d/{}/view"
ID_A = "a" * 25
ID_B = "b" * 25


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
