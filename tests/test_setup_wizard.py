"""setup.py（対話式のセットアップ）のシナリオテスト。

一時ディレクトリの中で setup.py の main() を動かし、標準入力・Slack・Google の
OAuth・Drive / Sheets をすべてフェイクに置き換える。画面に出した文字・Slack と
Google への呼び出し・書き出した .env を、tests/golden/setup/ の記録と突き合わせる。

挙動を意図して変えたときは、記録を書き直して差分をレビューする:

    UPDATE_GOLDEN=1 uv run python -m unittest tests.test_setup_wizard
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import logging
import os
import shutil
import sys
import tempfile
import unittest
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

import google_auth_oauthlib.flow
import gspread
import slack_sdk
from slack_sdk.errors import SlackApiError

# `python -m unittest tests.test_setup_wizard` でも fakes を import できるように
sys.path.insert(0, str(Path(__file__).resolve().parent))

from typing import TYPE_CHECKING

from fakes import (
    FOLDER_MIME,
    CallLog,
    DriveWorld,
    FakeGspread,
    dump_golden,
)
from src.infrastructure import settings as settings_module
from src.infrastructure.google import auth
from src.infrastructure.google import drive as drive_module

if TYPE_CHECKING:
    from collections.abc import Callable

GOLDEN_DIR = Path(__file__).parent / "golden" / "setup"

PARENT_FOLDER_ID = "parentfolder00000000000000"
SHARED_DRIVE_ID = "shareddrive0000000000000"

APP_TOKEN = "xapp-1-A111-222-" + "a" * 64
BOT_TOKEN = "xoxb-111-222-" + "b" * 24
BOT_TOKEN_NO_SCOPE_HEADER = "xoxb-111-333-" + "c" * 24
BOT_TOKEN_FEW_SCOPES = "xoxb-111-444-" + "d" * 24

ALL_SCOPES = (
    "app_mentions:read,channels:history,channels:read,groups:history,groups:read,"
    "users:read,users:read.email,files:read,chat:write,im:history"
)

# 非 ASCII を含めない（setup.py が .env.example を読むときの文字コードに依存しないように）
ENV_EXAMPLE = """# Test template
# -- Slack --
SLACK_BOT_TOKEN=xoxb-xxxx
SLACK_APP_TOKEN=xapp-xxxx

# -- Google --
GOOGLE_DRIVE_FOLDER_ID=

GOOGLE_OAUTH_CLIENT_FILE=client_secret.json
GOOGLE_DRIVE_TOKEN_FILE=drive_token.json
TIMEZONE=Asia/Tokyo
"""

CHANNELS = [
    {"id": "C1", "name": "general"},
    {"id": "C9", "name": "random"},
    {"id": "G1", "name": "secret"},
]


# ── Slack と Google OAuth のフェイク ──


class _SlackResponse:
    def __init__(self, data: dict, headers: dict) -> None:
        self.data = data
        self.headers = headers

    def get(self, key: str, default: object = None) -> object:
        return self.data.get(key, default)


class SlackApp:
    """セットアップ中に確かめる Slack App の状態。"""

    def __init__(self, log: CallLog) -> None:
        self.log = log
        self.app_tokens = {APP_TOKEN}
        # bot トークン → X-OAuth-Scopes ヘッダ（None ならヘッダなし）
        self.bot_tokens: dict[str, str | None] = {
            BOT_TOKEN: ALL_SCOPES,
            BOT_TOKEN_NO_SCOPE_HEADER: None,
            BOT_TOKEN_FEW_SCOPES: "channels:history,channels:read,users:read",
        }
        self.rejection = "invalid_auth"
        self.channels = list(CHANNELS)
        self.list_error: str | None = None

    def web_client_class(self) -> type:
        app = self

        class WebClient:
            def __init__(self, token: str | None = None, **_: object) -> None:
                self.token = token

            def apps_connections_open(self, app_token: str) -> dict:
                app.log.add("slack_reads", "apps.connections.open", app_token=app_token)
                if app_token not in app.app_tokens:
                    msg = "rejected"
                    raise SlackApiError(msg, {"ok": False, "error": "invalid_auth"})
                return {"ok": True}

            def auth_test(self) -> _SlackResponse:
                app.log.add("slack_reads", "auth.test", token=self.token)
                if self.token not in app.bot_tokens:
                    msg = "rejected"
                    raise SlackApiError(msg, {"ok": False, "error": app.rejection})
                scopes = app.bot_tokens[self.token]
                headers = {"X-OAuth-Scopes": scopes} if scopes is not None else {}
                return _SlackResponse({"ok": True, "team": "Acme", "user": "logbot"}, headers)

            def conversations_list(
                self, types: str | None = None, cursor: str | None = None, **_: object
            ) -> dict:
                app.log.add("slack_reads", "conversations.list", types=types, cursor=cursor)
                if app.list_error:
                    raise RuntimeError(app.list_error)
                start = int(cursor or 0)
                page = app.channels[start : start + 2]
                nxt = str(start + 2) if start + 2 < len(app.channels) else ""
                return {"channels": page, "response_metadata": {"next_cursor": nxt}}

        return WebClient


class ConsentError(Exception):
    """同意画面での失敗（access_denied など）。"""


class GoogleAccount:
    """OAuth の同意画面と、保存済みトークンの状態。"""

    def __init__(self, log: CallLog) -> None:
        self.log = log
        self.token_error: str | None = None
        self.consent_error: str | None = None

    def load_credentials(self, _required_scopes: list[str] | None = None) -> str:
        self.log.add("google_auth", "load_credentials")
        if self.token_error:
            raise auth.CredentialsError(self.token_error)
        return "creds"

    def save_token(self, creds: str, path: str | None = None) -> str | None:
        self.log.add("google_auth", "save_token", creds=creds, path=path)
        Path(path).write_text("{}", encoding="utf-8")
        return path

    def flow_class(self) -> type:
        account = self

        class InstalledAppFlow:
            @classmethod
            def from_client_secrets_file(
                cls, client_file: str, scopes: list[str]
            ) -> InstalledAppFlow:
                account.log.add("google_auth", "flow", client_file=client_file, scopes=scopes)
                return cls()

            def run_local_server(self, *, port: int = 0, open_browser: bool = True) -> str:
                account.log.add(
                    "google_auth", "run_local_server", port=port, open_browser=open_browser
                )
                if account.consent_error:
                    raise ConsentError(account.consent_error)
                return "new-creds"

        return InstalledAppFlow


# ── シナリオ ──


@dataclass
class World:
    log: CallLog
    drive: DriveWorld
    gc: FakeGspread
    slack: SlackApp
    google: GoogleAccount
    tmp: Path


@dataclass
class Scenario:
    argv: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    shared_drive: bool = False
    env: dict[str, str] | None = None  # 既にある .env（プロファイルなし）
    profile_env: dict[str, dict[str, str]] = field(default_factory=dict)
    token: bool = False  # drive_token.json が既にあるか
    client_secret: bool = True
    prepare: Callable[[World], None] | None = None


def _seed_workspace_folder(world: World) -> None:
    """運用中の保存先（公開側・非公開側・索引・旧レイアウトの残り）。"""
    root = PARENT_FOLDER_ID
    public = world.drive.new_id()
    world.drive.add_file(public, "public", FOLDER_MIME, [root])
    world.drive.add_file(world.drive.new_id(), "private", FOLDER_MIME, [root])
    index = world.gc.add_spreadsheet("Slack ログ 索引 - Acme", public)
    index.sheets[0].title = "📖 このシートについて"
    index.sheets[0].values = [["Slack ログ", ""]]
    world.gc.add_spreadsheet("Slack Log - #general", root)
    attachments = world.drive.new_id()
    world.drive.add_file(attachments, "添付ファイル", FOLDER_MIME, [root])
    world.drive.add_file(world.drive.new_id(), "#general", FOLDER_MIME, [attachments])
    world.drive.add_file(
        world.drive.new_id(),
        "#random",
        FOLDER_MIME,
        [attachments],
        {"slackChannelId": "C9"},
    )


def _refuse_writes(world: World) -> None:
    world.drive.refusals |= {"files.create", "sheets.create"}


def _valid_env(**extra: str) -> dict[str, str]:
    return {"SLACK_APP_TOKEN": APP_TOKEN, "SLACK_BOT_TOKEN": BOT_TOKEN, **extra}


SCENARIOS: dict[str, Scenario] = {
    # 何も無い状態から。入力ミス・途中で切れたトークン・拒否からのやり直しを含む
    "fresh": Scenario(
        inputs=[
            "",
            "abc",
            APP_TOKEN,
            "xoxb-1-2-short",
            BOT_TOKEN,
            f"https://drive.google.com/drive/folders/{PARENT_FOLDER_ID}?usp=sharing",
        ],
        shared_drive=True,
    ),
    # 運用中のプロファイルで再実行。--parent は無視され、チャンネル ID を付け直す
    "rerun_profile": Scenario(
        argv=["--profile", "leaders", "--parent", "https://drive.google.com/drive/folders/x"],
        profile_env={
            "leaders": {
                "SLACK_APP_TOKEN": APP_TOKEN,
                "SLACK_BOT_TOKEN": BOT_TOKEN_NO_SCOPE_HEADER,
                "GOOGLE_DRIVE_FOLDER_ID": PARENT_FOLDER_ID,
            }
        },
        token=True,
        client_secret=False,
        prepare=_seed_workspace_folder,
    ),
    # マイドライブに作る（親フォルダを空欄に）。チャンネル一覧は取れない
    "fresh_my_drive": Scenario(
        inputs=[""],
        env=_valid_env(),
        prepare=lambda w: setattr(w.slack, "list_error", "missing_scope"),
    ),
    "missing_scopes": Scenario(
        env=_valid_env(SLACK_BOT_TOKEN=BOT_TOKEN_FEW_SCOPES),
    ),
    "revoked_app_token": Scenario(
        env=_valid_env(SLACK_APP_TOKEN="xapp-1-A111-999-" + "z" * 64),
    ),
    "token_rejected_three_times": Scenario(
        env=_valid_env(SLACK_BOT_TOKEN="xoxb-9-9-" + "e" * 24),
        inputs=["xoxb-9-8-" + "f" * 24, "xoxb-9-7-" + "g" * 24],
        prepare=lambda w: setattr(w.slack, "rejection", "token_revoked"),
    ),
    "consent_denied": Scenario(
        env=_valid_env(),
        prepare=lambda w: setattr(
            w.google, "consent_error", "(access_denied) Access blocked: not a test user"
        ),
    ),
    "expired_token_without_client": Scenario(
        env=_valid_env(),
        token=True,
        client_secret=False,
        prepare=lambda w: setattr(
            w.google,
            "token_error",
            "drive_token.json の有効期限が切れており、更新もできません。\n"
            "  uv run setup.py を実行して認証をやり直してください。",
        ),
    ),
    "eof_at_prompt": Scenario(),
    "check_drive_ok": Scenario(
        argv=["--check-drive", f"https://drive.google.com/drive/folders/{PARENT_FOLDER_ID}"],
        shared_drive=True,
        token=True,
    ),
    "check_drive_refused": Scenario(
        argv=["--check-drive", PARENT_FOLDER_ID],
        token=True,
        prepare=_refuse_writes,
    ),
    "check_drive_missing_folder": Scenario(
        argv=["--check-drive", "https://drive.google.com/drive/folders/nosuchfolder"],
        token=True,
    ),
}


# ── 実行 ──


class _Console:
    """標準出力と標準エラーを、出た順に行として集める。"""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self._partial = {"out": "", "err": ""}

    def stream(self, tag: str) -> io.TextIOBase:
        console = self

        class Stream(io.TextIOBase):
            def write(self, text: str) -> int:
                console.add(tag, text)
                return len(text)

            def flush(self) -> None:
                pass

        return Stream()

    def add(self, tag: str, text: str) -> None:
        *complete, self._partial[tag] = (self._partial[tag] + text).split("\n")
        for line in complete:
            self.lines.append(line if tag == "out" else f"[stderr] {line}")

    def finish(self) -> list[str]:
        for tag in ("out", "err"):
            if self._partial[tag]:
                self.add(tag, "\n")
        return self.lines


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.records: list[list[str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append([record.levelname, record.getMessage()])


def _write_env(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")


ENV_KEYS = (
    "SLACK_LOG_PROFILE",
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "GOOGLE_DRIVE_FOLDER_ID",
    "GOOGLE_SPREADSHEET_ID",
    "GOOGLE_DRIVE_TOKEN_FILE",
    "GOOGLE_OAUTH_CLIENT_FILE",
    "TIMEZONE",
)


def _prepare_files(tmp: Path, scenario: Scenario) -> None:
    (tmp / ".env.example").write_text(ENV_EXAMPLE, encoding="utf-8")
    if scenario.client_secret:
        (tmp / "client_secret.json").write_text("{}", encoding="utf-8")
    if scenario.env is not None:
        _write_env(tmp / ".env", scenario.env)
    if scenario.token:
        (tmp / "drive_token.json").write_text("{}", encoding="utf-8")
    for profile, values in scenario.profile_env.items():
        _write_env(tmp / "profiles" / profile / ".env", values)
        if scenario.token:
            (tmp / "profiles" / profile / "drive_token.json").write_text("{}", encoding="utf-8")


def _fake_input(inputs: list[str]) -> Callable[[str], str]:
    """用意した答えを順に返す input()。答えが尽きたら EOF。"""
    answers = iter(inputs)

    def fake_input(prompt: str = "") -> str:
        sys.stdout.write(prompt)
        try:
            answer = next(answers)
        except StopIteration:
            sys.stdout.write("<EOF>\n")
            raise EOFError from None
        sys.stdout.write(answer + "\n")
        return answer

    return fake_input


def _install_fakes(stack: contextlib.ExitStack, world: World, scenario: Scenario) -> None:
    # 閉じ忘れたファイルの警告は GC の時機で出たり出なかったりするので記録しない
    stack.enter_context(warnings.catch_warnings())
    warnings.simplefilter("ignore", ResourceWarning)
    stack.enter_context(mock.patch.dict(os.environ))
    for key in ENV_KEYS:
        os.environ.pop(key, None)
    stack.enter_context(mock.patch.object(settings_module, "BASE_DIR", world.tmp))
    stack.enter_context(mock.patch.object(sys, "argv", ["setup.py", *scenario.argv]))
    stack.enter_context(mock.patch("builtins.input", _fake_input(scenario.inputs)))
    web_client = world.slack.web_client_class()
    stack.enter_context(mock.patch.object(slack_sdk, "WebClient", web_client))
    stack.enter_context(
        mock.patch.object(google_auth_oauthlib.flow, "InstalledAppFlow", world.google.flow_class())
    )
    stack.enter_context(mock.patch.object(auth, "load_credentials", world.google.load_credentials))
    stack.enter_context(mock.patch.object(auth, "save_token", world.google.save_token))
    stack.enter_context(
        mock.patch.object(drive_module, "build", lambda *_a, **_k: world.drive.service())
    )
    stack.enter_context(mock.patch.object(gspread, "authorize", lambda _creds: world.gc))


def _run_main(stack: contextlib.ExitStack, world: World, console: _Console) -> int:
    """setup.py の main() を動かし、終了コードを返す。"""
    setup = importlib.reload(importlib.import_module("setup"))
    # プロファイルの置き場所を一時ディレクトリにする
    # （旧実装は setup.py の場所を基準にしていた）
    setup.__file__ = str(world.tmp / "setup.py")

    stack.enter_context(contextlib.redirect_stdout(console.stream("out")))
    stack.enter_context(contextlib.redirect_stderr(console.stream("err")))
    try:
        setup.main()
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    return 0


def run_setup(name: str) -> dict:
    scenario = SCENARIOS[name]
    tmp = Path(tempfile.mkdtemp(prefix="setup-wizard-"))
    log = CallLog()
    log.entries["google_auth"] = []
    drive = DriveWorld(log, PARENT_FOLDER_ID, SHARED_DRIVE_ID if scenario.shared_drive else None)
    world = World(log, drive, FakeGspread(log, drive), SlackApp(log), GoogleAccount(log), tmp)

    _prepare_files(tmp, scenario)
    if scenario.prepare:
        scenario.prepare(world)
    log.entries = {key: [] for key in log.entries}  # 準備中の呼び出しは記録しない

    console = _Console()
    capture = _Capture()
    root_logger = logging.getLogger()
    level = root_logger.level
    root_logger.addHandler(capture)
    root_logger.setLevel(logging.INFO)
    cwd = Path.cwd()
    try:
        os.chdir(tmp)
        with contextlib.ExitStack() as stack:
            _install_fakes(stack, world, scenario)
            exit_code = _run_main(stack, world, console)
    finally:
        os.chdir(cwd)
        root_logger.removeHandler(capture)
        root_logger.setLevel(level)

    env_files = {
        str(path.relative_to(tmp)).replace("\\", "/"): path.read_text(encoding="utf-8")
        for path in sorted(tmp.rglob(".env"))
    }
    result = {
        "exit": exit_code,
        "console": console.finish(),
        "env_files": env_files,
        "slack_reads": log.entries["slack_reads"],
        "google_auth": log.entries["google_auth"],
        "drive": log.entries["drive"],
        "sheets": log.entries["sheets"],
        "logs": capture.records,
    }
    shutil.rmtree(tmp, ignore_errors=True)
    return _scrub(result, tmp)


def _scrub(value: object, tmp: Path) -> object:
    """一時ディレクトリのパスを <TMP> に置き換える。"""
    variants = sorted(
        {str(tmp), str(tmp).replace("\\", "/"), os.path.realpath(tmp)}, key=len, reverse=True
    )
    if isinstance(value, str):
        for variant in variants:
            value = value.replace(variant, "<TMP>")
        # パスの区切りは OS によらず / にそろえる
        return value.replace("\\", "/") if "<TMP>" in value else value
    if isinstance(value, list):
        return [_scrub(v, tmp) for v in value]
    if isinstance(value, dict):
        return {_scrub(k, tmp): _scrub(v, tmp) for k, v in value.items()}
    return value


class SetupWizardTests(unittest.TestCase):
    maxDiff = None

    def _check(self, name: str) -> None:
        actual = json.loads(json.dumps(run_setup(name)))
        path = GOLDEN_DIR / f"{name}.json"
        if os.environ.get("UPDATE_GOLDEN") == "1":
            dump_golden(actual, path)
            self.skipTest(f"{path.name} を書き直しました")
        expected = json.loads(path.read_text(encoding="utf-8"))
        for key in expected:
            want, got = expected[key], actual[key]
            if isinstance(want, list):
                for i, (w, g) in enumerate(zip(want, got, strict=False)):
                    if w != g:
                        self.fail(f"{name}: {key}[{i}] が違います\n  期待: {w!r}\n  実際: {g!r}")
            self.assertEqual(want, got, f"{name}: {key}")


def _add_test(name: str) -> None:
    def test(self: SetupWizardTests) -> None:
        self._check(name)

    test.__doc__ = name
    setattr(SetupWizardTests, f"test_{name}", test)


for _name in SCENARIOS:
    _add_test(_name)


if __name__ == "__main__":
    unittest.main()
