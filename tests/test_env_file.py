"""セットアップが書き出す .env のテスト。"""

import shutil
import tempfile
import unittest
from pathlib import Path

from src.infrastructure.env_file import EnvFile

ROOT = Path(__file__).resolve().parents[1]


class EnvFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_first_write_copies_the_real_template_with_its_japanese_comments(self) -> None:
        # .env.example は日本語のコメントを含む。既定の文字コードで読むと、
        # Windows（cp932）では UnicodeDecodeError で止まっていた。
        env = EnvFile(str(self.tmp / ".env"), template=str(ROOT / ".env.example"))
        env.write({"SLACK_BOT_TOKEN": "xoxb-1-2-3", "GOOGLE_DRIVE_FOLDER_ID": "folder-id"})

        text = (self.tmp / ".env").read_text(encoding="utf-8")
        self.assertIn("# Bot User OAuth Token (Install App 画面)", text)
        self.assertIn("SLACK_BOT_TOKEN=xoxb-1-2-3\n", text)
        self.assertIn("GOOGLE_DRIVE_FOLDER_ID=folder-id\n", text)
        self.assertEqual(env.read()["SLACK_BOT_TOKEN"], "xoxb-1-2-3")

    def test_rewrite_keeps_comments_and_order_and_appends_new_keys(self) -> None:
        path = self.tmp / ".env"
        path.write_text("# トークン\nB=1\nA=2\n", encoding="utf-8")
        env = EnvFile(str(path), template=str(self.tmp / "missing.example"))

        env.write({"A": "20", "C": "3"})

        self.assertEqual(path.read_text(encoding="utf-8"), "# トークン\nB=1\nA=20\nC=3\n")

    def test_read_of_missing_file_is_empty(self) -> None:
        self.assertEqual(EnvFile(str(self.tmp / "none")).read(), {})


if __name__ == "__main__":
    unittest.main()
