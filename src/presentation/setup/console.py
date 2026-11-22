"""セットアップの画面表示と入力。"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from collections.abc import Callable


def step(n: int, title: str) -> None:
    """ステップの見出しを出す。"""
    print(f"\n\033[1m[{n}/4] {title}\033[0m")


def ok(msg: str) -> None:
    """うまくいったことを出す。"""
    print(f"  \033[32m✓\033[0m {msg}")


def warn(msg: str) -> None:
    """注意を出す（続行する）。"""
    print(f"  \033[33m!\033[0m {msg}")


def fail(msg: str) -> NoReturn:
    """失敗を標準エラーに出して終了する。"""
    print(f"  \033[31m✗\033[0m {msg}", file=sys.stderr)
    sys.exit(1)


def ask(prompt: str, validate: Callable[[str], str | None] | None = None) -> str:
    """空でない値を入力してもらう。validate が問題を返したら聞き直す。"""
    while True:
        try:
            value = input(f"  {prompt}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            fail("中断しました。")
        if not value:
            print("    (空欄にはできません)")
            continue
        if validate:
            problem = validate(value)
            if problem:
                print(f"    {problem}")
                continue
        return value
