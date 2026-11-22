"""Slack の添付ファイルのダウンロード。"""

from __future__ import annotations

import requests

from src.application.ports import DownloadFailedError


class SlackFileDownloader:
    """FileDownloader ポートの実装。bot トークンで非公開 URL から取得する。"""

    def __init__(self, token: str, timeout: int = 60) -> None:
        """ダウンロードに使う bot トークンと、応答を待つ秒数を受け取る。"""
        self._token = token
        self._timeout = timeout

    def download(self, url: str) -> bytes:
        """非公開 URL から取得する。取得できなければ DownloadFailedError。"""
        try:
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise DownloadFailedError(str(e)) from e
        return resp.content
