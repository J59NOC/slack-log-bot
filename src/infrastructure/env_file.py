""".env ファイルの読み書き（セットアップ用）。"""

from __future__ import annotations

import re
from pathlib import Path

from dotenv import dotenv_values

ENV_EXAMPLE = ".env.example"


class EnvFile:
    """コメントとキーの並びを残したまま書き換えられる .env。"""

    def __init__(self, path: str, template: str = ENV_EXAMPLE) -> None:
        """書き換える .env の場所と、まだ無いときのひな形の場所を受け取る。"""
        self.path = path
        # .env がまだ無いときは、これをひな形にしてコメントごと書き出す
        self.template = template

    def read(self) -> dict[str, str]:
        """今の値を読む。ファイルが無ければ空。"""
        if not Path(self.path).exists():
            return {}
        return {k: (v or "") for k, v in dotenv_values(self.path).items()}

    def write(self, values: dict[str, str]) -> None:
        """Rewrite .env, preserving comments and key order where they already exist."""
        lines: list[str] = []
        seen: set[str] = set()

        source = Path(self.path) if Path(self.path).exists() else Path(self.template)
        if source.exists():
            # UTF-8 を明示する。既定の文字コードに任せると、Windows では
            # .env.example の日本語コメントが読めない。
            for raw in source.read_text(encoding="utf-8").splitlines():
                match = re.match(r"^([A-Z_][A-Z0-9_]*)=", raw)
                if match and match.group(1) in values:
                    key = match.group(1)
                    lines.append(f"{key}={values[key]}")
                    seen.add(key)
                else:
                    lines.append(raw)

        lines.extend(f"{key}={value}" for key, value in values.items() if key not in seen)

        with Path(self.path).open("w", encoding="utf-8") as f:
            f.write("\n".join(lines).rstrip() + "\n")
        Path(self.path).chmod(0o600)
