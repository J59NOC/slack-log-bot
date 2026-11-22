"""シナリオテスト。外部 API の呼び出しをゴールデンファイルと突き合わせる。

実際の使われ方に沿ってイベントを流す。ゴールデンファイルは tests/golden/*.json。

Google・Slack はすべてフェイク（tests/fakes.py）に置き換えてある。ゴールデンは
「どの API を、どの順で、どんな中身で呼んだか」と、出したログの記録で、
スプレッドシートの書式や共有設定が意図せず変わっていないことを確かめる。

挙動を意図して変えたときは、ゴールデンを書き直して差分をレビューする:

    UPDATE_GOLDEN=1 uv run python -m unittest tests.test_scenarios
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import sys
import time
import types
import unittest
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

import requests

# `python -m unittest tests.test_scenarios` でも fakes / scenarios を import できるように
sys.path.insert(0, str(Path(__file__).resolve().parent))

import scenarios
from fakes import FakeBoltApp, FakeDownload, dump_golden
from src.bootstrap import GoogleClients, wire
from src.infrastructure.google.drive import wrap_drive
from src.infrastructure.settings import Settings
from src.infrastructure.slack.client import install_retry_handlers
from src.presentation import cli, slack_app

if TYPE_CHECKING:
    from collections.abc import Callable

    from fakes import FakeSlackClient
    from src.bootstrap import Services

GOLDEN_DIR = Path(__file__).parent / "golden"
KEYS = ("sheets", "drive", "slack", "slack_reads", "logs")

SETTINGS = Settings(
    # テスト用の偽のトークン
    slack_bot_token="xoxb-test",  # noqa: S106
    # テスト用の偽のトークン
    slack_app_token="xapp-test",  # noqa: S106
    google_drive_folder_id=scenarios.ROOT_FOLDER_ID,
)


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.records: list[list[str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append([record.levelname, record.getMessage()])


def _call(fn: Callable[..., object], **kwargs: object) -> object:
    """Bolt と同じく、ハンドラが受け取る引数だけを渡す。"""
    accepted = inspect.signature(fn).parameters
    return fn(**{k: v for k, v in kwargs.items() if k in accepted})


def run_scenario(group: str) -> dict:
    world, steps = scenarios.build(group)
    world.slack.now = time.time()

    def download(url: str, headers: dict, **_: object) -> FakeDownload:
        world.log.add("slack_reads", "files.download", url=url, auth=headers["Authorization"])
        return FakeDownload(url)

    def services(client: FakeSlackClient) -> Services:
        install_retry_handlers(client)
        google = GoogleClients(
            sheets=world.gc,
            log_drive=wrap_drive(world.drive.service()),
            attachment_drive=wrap_drive(world.drive.service()),
        )
        return wire(SETTINGS, client, google)

    capture = _Capture()
    root = logging.getLogger()
    level = root.level
    root.addHandler(capture)
    root.setLevel(logging.INFO)
    fake_requests = types.SimpleNamespace(get=download, RequestException=requests.RequestException)
    try:
        with (
            mock.patch("src.infrastructure.slack.files.requests", fake_requests),
            mock.patch.object(slack_app, "_in_background", lambda fn, *a: fn(*a)),
        ):
            app = None
            if group in ("realtime", "shared_drive"):
                app = FakeBoltApp(world.slack)
                slack_app.register_handlers(app, services(app.client))
            for step in steps:
                _run_step(world, app, step, services)
    finally:
        root.removeHandler(capture)
        root.setLevel(level)

    result = dict(world.log.entries)
    result["logs"] = capture.records
    return result


def _run_step(
    world: scenarios.World,
    app: FakeBoltApp | None,
    step: tuple,
    services: Callable[[FakeSlackClient], Services],
) -> None:
    kind = step[0]
    if kind == "set":
        scenarios.apply_set(world, step[1])
    elif kind == "event":
        _call(
            app.handlers[step[1]],
            event=step[2],
            client=world.slack,
            logger=logging.getLogger("handler"),
        )
    elif kind == "mention":
        event = {"type": "app_mention", "user": "U1", "ts": "1", **step[1]}

        def say(text: str) -> None:
            world.log.add("slack", "say", channel=event["channel"], text=text)

        _call(
            app.handlers["app_mention"],
            event=event,
            client=world.slack,
            say=say,
            logger=logging.getLogger("handler"),
        )
    elif kind in ("weekly", "backfill"):
        # 週次収集・バックフィルは実行のたびに別プロセスなので、毎回組み立て直す
        opts = step[1]
        run = cli.run_weekly if kind == "weekly" else cli.run_backfill
        run(
            services(world.slack),
            channel_filter=opts.get("channel"),
            days=opts["days"],
            public_only=opts.get("public", False),
        )
    else:
        raise ValueError(kind)


class ScenarioTests(unittest.TestCase):
    maxDiff = None

    def check(self, group: str) -> None:
        actual = run_scenario(group)
        path = GOLDEN_DIR / f"{group}.json"
        if os.environ.get("UPDATE_GOLDEN") == "1":
            dump_golden({key: actual[key] for key in KEYS}, path)
            self.skipTest(f"{path.name} を書き直しました")
        expected = json.loads(path.read_text(encoding="utf-8"))

        for key in KEYS:
            want, got = expected[key], json.loads(json.dumps(actual[key]))
            for i, (w, g) in enumerate(zip(want, got, strict=False)):
                if w != g:
                    self.fail(
                        f"{group}: {key}[{i}] が違います\n"
                        f"  期待: {json.dumps(w, ensure_ascii=False)[:3000]}\n"
                        f"  実際: {json.dumps(g, ensure_ascii=False)[:3000]}"
                    )
            if len(want) != len(got):
                extra = (got if len(got) > len(want) else want)[min(len(want), len(got)) :]
                self.fail(
                    f"{group}: {key} の件数が違います（期待 {len(want)} / 実際 {len(got)}）\n"
                    + "\n".join(json.dumps(e, ensure_ascii=False)[:500] for e in extra[:5])
                )

    def test_realtime(self) -> None:
        """投稿・返信・添付・編集・削除・メンバーの出入り・コマンド・改名。"""
        self.check("realtime")

    def test_shared_drive(self) -> None:
        """共有ドライブ上では、ファイル単位の共有をしない。"""
        self.check("shared_drive")

    def test_weekly(self) -> None:
        self.check("weekly")

    def test_backfill(self) -> None:
        self.check("backfill")


if __name__ == "__main__":
    unittest.main()
