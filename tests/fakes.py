"""Google と Slack の API を置き換えるフェイク。

外部 API を叩かずに、bot が「どの API を、どの順で、どんな中身で」呼んだかを
記録する。シナリオテスト（test_scenarios.py）はこの記録をゴールデンファイルと
突き合わせ、リファクタリングで API 呼び出しが変わっていないことを確かめる。

Drive はクエリ（q=）を解釈するインメモリのモデルとして動く。作ったフォルダが
次の検索で見つかる、といった往復が実物と同じように起きないと、bot は毎回
「まだ無い」と判断して作り直す経路しか通らないため。
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import gspread
import requests

if TYPE_CHECKING:
    from collections.abc import Callable

FOLDER_MIME = "application/vnd.google-apps.folder"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"


# ── 記録 ──


def _plain(value: object) -> object:
    """JSON に落とせる形へ。タプルはリストに、未知のオブジェクトは型名に。"""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "value") and type(value).__module__.startswith("gspread"):
        return value.value  # gspread の Enum（ValueInputOption など）
    return f"<{type(value).__name__}>"


class CallLog:
    """API 呼び出しを宛先ごとに記録する。"""

    CHANNELS = ("sheets", "drive", "slack", "slack_reads")

    def __init__(self) -> None:
        self.entries: dict[str, list] = {name: [] for name in self.CHANNELS}

    def add(self, dest: str, call: str, /, **args: object) -> None:
        self.entries[dest].append({"call": call, **_plain(args)})


# ── Drive ──


_CLAUSES = [
    (
        re.compile(r"appProperties has \{ key='([^']+)' and value='([^']*)' \}"),
        lambda m, f: f["appProperties"].get(m.group(1)) == m.group(2),
    ),
    (re.compile(r"(?<![A-Za-z])name = '([^']*)'"), lambda m, f: f["name"] == m.group(1)),
    (re.compile(r"'([^']+)' in parents"), lambda m, f: m.group(1) in f["parents"]),
    (re.compile(r"mimeType = '([^']+)'"), lambda m, f: f["mimeType"] == m.group(1)),
    (re.compile(r"trashed = false"), lambda _m, f: not f["trashed"]),
]


def _matches(query: str, meta: dict) -> bool:
    return all(check(m, meta) for pattern, check in _CLAUSES for m in pattern.finditer(query or ""))


class RefusedError(Exception):
    """DriveWorld.refusals で拒否させた呼び出し（権限不足のつもり）。"""


class _Request:
    def __init__(self, fn: Callable[[], object], refused: str = "") -> None:
        self._fn = fn
        self._refused = refused

    def execute(self) -> object:
        if self._refused:
            msg = f"insufficientFilePermissions: {self._refused}"
            raise RefusedError(msg)
        return self._fn()


class DriveWorld:
    """Drive の中身。ファイルと権限をメモリに持つ。"""

    def __init__(self, log: CallLog, root_id: str, drive_id: str | None = None) -> None:
        self.log = log
        self.drive_id = drive_id
        self.files: dict[str, dict] = {}
        self.permissions: dict[str, list[dict]] = {}
        # ここに入れた呼び出し名（"files.create" など）は実行時に RefusedError になる
        self.refusals: set[str] = set()
        self._seq = 0
        self.add_file(root_id, "Slack ログ", FOLDER_MIME, parents=[])

    def new_id(self) -> str:
        # 添付リンクから id を拾う正規表現は 20 文字以上を要求する
        self._seq += 1
        return f"id{self._seq:020d}"

    def add_file(
        self,
        file_id: str,
        name: str,
        mime: str,
        parents: list[str],
        app_properties: dict | None = None,
    ) -> dict:
        meta = {
            "id": file_id,
            "name": name,
            "mimeType": mime,
            "parents": list(parents),
            "appProperties": dict(app_properties or {}),
            "trashed": False,
        }
        if self.drive_id:
            meta["driveId"] = self.drive_id
        self.files[file_id] = meta
        self.permissions[file_id] = [
            {
                "id": f"perm-owner-{file_id[-4:]}",
                "type": "user",
                "role": "owner",
                "emailAddress": "owner@example.com",
            }
        ]
        return meta

    def service(self) -> FakeDriveService:
        return FakeDriveService(self)


class _Files:
    def __init__(self, world: DriveWorld) -> None:
        self.w = world

    def _request(self, call: str, fn: Callable[[], object]) -> _Request:
        return _Request(fn, call if call in self.w.refusals else "")

    def list(self, **kw: object) -> _Request:
        self.w.log.add("drive", "files.list", **kw)

        def run() -> dict:
            keys = ["id", "name"]
            keys += [k for k in ("mimeType", "appProperties") if k in kw.get("fields", "")]
            found = [
                {k: copy.deepcopy(f[k]) for k in keys}
                for f in self.w.files.values()
                if _matches(kw.get("q", ""), f)
            ]
            return {"files": found[: kw.get("pageSize", 100)]}

        return self._request("files.list", run)

    def get(self, **kw: object) -> _Request:
        self.w.log.add("drive", "files.get", **kw)

        def run() -> dict:
            if kw["fileId"] not in self.w.files:
                msg = f"File not found: {kw['fileId']}"
                raise LookupError(msg)
            meta = self.w.files[kw["fileId"]]
            return copy.deepcopy(meta)

        return self._request("files.get", run)

    def create(self, **kw: object) -> _Request:
        media = kw.get("media_body")
        logged = dict(kw)
        if media is not None:
            logged["media_body"] = {
                "mimetype": media.mimetype(),
                "size": media.size(),
            }
        self.w.log.add("drive", "files.create", **logged)

        def run() -> dict:
            body = kw.get("body", {})
            file_id = self.w.new_id()
            self.w.add_file(
                file_id,
                body.get("name", ""),
                body.get("mimeType") or (media.mimetype() if media else ""),
                body.get("parents", []),
                body.get("appProperties"),
            )
            return {
                "id": file_id,
                "webViewLink": f"https://drive.google.com/file/d/{file_id}/view",
            }

        return self._request("files.create", run)

    def update(self, **kw: object) -> _Request:
        self.w.log.add("drive", "files.update", **kw)

        def run() -> dict:
            meta = self.w.files[kw["fileId"]]
            body = kw.get("body") or {}
            for key, value in body.items():
                if key == "appProperties":
                    meta["appProperties"].update(value)
                else:
                    meta[key] = value
            if kw.get("removeParents"):
                removed = kw["removeParents"].split(",")
                meta["parents"] = [p for p in meta["parents"] if p not in removed]
            if kw.get("addParents"):
                meta["parents"].append(kw["addParents"])
            return {"id": meta["id"]}

        return self._request("files.update", run)

    def delete(self, **kw: object) -> _Request:
        self.w.log.add("drive", "files.delete", **kw)

        def run() -> dict:
            self.w.files.pop(kw["fileId"], None)
            return {}

        return self._request("files.delete", run)

    def copy(self, **kw: object) -> _Request:
        self.w.log.add("drive", "files.copy", **kw)
        return _Request(lambda: {"id": self.w.new_id()})


class _Permissions:
    def __init__(self, world: DriveWorld) -> None:
        self.w = world

    def list(self, **kw: object) -> _Request:
        self.w.log.add("drive", "permissions.list", **kw)
        return _Request(
            lambda: {"permissions": copy.deepcopy(self.w.permissions.get(kw["fileId"], []))}
        )

    def create(self, **kw: object) -> _Request:
        self.w.log.add("drive", "permissions.create", **kw)

        def run() -> dict:
            perms = self.w.permissions.setdefault(kw["fileId"], [])
            perm = {"id": f"perm-{len(perms)}-{kw['fileId'][-4:]}", **kw["body"]}
            perms.append(perm)
            return perm

        return _Request(run)

    def update(self, **kw: object) -> _Request:
        self.w.log.add("drive", "permissions.update", **kw)

        def run() -> dict:
            for perm in self.w.permissions.get(kw["fileId"], []):
                if perm["id"] == kw["permissionId"]:
                    perm.update(kw["body"])
            return {}

        return _Request(run)

    def delete(self, **kw: object) -> _Request:
        self.w.log.add("drive", "permissions.delete", **kw)

        def run() -> dict:
            perms = self.w.permissions.get(kw["fileId"], [])
            perms[:] = [p for p in perms if p["id"] != kw["permissionId"]]
            return {}

        return _Request(run)

    def get(self, **kw: object) -> _Request:
        self.w.log.add("drive", "permissions.get", **kw)
        return _Request(dict)


class FakeDriveService:
    """googleapiclient の build("drive", "v3") の代わり。"""

    def __init__(self, world: DriveWorld) -> None:
        self._world = world

    def files(self) -> _Files:
        return _Files(self._world)

    def permissions(self) -> _Permissions:
        return _Permissions(self._world)


# ── Sheets (gspread) ──


def _a1_cell(a1: str) -> tuple[int, int]:
    """A1 形式のセル番地を (行, 列) にする。例: B12 → (12, 2)"""
    m = re.fullmatch(r"([A-Z]+)(\d+)", a1)
    col = 0
    for ch in m.group(1):
        col = col * 26 + (ord(ch) - ord("A") + 1)
    return int(m.group(2)), col


class _FakeResponse:
    def __init__(self, status: int, payload: dict) -> None:
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


def _api_error(status: int, message: str) -> gspread.exceptions.APIError:
    return gspread.exceptions.APIError(
        _FakeResponse(status, {"error": {"code": status, "message": message, "status": "FAILED"}})
    )


class FakeWorksheet:
    def __init__(
        self,
        spreadsheet: FakeSpreadsheet,
        sheet_id: int,
        title: str,
        rows: int = 1000,
        cols: int = 26,
    ) -> None:
        self.spreadsheet = spreadsheet
        self.id = sheet_id
        self.title = title
        self.row_count = rows
        self.col_count = cols
        self.values: list[list[str]] = []
        self.runs: dict[tuple[int, int], list] = {}
        # 行のグループ。start は 0 始まり、end はその手前まで、collapsed は畳んでいるか
        self.groups: list[dict] = []

    def _log(self, call: str, **args: object) -> None:
        self.spreadsheet.gc.log.add("sheets", call, ss=self.spreadsheet.id, ws=self.title, **args)

    # 読み取り

    def col_values(self, col: int) -> list[str]:
        self._log("col_values", col=col)
        column = [row[col - 1] if len(row) >= col else "" for row in self.values]
        while column and not column[-1]:
            column.pop()
        return column

    def row_values(self, row: int) -> list[str]:
        self._log("row_values", row=row)
        if len(self.values) < row:
            return []
        values = list(self.values[row - 1])
        while values and not values[-1]:
            values.pop()
        return values

    def get_all_values(self) -> list[list[str]]:
        self._log("get_all_values")
        width = max((len(r) for r in self.values), default=0)
        return [list(r) + [""] * (width - len(r)) for r in self.values]

    def cell(self, row: int, col: int) -> SimpleNamespace:
        self._log("cell", row=row, col=col)
        value = ""
        if len(self.values) >= row and len(self.values[row - 1]) >= col:
            value = self.values[row - 1][col - 1]
        return SimpleNamespace(value=value)

    # 書き込み

    def set_cell(self, row: int, col: int, value: str) -> None:
        while len(self.values) < row:
            self.values.append([])
        line = self.values[row - 1]
        while len(line) < col:
            line.append("")
        line[col - 1] = value

    def _range(self, first: int, last: int) -> str:
        return f"'{self.title}'!A{first}:F{last}"

    def append_row(self, values: list[str], **kw: object) -> dict:
        self._log("append_row", values=values, **kw)
        self.values.append([str(v) for v in values])
        n = len(self.values)
        return {"updates": {"updatedRange": self._range(n, n)}}

    def append_rows(self, values: list[list[str]], **kw: object) -> dict:
        self._log("append_rows", values=values, **kw)
        first = len(self.values) + 1
        self.values.extend([str(v) for v in row] for row in values)
        return {"updates": {"updatedRange": self._range(first, len(self.values))}}

    def insert_row(self, values: list[str], index: int = 1, **kw: object) -> dict:
        self._log("insert_row", values=values, index=index, **kw)
        at = index - 1
        self.values.insert(at, [str(v) for v in values])
        for group in self.groups:
            if group["start"] >= at:
                group["start"] += 1
                group["end"] += 1
            elif group["start"] < at < group["end"]:
                group["end"] += 1
        return {}

    def delete_rows(self, start_index: int, end_index: int | None = None) -> dict:
        self._log("delete_rows", start_index=start_index, end_index=end_index)
        at = start_index - 1
        del self.values[at]
        kept = []
        for group in self.groups:
            if group["start"] > at:
                group["start"] -= 1
                group["end"] -= 1
            elif group["start"] <= at < group["end"]:
                group["end"] -= 1
            if group["end"] > group["start"]:
                kept.append(group)
        self.groups = kept
        return {}

    def update_cell(self, row: int, col: int, value: object) -> dict:
        self._log("update_cell", row=row, col=col, value=value)
        self.set_cell(row, col, str(value))
        return {}

    def update(self, values: list[list[str]], range_name: str | None = None, **kw: object) -> dict:
        self._log("update", values=values, range_name=range_name, **kw)
        if range_name != "A1":
            msg = f"このフェイクは A1 からの書き込みだけを扱う: {range_name}"
            raise ValueError(msg)
        for r, row in enumerate(values, start=1):
            for c, value in enumerate(row, start=1):
                self.set_cell(r, c, str(value))
        return {}

    def batch_update(self, data: list[dict], **kw: object) -> dict:
        self._log("ws_batch_update", data=data, **kw)
        for item in data:
            row, col = _a1_cell(item["range"])
            self.set_cell(row, col, item["values"][0][0])
        return {}

    def clear(self) -> dict:
        self._log("clear")
        self.values = []
        return {}

    def update_title(self, title: str) -> dict:
        self._log("update_title", title=title)
        self.title = title
        return {}


class FakeSpreadsheet:
    def __init__(self, gc: FakeGspread, spreadsheet_id: str, title: str) -> None:
        self.gc = gc
        self.id = spreadsheet_id
        self.title = title
        self.url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
        self.sheets: list[FakeWorksheet] = [FakeWorksheet(self, 0, "Sheet1")]
        self._next_sheet_id = 100

    def _log(self, call: str, **args: object) -> None:
        self.gc.log.add("sheets", call, ss=self.id, **args)

    @property
    def sheet1(self) -> FakeWorksheet:
        self._log("sheet1")
        return self.sheets[0]

    def worksheets(self) -> list[FakeWorksheet]:
        self._log("worksheets")
        return list(self.sheets)

    def worksheet(self, title: str) -> FakeWorksheet:
        self._log("worksheet", title=title)
        for ws in self.sheets:
            if ws.title == title:
                return ws
        raise gspread.exceptions.WorksheetNotFound(title)

    def get_worksheet_by_id(self, sheet_id: int) -> FakeWorksheet:
        self._log("get_worksheet_by_id", sheet_id=sheet_id)
        return next(ws for ws in self.sheets if ws.id == sheet_id)

    def add_worksheet(
        self, title: str, rows: int, cols: int, index: int | None = None
    ) -> FakeWorksheet:
        self._log("add_worksheet", title=title, rows=rows, cols=cols, index=index)
        ws = FakeWorksheet(self, self._next_sheet_id, title, rows, cols)
        self._next_sheet_id += 1
        if index is None:
            self.sheets.append(ws)
        else:
            self.sheets.insert(index, ws)
        return ws

    def del_worksheet(self, worksheet: FakeWorksheet) -> dict:
        self._log("del_worksheet", title=worksheet.title)
        self.sheets.remove(worksheet)
        return {}

    def share(self, email: str, **kw: object) -> dict:
        self._log("share", email=email, **kw)
        return {}

    def _sheet_by_id(self, sheet_id: int) -> FakeWorksheet:
        return next(ws for ws in self.sheets if ws.id == sheet_id)

    def batch_update(self, body: dict) -> dict:
        self._log("batch_update", body=body)
        # 一括で弾かれる（atomic）ところまで実物に寄せる
        for request in body.get("requests", []):
            if "updateDimensionGroup" in request:
                span = request["updateDimensionGroup"]["dimensionGroup"]["range"]
                ws = self._sheet_by_id(span["sheetId"])
                if not any(
                    g["start"] == span["startIndex"] and g["end"] == span["endIndex"]
                    for g in ws.groups
                ):
                    raise _api_error(400, "No grouping to update on the specified range")
        for request in body.get("requests", []):
            self._apply(request)
        return {}

    def _apply(self, request: dict) -> None:
        if "updateCells" in request:
            update = request["updateCells"]
            ws = self._sheet_by_id(update["start"]["sheetId"])
            cell = update["rows"][0]["values"][0]
            row = update["start"]["rowIndex"] + 1
            col = update["start"]["columnIndex"] + 1
            ws.set_cell(row, col, cell["userEnteredValue"]["stringValue"])
            if "textFormatRuns" in cell:
                ws.runs[(row, col)] = cell["textFormatRuns"]
        elif "addDimensionGroup" in request:
            span = request["addDimensionGroup"]["range"]
            ws = self._sheet_by_id(span["sheetId"])
            start, end = span["startIndex"], span["endIndex"]
            # 実物と同じく、隣接・重なるグループには吸収される
            for group in ws.groups:
                if start <= group["end"] and end >= group["start"]:
                    group["start"] = min(group["start"], start)
                    group["end"] = max(group["end"], end)
                    return
            ws.groups.append({"start": start, "end": end, "collapsed": False})
        elif "updateDimensionGroup" in request:
            span = request["updateDimensionGroup"]["dimensionGroup"]["range"]
            ws = self._sheet_by_id(span["sheetId"])
            for group in ws.groups:
                if group["start"] == span["startIndex"] and group["end"] == span["endIndex"]:
                    dimension_group = request["updateDimensionGroup"]["dimensionGroup"]
                    group["collapsed"] = dimension_group["collapsed"]


class _FakeHttpClient:
    """gspread の http_client.request（Sheets API v4 の生 GET）。"""

    def __init__(self, gc: FakeGspread) -> None:
        self.gc = gc

    def request(
        self, method: str, url: str, params: dict | None = None, **_kw: object
    ) -> SimpleNamespace:
        self.gc.log.add("sheets", "http", method=method, url=url, params=params)
        spreadsheet_id = url.rsplit("/", 1)[-1]
        ss = self.gc.spreadsheets[spreadsheet_id]
        fields = (params or {}).get("fields", "")
        ranges = (params or {}).get("ranges", "")
        title, _, a1 = ranges.partition("!")
        ws = next(w for w in ss.sheets if f"'{w.title}'" == title)

        if "rowGroups" in fields:
            hidden = set()
            for group in ws.groups:
                if group["collapsed"]:
                    hidden.update(range(group["start"], group["end"]))
            payload = {
                "sheets": [
                    {
                        "properties": {"sheetId": ws.id},
                        "rowGroups": [
                            {
                                "range": {
                                    "sheetId": ws.id,
                                    "dimension": "ROWS",
                                    "startIndex": g["start"],
                                    "endIndex": g["end"],
                                },
                                "depth": 1,
                                "collapsed": g["collapsed"],
                            }
                            for g in ws.groups
                        ],
                        "data": [
                            {
                                "rowMetadata": [
                                    {"hiddenByUser": True} if i in hidden else {}
                                    for i in range(len(ws.values))
                                ]
                            }
                        ],
                    }
                ]
            }
        elif re.fullmatch(r"([A-Z]+):\1", a1):
            # 列まるごと（例: D:D）
            _, col = _a1_cell(a1.split(":")[0] + "1")
            rows = [{"values": [_cell_data(ws, r, col)]} for r in range(1, len(ws.values) + 1)]
            payload = {"sheets": [{"data": [{"rowData": rows}]}]}
        else:
            row, col = _a1_cell(a1)
            payload = {
                "sheets": [{"data": [{"rowData": [{"values": [_cell_data(ws, row, col)]}]}]}]
            }
        return SimpleNamespace(json=lambda: payload)


def _cell_data(ws: FakeWorksheet, row: int, col: int) -> dict:
    """Sheets API の CellData（表示値と、リンクの入ったリッチテキスト）。"""
    text = ""
    if len(ws.values) >= row and len(ws.values[row - 1]) >= col:
        text = ws.values[row - 1][col - 1]
    value: dict = {"formattedValue": text}
    if (row, col) in ws.runs:
        value["textFormatRuns"] = ws.runs[(row, col)]
    return value


class FakeGspread:
    """gspread.authorize() が返すクライアントの代わり。"""

    def __init__(self, log: CallLog, drive: DriveWorld) -> None:
        self.log = log
        self.drive = drive
        self.spreadsheets: dict[str, FakeSpreadsheet] = {}
        self.http_client = _FakeHttpClient(self)

    def add_spreadsheet(
        self,
        title: str,
        parent: str,
        spreadsheet_id: str | None = None,
        app_properties: dict | None = None,
    ) -> FakeSpreadsheet:
        spreadsheet_id = spreadsheet_id or self.drive.new_id()
        self.drive.add_file(spreadsheet_id, title, SHEET_MIME, [parent], app_properties)
        ss = FakeSpreadsheet(self, spreadsheet_id, title)
        self.spreadsheets[spreadsheet_id] = ss
        return ss

    def open_by_key(self, key: str) -> FakeSpreadsheet:
        self.log.add("sheets", "open_by_key", key=key)
        is_sheet = self.drive.files.get(key, {}).get("mimeType") == SHEET_MIME
        if key not in self.spreadsheets and is_sheet:
            self.spreadsheets[key] = FakeSpreadsheet(self, key, self.drive.files[key]["name"])
        ss = self.spreadsheets[key]
        ss.title = self.drive.files[key]["name"]
        return ss

    def create(self, title: str, folder_id: str | None = None) -> FakeSpreadsheet:
        self.log.add("sheets", "create", title=title, folder_id=folder_id)
        if "sheets.create" in self.drive.refusals:
            msg = "insufficientFilePermissions: sheets.create"
            raise RefusedError(msg)
        return self.add_spreadsheet(title, folder_id or "my-drive")


# ── Slack ──


class FakeSlackClient:
    """slack_sdk.WebClient の代わり。ページングは 2 件ずつにして cursor の経路も通す。"""

    PAGE = 2

    def __init__(self, log: CallLog, bot_user_id: str = "UBOT") -> None:
        self.log = log
        self.retry_handlers: list = []
        self.bot_user_id = bot_user_id
        self.users: dict[str, dict] = {}
        self.channels: dict[str, dict] = {}
        self.members: dict[str, list[str]] = {}
        self.history: dict[str, list[dict]] = {}
        self.replies: dict[tuple[str, str], list[dict]] = {}
        self.now: float | None = None

    def _page(self, items: list, cursor: str | None) -> tuple[list, str]:
        start = int(cursor or 0)
        end = start + self.PAGE
        return items[start:end], (str(end) if end < len(items) else "")

    def auth_test(self) -> dict:
        self.log.add("slack_reads", "auth.test")
        return {"user_id": self.bot_user_id}

    def users_info(self, user: str) -> dict:
        self.log.add("slack_reads", "users.info", user=user)
        if user not in self.users:
            msg = f"user_not_found: {user}"
            raise RuntimeError(msg)
        return {"user": copy.deepcopy(self.users[user])}

    def conversations_info(self, channel: str) -> dict:
        self.log.add("slack_reads", "conversations.info", channel=channel)
        if channel not in self.channels:
            msg = f"channel_not_found: {channel}"
            raise RuntimeError(msg)
        return {"channel": copy.deepcopy(self.channels[channel])}

    def conversations_members(self, channel: str, cursor: str | None = None, **_: object) -> dict:
        self.log.add("slack_reads", "conversations.members", channel=channel, cursor=cursor)
        page, nxt = self._page(self.members.get(channel, []), cursor)
        return {"members": page, "response_metadata": {"next_cursor": nxt}}

    def conversations_list(
        self, types: str | None = None, cursor: str | None = None, **_: object
    ) -> dict:
        self.log.add("slack_reads", "conversations.list", types=types, cursor=cursor)
        page, nxt = self._page(list(self.channels.values()), cursor)
        return {"channels": copy.deepcopy(page), "response_metadata": {"next_cursor": nxt}}

    def conversations_history(
        self,
        channel: str,
        oldest: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict:
        # 実行時刻で変わる oldest は、何日前かに丸めて記録する
        days = round((self.now - float(oldest)) / 86400) if self.now and oldest else None
        self.log.add(
            "slack_reads",
            "conversations.history",
            channel=channel,
            days=days,
            limit=limit,
            cursor=cursor,
        )
        page, nxt = self._page(self.history.get(channel, []), cursor)
        return {"messages": copy.deepcopy(page), "response_metadata": {"next_cursor": nxt}}

    def conversations_replies(self, channel: str, ts: str, limit: int | None = None) -> dict:
        self.log.add("slack_reads", "conversations.replies", channel=channel, ts=ts, limit=limit)
        return {"messages": copy.deepcopy(self.replies.get((channel, ts), []))}

    # Slack SDK のメソッド名（chat.postMessage）に合わせる必要がある
    def chat_postMessage(self, channel: str, text: str, **_: object) -> dict:  # noqa: N802
        self.log.add("slack", "chat.postMessage", channel=channel, text=text)
        return {"ok": True}


class FakeBoltApp:
    """slack_bolt.App の代わり。登録されたハンドラを名前で引けるようにする。"""

    def __init__(self, client: FakeSlackClient) -> None:
        self.client = client
        self.handlers: dict[str, object] = {}

    def event(self, name: str) -> Callable[[Callable[..., object]], Callable[..., object]]:
        def register(fn: Callable[..., object]) -> Callable[..., object]:
            self.handlers[name] = fn
            return fn

        return register


class FakeDownload:
    """requests.get の戻り値の代わり。"""

    def __init__(self, url: str) -> None:
        self.content = f"bytes of {url}".encode()

    def raise_for_status(self) -> None:
        if "broken" in self.content.decode():
            msg = "404 Not Found"
            raise requests.HTTPError(msg)


# ── ゴールデンファイル ──


def dump_golden(result: dict, path: str | Path) -> None:
    """1 呼び出し（1 行）ずつ改行した JSON にする。差分を読みやすくするため。

    値がリストのキーは要素ごとに 1 行、それ以外はそのまま 1 行で書く。
    Windows でも LF で書く（write_text は CRLF に変えてしまう）。
    """
    lines = ["{"]
    keys = list(result)
    for k, key in enumerate(keys):
        value = result[key]
        tail = "," if k < len(keys) - 1 else ""
        if isinstance(value, list):
            lines.append(f' "{key}": [')
            for i, item in enumerate(value):
                comma = "," if i < len(value) - 1 else ""
                lines.append("  " + json.dumps(item, ensure_ascii=False) + comma)
            lines.append(" ]" + tail)
        else:
            lines.append(f' "{key}": ' + json.dumps(value, ensure_ascii=False) + tail)
    lines.append("}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
