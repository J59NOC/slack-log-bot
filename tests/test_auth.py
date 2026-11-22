"""保存済みの Google 認証情報の読み込みと、そのエラーメッセージのテスト。

メッセージは setup.py がそのまま画面に出すので、文言まで確かめる。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from src.infrastructure.google import auth


class LoadCredentialsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = self.tmp / "drive_token.json"
        patcher = mock.patch.dict(os.environ, {"GOOGLE_DRIVE_TOKEN_FILE": str(self.path)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _store(self, **overrides: object) -> None:
        data = {
            "token": "access",
            "refresh_token": "refresh",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client",
            "client_secret": "secret",
            "scopes": list(auth.SCOPES),
            "expiry": (datetime.now(timezone.utc) + timedelta(hours=1))
            .replace(tzinfo=None)
            .isoformat(),
        }
        data.update(overrides)
        self.path.write_text(json.dumps({k: v for k, v in data.items() if v is not None}))

    def _message(self) -> str:
        with self.assertRaises(auth.CredentialsError) as caught:
            auth.load_credentials()
        return str(caught.exception)

    def test_valid_token_is_loaded(self) -> None:
        self._store()
        self.assertEqual(auth.load_credentials().token, "access")

    def test_missing_file(self) -> None:
        self.assertEqual(
            self._message(),
            f"Google の認証ファイル {self.path} がありません。\n"
            f"  uv run setup.py を実行してセットアップしてください。",
        )

    def test_unreadable_file(self) -> None:
        self.path.write_text("{not json")
        message = self._message()
        self.assertTrue(message.startswith(f"{self.path} を読み込めません: "), message)
        self.assertTrue(message.endswith("\n  uv run setup.py で作り直してください。"), message)

    def test_missing_scope(self) -> None:
        self._store(scopes=["https://www.googleapis.com/auth/drive"])
        self.assertEqual(
            self._message(),
            f"{self.path} に必要な権限がありません: "
            f"https://www.googleapis.com/auth/spreadsheets\n"
            f"  uv run setup.py を実行して認証をやり直してください。",
        )

    def test_expired_without_refresh_token(self) -> None:
        self._store(refresh_token="", expiry="2000-01-01T00:00:00")
        self.assertEqual(
            self._message(),
            f"{self.path} の有効期限が切れており、更新もできません。\n"
            f"  uv run setup.py を実行して認証をやり直してください。",
        )

    def test_refresh_failure(self) -> None:
        self._store(expiry="2000-01-01T00:00:00")
        with mock.patch.object(
            auth.Credentials, "refresh", side_effect=RuntimeError("invalid_grant")
        ):
            message = self._message()
        self.assertEqual(
            message,
            "Google 認証トークンの更新に失敗しました: invalid_grant\n"
            "  OAuth同意画面が「テスト中」のままだとトークンは7日で失効します。\n"
            "  uv run setup.py で再認証するか、同意画面を「本番環境」に切り替えてください。",
        )


if __name__ == "__main__":
    unittest.main()
