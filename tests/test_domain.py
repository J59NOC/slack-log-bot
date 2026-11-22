"""ドメイン層の規則の単体テスト。外部 API もフェイクも使わない。"""

import unittest

from src.domain.channel import Channel, PrivateChannels
from src.domain.log_entry import Attachment, LogEntry, arrange_for_log
from src.domain.people import AccessChange
from src.domain.post import SlackPost, SourceFile
from src.domain.text import render_slack_text


class PrivateChannelsTests(unittest.TestCase):
    def test_private_stays_private_even_if_later_reported_public(self) -> None:
        # Slack への問い合わせが失敗すると公開扱いで返ってくる。それを信じると
        # プライベートチャンネルのログが公開側に書かれてしまう。
        private = PrivateChannels()
        self.assertTrue(private.classify("G1", "secret", reported_private=True).is_private)
        self.assertTrue(private.classify("G1", "G1", reported_private=False).is_private)
        self.assertIn("G1", private)

    def test_unknown_channel_is_public(self) -> None:
        self.assertEqual(
            PrivateChannels().classify("C1", "general"), Channel("C1", "general", is_private=False)
        )

    def test_channel_without_id_is_never_remembered(self) -> None:
        private = PrivateChannels()
        self.assertFalse(private.classify("", "x", reported_private=True).is_private)
        self.assertNotIn("", private)


class LogEntryTests(unittest.TestCase):
    def test_reply_has_a_thread_ts_other_than_its_own(self) -> None:
        self.assertTrue(LogEntry("a", "", "2.0", "1.0").is_reply)
        self.assertFalse(LogEntry("a", "", "1.0", "1.0").is_reply)
        self.assertFalse(LogEntry("a", "", "1.0").is_reply)

    def test_thread_key(self) -> None:
        self.assertEqual(LogEntry("a", "", "2.0", "1.0").thread_key, "1.0")
        self.assertEqual(LogEntry("a", "", "1.0").thread_key, "1.0")


class ArrangeForLogTests(unittest.TestCase):
    def test_threads_are_written_parent_first_in_posting_order(self) -> None:
        entries = [
            LogEntry("a", "後の親", "3.0"),
            LogEntry("b", "返信2", "2.5", "1.0"),
            LogEntry("a", "親", "1.0", "1.0"),
            LogEntry("c", "返信1", "2.0", "1.0"),
        ]
        ordered, skipped = arrange_for_log(entries, set())
        self.assertEqual([e.ts for e in ordered], ["1.0", "2.0", "2.5", "3.0"])
        self.assertEqual(skipped, 0)

    def test_skips_recorded_and_duplicated_within_the_batch(self) -> None:
        # スレッドからチャンネルへも投稿された返信は、履歴と返信の両方から届く
        entries = [
            LogEntry("a", "", "1.0"),
            LogEntry("b", "", "2.0", "1.0"),
            LogEntry("b", "", "2.0", "1.0"),
        ]
        ordered, skipped = arrange_for_log(entries, {"1.0"})
        self.assertEqual([e.ts for e in ordered], ["2.0"])
        self.assertEqual(skipped, 2)

    def test_does_not_modify_the_recorded_set(self) -> None:
        recorded = {"1.0"}
        arrange_for_log([LogEntry("a", "", "2.0")], recorded)
        self.assertEqual(recorded, {"1.0"})


class RenderSlackTextTests(unittest.TestCase):
    def render(self, text: str) -> str:
        return render_slack_text(
            text,
            {"U1": "alice"}.get,
            {"C1": "general"}.get,
        )

    def test_mentions_become_names(self) -> None:
        self.assertEqual(self.render("<@U1> と <#C1>"), "@alice と #general")

    def test_labels_are_used_without_lookup(self) -> None:
        self.assertEqual(self.render("<@U9|ghost> <#C9|random>"), "@ghost #random")

    def test_special_mentions_and_links(self) -> None:
        self.assertEqual(
            self.render("<!here> <!subteam^S1|@devs> <https://x.example|リンク> <mailto:a@b.c>"),
            "@here @devs リンク mailto:a@b.c",
        )

    def test_unescapes_entities(self) -> None:
        self.assertEqual(self.render("a &amp; &lt;b&gt;"), "a & <b>")

    def test_empty_text(self) -> None:
        self.assertEqual(self.render(""), "")


class SlackPostTests(unittest.TestCase):
    def test_bookkeeping_subtypes(self) -> None:
        for subtype in ("bot_message", "channel_join", "channel_leave"):
            self.assertTrue(SlackPost("1", subtype=subtype).is_bookkeeping)
        self.assertFalse(SlackPost("1", subtype="thread_broadcast").is_bookkeeping)
        self.assertFalse(SlackPost("1").is_bookkeeping)

    def test_is_by(self) -> None:
        self.assertTrue(SlackPost("1", user_id="UBOT").is_by(("UBOT",)))
        self.assertFalse(SlackPost("1", user_id="U1").is_by(("UBOT",)))


class SourceFileTests(unittest.TestCase):
    def test_size_limit(self) -> None:
        self.assertTrue(SourceFile("a", size=11).exceeds(10))
        self.assertFalse(SourceFile("a", size=10).exceeds(10))

    def test_external_links_are_not_downloadable(self) -> None:
        self.assertFalse(SourceFile("link").is_downloadable)
        self.assertTrue(SourceFile("a", download_url="https://files.slack.com/a").is_downloadable)

    def test_stored_name(self) -> None:
        self.assertEqual(SourceFile(None).stored_name, "unknown_file")
        self.assertEqual(SourceFile("a.png").stored_name, "a.png")


class ValueTests(unittest.TestCase):
    def test_access_change_adds_up(self) -> None:
        self.assertEqual(AccessChange(1, 2) + AccessChange(3, 4), AccessChange(4, 6))

    def test_attachment_unpacks_as_name_and_url(self) -> None:
        name, _url = Attachment("a.png", "https://drive.google.com/file/d/x/view")
        self.assertEqual(name, "a.png")


if __name__ == "__main__":
    unittest.main()
