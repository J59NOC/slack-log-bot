"""Sheets API のレート制限に当たったときの再試行。"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, TypeVar

import gspread

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Sheets allows 60 read + 60 write requests per minute per user. A busy
# workspace blows through that, so every API call gets backed off and retried.
MAX_RETRIES = 5
INITIAL_BACKOFF = 2.0
# 429 means the request was rejected outright, so retrying can never duplicate
# a write. 5xx is ambiguous, so it is only retried for idempotent operations.
RATE_LIMIT_STATUS = (429,)
TRANSIENT_STATUS = (429, 500, 502, 503, 504)


def retry(fn: Callable[..., T], *args: object, idempotent: bool = True, **kwargs: object) -> T:
    """Call a gspread/Sheets operation, backing off on rate limits."""
    retry_on = TRANSIENT_STATUS if idempotent else RATE_LIMIT_STATUS
    delay = INITIAL_BACKOFF
    for attempt in range(MAX_RETRIES - 1):
        try:
            return fn(*args, **kwargs)
        # 失敗を受けて待ち、やり直すこと自体がこの関数の役目なので、ループの中で受ける
        except gspread.exceptions.APIError as e:  # noqa: PERF203
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status not in retry_on:
                raise
            logger.warning(
                "Sheets API %s on %s, retrying in %.0fs (%s/%s)",
                status,
                getattr(fn, "__name__", fn),
                delay,
                attempt + 1,
                MAX_RETRIES - 1,
            )
            time.sleep(delay)
            delay *= 2
    # 最後の 1 回は、失敗したらそのまま投げる
    return fn(*args, **kwargs)
