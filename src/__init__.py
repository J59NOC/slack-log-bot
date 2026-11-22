"""Slack Log Bot 本体。

ドメイン駆動設計（DDD）のレイヤーに分けてある。依存は外側から内側への一方向で、
domain は他のどのレイヤーも import しない。

    presentation    Slack のイベントハンドラと CLI。入力を解釈してユースケースを呼ぶ
    application     ユースケース。ドメインの規則とポート（外部への口）を組み合わせる
    domain          チャンネル・メッセージ・添付ファイルと、その規則
    infrastructure  Google Sheets / Drive / Slack API を使ったポートの実装

部品の組み立ては src/bootstrap.py が受け持つ。
"""

import sys

# The codebase uses `X | None` annotations, which 3.9 raises a TypeError on at
# import time. macOS ships 3.9 as /usr/bin/python3, so a venv built with a bare
# `python3` lands there and fails with no hint about the cause.
if sys.version_info < (3, 10):  # noqa: UP036
    msg = (
        f"[Python のバージョンエラー] Python 3.10 以上が必要です"
        f"（現在: {sys.version.split()[0]} / {sys.executable}）\n"
        f"  macOS の /usr/bin/python3 は 3.9 です。仮想環境を作り直してください:\n"
        f"    rm -rf .venv && uv venv --python 3.13 .venv\n"
        f"    uv pip install --python .venv/bin/python -r requirements.txt"
    )
    raise SystemExit(msg)
