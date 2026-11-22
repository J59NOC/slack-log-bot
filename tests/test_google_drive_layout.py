import unittest

# drive.py は設定（.env）を読まないので、環境変数なしで import できる
from src.infrastructure.google.drive import (
    FOLDER_MIME,
    ensure_folder,
    move_into_folder,
)


class FakeRequest:
    def __init__(self, value: object) -> None:
        self.value = value

    def execute(self) -> object:
        return self.value


class FakeFiles:
    def __init__(self, listed: list[dict] | None = None, parents: list[str] | None = None) -> None:
        self.listed = listed or []
        self.parents = parents or []
        self.created = []
        self.updated = []

    def list(self, **_: object) -> FakeRequest:
        return FakeRequest({"files": self.listed})

    def create(self, **kwargs: object) -> FakeRequest:
        self.created.append(kwargs)
        return FakeRequest({"id": "created-folder"})

    def get(self, **_: object) -> FakeRequest:
        return FakeRequest({"parents": self.parents})

    def update(self, **kwargs: object) -> FakeRequest:
        self.updated.append(kwargs)
        return FakeRequest({"id": kwargs["fileId"]})


class FakeDrive:
    def __init__(self, files: FakeFiles) -> None:
        self._files = files

    def files(self) -> FakeFiles:
        return self._files


class GoogleDriveLayoutTests(unittest.TestCase):
    def test_ensure_folder_reuses_existing_child(self) -> None:
        files = FakeFiles(listed=[{"id": "public-id"}])

        folder_id = ensure_folder(FakeDrive(files), "public", "root-id")

        self.assertEqual(folder_id, "public-id")
        self.assertEqual(files.created, [])

    def test_ensure_folder_creates_missing_child(self) -> None:
        files = FakeFiles()

        folder_id = ensure_folder(FakeDrive(files), "private", "root-id")

        self.assertEqual(folder_id, "created-folder")
        self.assertEqual(files.created[0]["body"]["mimeType"], FOLDER_MIME)
        self.assertEqual(files.created[0]["body"]["parents"], ["root-id"])

    def test_move_into_folder_does_nothing_when_already_inside(self) -> None:
        files = FakeFiles(parents=["public-id"])

        moved = move_into_folder(FakeDrive(files), "sheet-id", "public-id")

        self.assertFalse(moved)
        self.assertEqual(files.updated, [])

    def test_move_into_folder_replaces_old_parent(self) -> None:
        files = FakeFiles(parents=["old-parent"])

        moved = move_into_folder(FakeDrive(files), "sheet-id", "public-id")

        self.assertTrue(moved)
        self.assertEqual(files.updated[0]["addParents"], "public-id")
        self.assertEqual(files.updated[0]["removeParents"], "old-parent")


if __name__ == "__main__":
    unittest.main()
