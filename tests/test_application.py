"""ユースケースの単体テスト。ポートはメモリ上の簡単なフェイクで置き換える。"""

from __future__ import annotations

import unittest
from typing import TYPE_CHECKING

from src.application.access import AccessService
from src.application.archive import ArchiveService
from src.application.attachments import AttachmentArchiver
from src.application.channels import ChannelResolver
from src.application.collection import CollectionService
from src.application.ports import DownloadFailedError
from src.application.renames import RenameService
from src.domain.channel import Channel
from src.domain.log_entry import Attachment
from src.domain.people import AccessChange, SlackUser
from src.domain.post import SlackPost, SourceFile

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from src.domain.log_entry import LogEntry


class Workspace:
    def __init__(self) -> None:
        self.channels = {
            "C1": Channel("C1", "general"),
            "G1": Channel("G1", "secret", is_private=True),
        }
        self.users = {
            "U1": SlackUser("U1", "Alice", "alice", "alice@example.com"),
            "U2": SlackUser("U2", "Bob", "bob", "bob@example.com"),
            "UB": SlackUser("UB", "bot", "bot", ""),
        }
        self.cache: dict[str, SlackUser] = {}
        self.history_posts: list[SlackPost] = []
        self.reply_posts: dict[str, list[SlackPost]] = {}
        self.replies_asked: list[str] = []

    def channel(self, channel_id: str) -> Channel:
        # 問い合わせに失敗したときと同じく、知らないチャンネルは公開扱い
        return self.channels.get(channel_id, Channel(channel_id, channel_id, is_private=False))

    def channel_name_of(self, channel_id: str) -> str:
        return self.channel(channel_id).name

    def cached_channel(self, channel_id: str) -> Channel | None:
        return self.channels.get(channel_id)

    def forget_channel(self, channel_id: str) -> None:
        pass

    def user(self, user_id: str) -> SlackUser:
        self.cache[user_id] = self.users[user_id]
        return self.users[user_id]

    def username_of(self, user_id: str) -> str:
        return self.user(user_id).username

    def cached_user(self, user_id: str) -> SlackUser | None:
        return self.cache.get(user_id)

    def forget_user(self, user_id: str) -> None:
        self.cache.pop(user_id, None)

    def member_emails(self, _channel_id: str) -> list[str]:
        return ["alice@example.com", "bob@example.com"]

    def forget_members(self, channel_id: str) -> None:
        pass

    def history(self, _channel: Channel, _oldest_ts: str) -> Iterator[SlackPost]:
        yield from self.history_posts

    def replies(self, _channel: Channel, parent_ts: str) -> list[SlackPost]:
        self.replies_asked.append(parent_ts)
        return self.reply_posts.get(parent_ts, [])


class Bot:
    def user_ids(self) -> tuple[str, ...]:
        return ("UB",)

    def handle(self) -> str:
        return "logbot"


class Log:
    def __init__(self) -> None:
        self.inserted = []
        self.linked = []
        self.removed_ids: list[str] | None = None
        self.recorded: set[str] = set()
        self.appended = []
        self.fail_sync = False
        self.renamed_authors = []

    def recorded_ts(self, _channel: Channel, _member_emails: list[str] | None = None) -> set[str]:
        return set(self.recorded)

    def insert(
        self, channel: Channel, entry: LogEntry, _member_emails: list[str] | None = None
    ) -> bool:
        self.inserted.append((channel, entry))
        return True

    def append_new(
        self,
        _channel: Channel,
        entries: Iterable[LogEntry],
        _member_emails: list[str] | None = None,
    ) -> tuple[int, int]:
        entries = list(entries)
        self.appended.extend(entries)
        return len(entries), 0

    def link_attachments(
        self,
        _channel: Channel,
        ts: str,
        attachments: list[Attachment],
        _member_emails: list[str] | None = None,
    ) -> None:
        self.linked.append((ts, attachments))

    def update_text(
        self, _channel: Channel, _ts: str, _text: str, _member_emails: list[str] | None = None
    ) -> bool:
        return True

    def remove(
        self, _channel: Channel, _ts: str, _member_emails: list[str] | None = None
    ) -> list[str] | None:
        return self.removed_ids

    def rename_channel(self, _channel: Channel, _previous_name: str = "") -> bool:
        return True

    def rename_author(self, old_name: str, new_name: str) -> int:
        self.renamed_authors.append((old_name, new_name))
        return 3

    def url_for(self, _channel: Channel) -> None:
        return None

    def sync_access(
        self, _channel: Channel, _member_emails: list[str], _revoke: list[str] | None = None
    ) -> AccessChange:
        if self.fail_sync:
            msg = "sheets down"
            raise RuntimeError(msg)
        return AccessChange(2, 0)


class Store:
    def __init__(self) -> None:
        self.uploaded = []
        self.trashed = []
        self.fail_upload = False

    def upload(
        self,
        channel: Channel,
        file_name: str,
        content: bytes,
        _mime_type: str,
        _member_emails: list[str] | None = None,
    ) -> Attachment:
        if self.fail_upload:
            msg = "drive down"
            raise RuntimeError(msg)
        self.uploaded.append((channel.name, file_name, content))
        return Attachment(file_name, f"https://drive.example/{file_name}")

    def trash(self, file_ids: list[str]) -> None:
        self.trashed.extend(file_ids)

    def sync_access(
        self, _channel: Channel, _member_emails: list[str], revoke: list[str] | None = None
    ) -> AccessChange:
        return AccessChange(1, len(revoke or []))

    def rename_channel(self, _channel: Channel) -> bool:
        return False


class Downloader:
    def download(self, url: str) -> bytes:
        if "broken" in url:
            msg = "404 Not Found"
            raise DownloadFailedError(msg)
        return f"bytes of {url}".encode()


def _file(name: str, size: int = 10, *, url: bool = True) -> SourceFile:
    return SourceFile(name, size, "image/png", f"https://files.slack.com/{name}" if url else None)


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Workspace()
        self.log = Log()
        self.store = Store()
        self.channels = ChannelResolver(self.workspace)
        self.attachments = AttachmentArchiver(Downloader(), self.store, max_file_size=100)
        self.archive = ArchiveService(
            self.workspace, Bot(), self.channels, self.log, self.attachments
        )
        self.collection = CollectionService(
            self.workspace, Bot(), self.channels, self.log, self.attachments
        )


class ArchiveServiceTests(Fixture):
    def test_records_with_display_name_and_readable_text(self) -> None:
        pending = self.archive.record("C1", SlackPost("1.0", "U1", "<@U2> やあ"))
        channel, entry = self.log.inserted[0]
        self.assertEqual(channel, Channel("C1", "general"))
        self.assertEqual((entry.author, entry.text), ("Alice", "@bob やあ"))
        self.assertIsNone(pending)

    def test_ignores_bookkeeping_bots_own_posts_and_authorless_posts(self) -> None:
        self.archive.record("C1", SlackPost("1.0", "U1", subtype="channel_join"))
        self.archive.record("C1", SlackPost("2.0", "UB", "収集を開始します"))
        self.archive.record("C1", SlackPost("3.0", "", "誰か"))
        self.assertEqual(self.log.inserted, [])

    def test_files_are_returned_for_later_and_then_linked(self) -> None:
        pending = self.archive.record(
            "C1", SlackPost("1.0", "U1", "資料", files=(_file("a.png"), _file("b.png", url=False)))
        )
        self.assertEqual(self.log.inserted[0][1].attachments, ())

        self.archive.attach_files(pending)
        self.assertEqual(
            self.log.linked, [("1.0", [Attachment("a.png", "https://drive.example/a.png")])]
        )

    def test_deletion_trashes_the_attachments_the_row_pointed_at(self) -> None:
        self.log.removed_ids = ["f1", "f2"]
        self.archive.apply_deletion("C1", "1.0")
        self.assertEqual(self.store.trashed, ["f1", "f2"])

    def test_deletion_of_an_unrecorded_message_trashes_nothing(self) -> None:
        self.log.removed_ids = None
        self.archive.apply_deletion("C1", "1.0")
        self.assertEqual(self.store.trashed, [])


class ChannelResolverTests(Fixture):
    def test_failed_lookup_after_seeing_a_private_channel_keeps_it_private(self) -> None:
        self.assertTrue(self.channels.lookup("G1").is_private)
        del self.workspace.channels["G1"]  # 次の問い合わせは失敗して公開扱いになる
        self.assertTrue(self.channels.lookup("G1").is_private)

    def test_known_uses_memory_only(self) -> None:
        self.channels.lookup("G1")
        self.assertEqual(
            self.channels.known("G1", "renamed"), Channel("G1", "renamed", is_private=True)
        )
        self.assertFalse(self.channels.known("C9", "random").is_private)


class AttachmentArchiverTests(Fixture):
    def test_stores_only_what_can_be_stored(self) -> None:
        with self.assertLogs("src.application.attachments", "INFO") as logs:
            stored = self.attachments.store(
                Channel("C1", "general"),
                [
                    _file("ok.png"),
                    _file("huge.bin", size=101),
                    _file("link", url=False),
                    _file("broken.png"),
                ],
            )
        self.assertEqual([a.name for a in stored], ["ok.png"])
        self.assertEqual(len(logs.records), 3)

    def test_upload_failure_is_not_fatal(self) -> None:
        self.store.fail_upload = True
        with self.assertLogs("src.application.attachments", "ERROR"):
            stored = self.attachments.store(Channel("C1", "general"), [_file("a.png")])
        self.assertEqual(stored, [])


class CollectionServiceTests(Fixture):
    def test_gathers_threads_and_skips_uploads_for_recorded_messages(self) -> None:
        self.workspace.history_posts = [
            SlackPost("3.0", "U2", "新しい", files=(_file("new.png"),)),
            SlackPost("1.0", "U1", "親", thread_ts="1.0", reply_count=1, files=(_file("old.png"),)),
            SlackPost("0.5", "UB", "bot"),
            SlackPost("0.4", "U1", "参加", subtype="channel_join"),
        ]
        self.workspace.reply_posts["1.0"] = [SlackPost("2.0", "U2", "返信", thread_ts="1.0")]

        entries = self.collection.gather(Channel("C1", "general"), "0", {"1.0"}, [], ("UB",))

        self.assertEqual(
            [(e.ts, e.thread_ts) for e in entries], [("3.0", None), ("1.0", "1.0"), ("2.0", "1.0")]
        )
        # 記録済みの 1.0 の添付は上げ直さない（Drive に複製が溜まる）
        self.assertEqual([u[1] for u in self.store.uploaded], ["new.png"])
        self.assertEqual(self.workspace.replies_asked, ["1.0"])

    def test_collect_appends_what_it_gathered(self) -> None:
        self.workspace.history_posts = [SlackPost("1.0", "U1", "a")]
        self.assertEqual(
            self.collection.collect(Channel("C1", "general"), [], "0", ("UB",)), (1, 0)
        )


class AccessServiceTests(Fixture):
    def test_drive_is_synced_even_if_the_sheet_fails(self) -> None:
        access = AccessService(self.workspace, self.channels, self.log, self.store)
        self.log.fail_sync = True
        with self.assertLogs("src.application.access", "ERROR"):
            change = access.sync("C1", revoke=["bob@example.com"])
        self.assertEqual(change, AccessChange(1, 1))

    def test_weekly_share_raises_on_failure(self) -> None:
        access = AccessService(self.workspace, self.channels, self.log, self.store)
        self.assertEqual(access.share_with_members(Channel("C1", "general"), []), 3)
        self.log.fail_sync = True
        with self.assertRaises(RuntimeError):
            access.share_with_members(Channel("C1", "general"), [])


class RenameServiceTests(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.renames = RenameService(self.workspace, self.channels, self.log, self.store)

    def test_rename_is_followed_only_for_a_cached_user(self) -> None:
        self.assertIsNone(self.renames.author_changed("U1"))  # 変更前の名前を知らない

        self.workspace.user("U1")
        self.workspace.users["U1"] = SlackUser("U1", "Alice2", "alice", "alice@example.com")
        rename = self.renames.author_changed("U1")
        self.assertEqual((rename.old_name, rename.new_name), ("Alice", "Alice2"))

        self.renames.apply_author_rename(rename)
        self.assertEqual(self.log.renamed_authors, [("Alice", "Alice2")])

    def test_unchanged_display_name_is_not_a_rename(self) -> None:
        self.workspace.user("U1")
        self.assertIsNone(self.renames.author_changed("U1"))


if __name__ == "__main__":
    unittest.main()
