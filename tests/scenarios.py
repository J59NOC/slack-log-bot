"""シナリオテストで流すイベント列と、その前提になる Slack / Drive の状態。

ドライバ（test_scenarios.py）はここに書かれた手順を順に実行し、フェイクに
記録された API 呼び出しをゴールデンファイルと比べる。手順はデータとして
書いてあり、bot の内部構造には依存しない。
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from fakes import CallLog, DriveWorld, FakeGspread, FakeSlackClient, FakeWorksheet

if TYPE_CHECKING:
    from fakes import FakeSpreadsheet

ROOT_FOLDER_ID = "rootfolder000000000000000"
SHARED_DRIVE_ID = "shareddrive0000000000000"
HEADER = ["日時", "表示名", "メッセージ", "添付ファイル", "メッセージTS", "スレッドTS"]

USERS = {
    "U1": {
        "id": "U1",
        "name": "alice",
        "real_name": "Alice A",
        "profile": {"display_name": "Alice", "real_name": "Alice A", "email": "alice@example.com"},
    },
    "U2": {
        "id": "U2",
        "name": "bob",
        "real_name": "Bob B",
        "profile": {"display_name": "", "real_name": "Bob B", "email": "Bob@Example.com"},
    },
    "U3": {
        "id": "U3",
        "name": "carol",
        "profile": {"display_name": "Carol", "email": "carol@example.com"},
    },
    "U4": {"id": "U4", "name": "dave", "profile": {"display_name": "Dave"}},
    "UBOT": {"id": "UBOT", "name": "logbot", "profile": {"display_name": "logbot"}},
}

CHANNELS = {
    "C1": {"id": "C1", "name": "general", "is_private": False, "is_member": True},
    "G1": {"id": "G1", "name": "secret", "is_private": True, "is_member": True},
    "G2": {"id": "G2", "name": "mpim-ish", "is_group": True, "is_member": True},
    "C9": {"id": "C9", "name": "random", "is_private": False, "is_member": False},
}

MEMBERS = {
    "C1": ["U1", "U2", "U4", "UBOT"],
    "G1": ["U1", "U2", "UBOT"],
    "G2": ["U1", "UBOT"],
    "C9": ["U3"],
}


def _file(name: str, size: int = 10, mime: str = "image/png", *, url: bool = True) -> dict:
    info = {"name": name, "size": size, "mimetype": mime}
    if url:
        info["url_private_download"] = f"https://files.slack.com/{name}"
    return info


PARENT_TS = "1700000300.000100"

HISTORY = {
    "C1": [
        {
            "ts": PARENT_TS,
            "thread_ts": PARENT_TS,
            "user": "U2",
            "text": "新しい投稿 <@U1>",
            "reply_count": 3,
        },
        {"ts": "1700000290.000100", "user": "UBOT", "text": "収集を開始します"},
        {
            "ts": "1700000280.000100",
            "user": "U3",
            "subtype": "channel_join",
            "text": "<@U3> has joined",
        },
        {"ts": "1700000270.000100", "user": "U1", "text": "添付あり", "files": [_file("a.png")]},
        {
            "ts": "1700000265.000100",
            "user": "U2",
            "subtype": "thread_broadcast",
            "thread_ts": PARENT_TS,
            "text": "ブロードキャスト",
        },
        {"ts": "1700000260.000100", "text": "作者のいない投稿"},
        {
            "ts": "1700000000.000100",
            "user": "U1",
            "text": "既存の投稿",
            "files": [_file("old.png")],
        },
        {"ts": "1700000250.000100", "user": "B1", "subtype": "bot_message", "text": "webhook"},
    ],
    "G1": [
        {
            "ts": "1700000500.000100",
            "user": "U1",
            "text": "秘密の話",
            "files": [_file("secret.txt", mime="text/plain")],
        },
        {"ts": "1700000400.000100", "user": "U2", "text": "前の話"},
    ],
    "G2": [
        {"ts": "1700000600.000100", "user": "U1", "text": "グループDM風"},
    ],
}

REPLIES = {
    ("C1", PARENT_TS): [
        HISTORY["C1"][0],
        {
            "ts": "1700000265.000100",
            "user": "U2",
            "thread_ts": PARENT_TS,
            "text": "ブロードキャスト",
        },
        {
            "ts": "1700000310.000100",
            "user": "U1",
            "thread_ts": PARENT_TS,
            "text": "スレ返信\n" + "あ" * 80,
            "files": [_file("b.txt", mime="text/plain")],
        },
        {"ts": "1700000320.000100", "user": "UBOT", "thread_ts": PARENT_TS, "text": "bot の返信"},
    ],
}


class World:
    """1 シナリオ分の外部状態。"""

    def __init__(self, *, shared_drive: bool = False) -> None:
        self.log = CallLog()
        self.drive = DriveWorld(self.log, ROOT_FOLDER_ID, SHARED_DRIVE_ID if shared_drive else None)
        self.gc = FakeGspread(self.log, self.drive)
        self.slack = FakeSlackClient(self.log)
        self.slack.users = copy.deepcopy(USERS)
        self.slack.channels = copy.deepcopy(CHANNELS)
        self.slack.members = copy.deepcopy(MEMBERS)
        self.slack.history = copy.deepcopy(HISTORY)
        self.slack.replies = copy.deepcopy(REPLIES)

    def seed_tab(
        self, ss: FakeSpreadsheet, title: str, rows: list[list[str]], sheet_id: int
    ) -> FakeWorksheet:
        ws = FakeWorksheet(ss, sheet_id, title, rows=1000, cols=len(HEADER))
        ws.values = [list(r) for r in rows]
        ss.sheets.append(ws)
        return ws

    def seed_legacy_sheet(self) -> None:
        """旧レイアウトでルート直下に置かれていたチャンネル別スプレッドシート。"""
        ss = self.gc.add_spreadsheet("Slack Log - #legacy", ROOT_FOLDER_ID)
        ss.sheets[0].title = "legacy"
        ss.sheets[0].values = [
            HEADER,
            ["2023-11-14 22:13:20", "Alice", "昔の投稿", "", "1699967600.000100", ""],
            [
                "2023-11-14 22:13:30",
                "Bob B",
                "昔の返信",
                "",
                "1699967610.000100",
                "1699967600.000100",
            ],
            ["2023-11-14 22:13:40", "Alice", "もう一つ", "", "1699967620.000100", ""],
        ]

    def seed_public_index(self) -> None:
        """既に運用中の公開側（public フォルダ・索引・general タブ）。"""
        public = self.drive.new_id()
        self.drive.add_file(
            public, "public", "application/vnd.google-apps.folder", [ROOT_FOLDER_ID]
        )
        ss = self.gc.add_spreadsheet("Slack ログ 索引", public)
        self.seed_tab(
            ss,
            "general",
            [
                HEADER,
                ["2023-11-15 07:13:20", "Alice", "既存の投稿", "", "1700000000.000100", ""],
            ],
            sheet_id=7,
        )


def _msg(channel: str, user: str, ts: str, text: str, **extra: object) -> dict:
    return {"type": "message", "channel": channel, "user": user, "ts": ts, "text": text, **extra}


def _set_members(channel: str, members: list[str]) -> tuple[str, tuple[str, str, list[str]]]:
    return ("set", ("members", channel, members))


REALTIME_STEPS = [
    (
        "event",
        "message",
        _msg(
            "C1",
            "U1",
            "1700000000.000100",
            "<@U2> こんにちは <#C1> <#C9|random> <!here> <https://example.com|リンク> "
            "<mailto:a@example.com> &amp; &lt;tag&gt; <@U9|ghost> <!subteam^S1|@devs> "
            "<!date^1700000000^{date}|昨日> <@U404>",
        ),
    ),
    (
        "event",
        "message",
        _msg(
            "C1",
            "U2",
            "1700000010.000100",
            "返信1",
            thread_ts="1700000000.000100",
        ),
    ),
    (
        "event",
        "message",
        _msg(
            "C1",
            "U1",
            "1700000020.000100",
            "長い返信\n" + "あ" * 80,
            thread_ts="1700000000.000100",
        ),
    ),
    (
        "event",
        "message",
        _msg(
            "C1",
            "U2",
            "1700000030.000100",
            "ファイルを送ります",
            files=[
                _file("report.pdf", mime="application/pdf"),
                _file(
                    "外部リンク", size=0, mime="application/vnd.google-apps.spreadsheet", url=False
                ),
                _file("huge.bin", size=60 * 1024 * 1024),
                _file("broken.png"),
                _file("photo.jpg", mime="image/jpeg"),
            ],
        ),
    ),
    (
        "event",
        "message",
        _msg(
            "C1",
            "U1",
            "1700000040.000100",
            "親が無い返信",
            thread_ts="1699999999.000100",
        ),
    ),
    (
        "event",
        "message",
        _msg(
            "C1",
            "U1",
            "1700000050.000100",
            "続けて投稿",
        ),
    ),
    # 記録しないもの
    (
        "event",
        "message",
        {**_msg("C1", "B1", "1700000060.000100", "webhook"), "subtype": "bot_message"},
    ),
    ("event", "message", _msg("C1", "UBOT", "1700000061.000100", "bot 自身")),
    (
        "event",
        "message",
        {**_msg("C1", "U3", "1700000062.000100", "joined"), "subtype": "channel_join"},
    ),
    (
        "event",
        "message",
        {"type": "message", "channel": "C1", "ts": "1700000063.000100", "text": "no user"},
    ),
    ("event", "message", _msg("C1", "U1", "1700000000.000100", "重複した配送")),
    # 編集
    (
        "event",
        "message",
        {
            "type": "message",
            "subtype": "message_changed",
            "channel": "C1",
            "message": {"ts": "1700000000.000100", "user": "U1", "text": "編集後 <@U2>"},
        },
    ),
    (
        "event",
        "message",
        {
            "type": "message",
            "subtype": "message_changed",
            "channel": "C1",
            "message": {"ts": "1700000050.000100", "user": "U1", "text": "編集後\n" + "い" * 200},
        },
    ),
    (
        "event",
        "message",
        {
            "type": "message",
            "subtype": "message_changed",
            "channel": "C1",
            "message": {"ts": "1700000061.000100", "user": "UBOT", "text": "bot の編集"},
        },
    ),
    (
        "event",
        "message",
        {
            "type": "message",
            "subtype": "message_changed",
            "channel": "C1",
            "message": {"ts": "1700009999.000100", "user": "U1", "text": "記録外の編集"},
        },
    ),
    # 削除
    (
        "event",
        "message",
        {
            "type": "message",
            "subtype": "message_deleted",
            "channel": "C1",
            "deleted_ts": "1700000030.000100",
        },
    ),
    (
        "event",
        "message",
        {
            "type": "message",
            "subtype": "message_deleted",
            "channel": "C1",
            "previous_message": {"ts": "1700000010.000100"},
        },
    ),
    (
        "event",
        "message",
        {
            "type": "message",
            "subtype": "message_deleted",
            "channel": "C1",
            "deleted_ts": "1700009999.000100",
        },
    ),
    ("event", "message", {"type": "message", "subtype": "message_deleted", "channel": "C1"}),
    # プライベートチャンネル
    ("event", "message", _msg("G1", "U1", "1700000100.000100", "秘密")),
    (
        "event",
        "message",
        _msg("G1", "U2", "1700000110.000100", "秘密の返信", thread_ts="1700000100.000100"),
    ),
    (
        "event",
        "message",
        _msg(
            "G1", "U1", "1700000120.000100", "資料", files=[_file("secret.txt", mime="text/plain")]
        ),
    ),
    ("event", "message", _msg("G2", "U1", "1700000130.000100", "is_group のチャンネル")),
    # メンバーの出入り
    _set_members("G1", ["U1", "U2", "U3", "UBOT"]),
    ("event", "member_joined_channel", {"channel": "G1", "user": "U3"}),
    _set_members("G1", ["U1", "U3", "UBOT"]),
    ("event", "member_left_channel", {"channel": "G1", "user": "U2"}),
    ("event", "member_joined_channel", {"channel": "C1", "user": "U4"}),
    ("event", "member_left_channel", {"channel": "C1", "user": "U4"}),
    _set_members("C1", ["U1", "U2", "U3", "U4", "UBOT"]),
    ("event", "member_joined_channel", {"channel": "C1", "user": "U3"}),
    ("event", "member_joined_channel", {"channel": "", "user": "U3"}),
    # メンション
    ("mention", {"channel": "C1", "text": "<@UBOT> help"}),
    ("mention", {"channel": "C1", "text": "<@UBOT>"}),
    ("mention", {"channel": "C1", "text": "<@UBOT|logbot> URL"}),
    ("mention", {"channel": "G1", "text": "<@UBOT> url"}),
    ("mention", {"channel": "C9", "text": "<@UBOT> url"}),
    ("mention", {"channel": "C1", "text": "<@UBOT> なにか"}),
    ("mention", {"channel": "C1", "text": "<@UBOT> backfill 3"}),
    ("mention", {"channel": "G1", "text": "<@UBOT> backfill"}),
    ("mention", {"channel": "C1", "text": "<@UBOT> backfill abc"}),
    # 名前の変更
    ("set", ("channel_name", "C1", "general-2")),
    (
        "event",
        "channel_rename",
        {"channel": {"id": "C1", "name": "general-2"}, "previous_name": "general"},
    ),
    ("set", ("channel_name", "G1", "secret-2")),
    ("event", "group_rename", {"channel": {"id": "G1", "name": "secret-2"}}),
    ("event", "channel_rename", {"channel": {"id": "C9", "name": "random-2"}}),
    ("event", "channel_rename", {"channel": {"id": "", "name": ""}}),
    ("event", "message", _msg("C1", "U2", "1700000200.000100", "改名後の投稿")),
    ("event", "message", _msg("G1", "U1", "1700000210.000100", "改名後の秘密")),
    ("mention", {"channel": "C1", "text": "<@UBOT> url"}),
    ("mention", {"channel": "G1", "text": "<@UBOT> url"}),
    # 表示名の変更
    ("set", ("display_name", "U1", "Alice2")),
    ("event", "user_change", {"user": {"id": "U1"}}),
    ("event", "user_change", {"user": {"id": "U1"}}),
    ("set", ("display_name", "U4", "Dave2")),
    ("event", "user_change", {"user": {"id": "U4"}}),
    ("event", "user_change", {"user": {"id": "U404"}}),
    ("event", "user_change", {"user": {}}),
    ("event", "message", _msg("C1", "U1", "1700000220.000100", "改名後のアリス")),
    ("event", "file_shared", {"file_id": "F1"}),
]

SHARED_DRIVE_STEPS = [
    ("event", "message", _msg("C1", "U1", "1700000000.000100", "共有ドライブ")),
    (
        "event",
        "message",
        _msg("C1", "U2", "1700000010.000100", "返信", thread_ts="1700000000.000100"),
    ),
    (
        "event",
        "message",
        _msg(
            "C1",
            "U2",
            "1700000030.000100",
            "ファイル",
            files=[_file("report.pdf", mime="application/pdf")],
        ),
    ),
    ("event", "message", _msg("G1", "U1", "1700000100.000100", "秘密")),
    (
        "event",
        "message",
        _msg(
            "G1", "U2", "1700000120.000100", "資料", files=[_file("secret.txt", mime="text/plain")]
        ),
    ),
    _set_members("G1", ["U1", "U2", "U3", "UBOT"]),
    ("event", "member_joined_channel", {"channel": "G1", "user": "U3"}),
    _set_members("G1", ["U1", "U3", "UBOT"]),
    ("event", "member_left_channel", {"channel": "G1", "user": "U2"}),
    ("event", "member_joined_channel", {"channel": "C1", "user": "U3"}),
    (
        "event",
        "message",
        {
            "type": "message",
            "subtype": "message_deleted",
            "channel": "C1",
            "deleted_ts": "1700000030.000100",
        },
    ),
    ("mention", {"channel": "G1", "text": "<@UBOT> url"}),
    ("set", ("channel_name", "G1", "secret-2")),
    ("event", "group_rename", {"channel": {"id": "G1", "name": "secret-2"}}),
    ("event", "message", _msg("G1", "U1", "1700000210.000100", "改名後")),
]

WEEKLY_STEPS = [
    ("weekly", {"days": 8}),
    ("weekly", {"channel": "secret", "days": 8}),
    ("weekly", {"public": True, "days": 8}),
    ("weekly", {"channel": "nope", "days": 8}),
]

BACKFILL_STEPS = [
    ("backfill", {"channel": "#general", "days": 30}),
    ("backfill", {"channel": "random", "days": 90}),
    ("backfill", {"channel": "nope", "days": 90}),
    ("backfill", {"public": True, "days": 90}),
    ("backfill", {"days": 90}),
]


def build(group: str) -> tuple[World, list]:
    """シナリオ名から、前提となる世界と手順を作る。"""
    if group == "realtime":
        world = World()
        world.seed_legacy_sheet()
        return world, REALTIME_STEPS
    if group == "shared_drive":
        return World(shared_drive=True), SHARED_DRIVE_STEPS
    if group == "weekly":
        world = World()
        world.seed_public_index()
        return world, WEEKLY_STEPS
    if group == "backfill":
        world = World()
        world.seed_public_index()
        return world, BACKFILL_STEPS
    raise ValueError(group)


GROUPS = ("realtime", "shared_drive", "weekly", "backfill")


def apply_set(world: World, payload: tuple) -> None:
    """("set", ...) 手順: Slack 側の状態を変える。"""
    kind, key, value = payload
    if kind == "members":
        world.slack.members[key] = list(value)
    elif kind == "channel_name":
        world.slack.channels[key]["name"] = value
    elif kind == "display_name":
        world.slack.users[key]["profile"]["display_name"] = value
    else:
        raise ValueError(kind)
