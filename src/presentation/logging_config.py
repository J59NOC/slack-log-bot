"""ログ出力の設定（各入口で一度だけ呼ぶ）。"""

from __future__ import annotations

import logging
import sys

FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def configure_logging(log_file: str = "") -> None:
    """ログを標準出力（と、指定があればファイル）に出すよう設定する。"""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format=FORMAT, handlers=handlers)
