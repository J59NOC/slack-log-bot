"""レイヤー間の依存の向きと、入口・設定まわりの約束事のテスト。"""

import ast
import unittest
from pathlib import Path

from src.infrastructure.settings import BASE_DIR
from src.presentation.commands import Backfill, ShowHelp, ShowUrl, Unknown, parse_mention

SRC = Path(__file__).resolve().parents[1] / "src"

# 各レイヤーが import してよいレイヤー（自分自身は常に可）
ALLOWED = {
    "domain": set(),
    "application": {"domain"},
    # ポート（application/ports.py）を実装するため application も参照できる
    "infrastructure": {"domain", "application"},
    "presentation": {"domain", "application", "infrastructure", "bootstrap"},
}


def _imported_layers(path: Path) -> set[str]:
    """ファイルが import している src 内のレイヤー名。"""
    package = path.relative_to(SRC).parts[:-1]
    layers = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - node.level + 1]
                target = list(base) + (node.module.split(".") if node.module else [])
            elif node.module and node.module.startswith("src."):
                target = node.module.split(".")[1:]
            else:
                continue
            names = target or [alias.name for alias in node.names]
            layers.add(names[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("src."):
                    layers.add(alias.name.split(".")[1])
    return layers


class LayerTests(unittest.TestCase):
    def test_dependencies_point_inwards(self) -> None:
        for layer, allowed in ALLOWED.items():
            for path in (SRC / layer).rglob("*.py"):
                with self.subTest(path=str(path.relative_to(SRC))):
                    used = _imported_layers(path) - {layer}
                    self.assertLessEqual(used, allowed)

    def test_google_modules_used_by_setup_never_load_settings(self) -> None:
        # setup.py は .env が揃う前にこれらを import する。設定の読み込みは
        # 足りない値があると終了するので、ここから呼ばれてはいけない。
        for name in ("auth.py", "drive.py", "provisioning.py", "sheets/guide.py"):
            source = (SRC / "infrastructure" / "google" / name).read_text(encoding="utf-8")
            self.assertNotIn("settings", source, name)


class SettingsTests(unittest.TestCase):
    def test_base_dir_is_the_checkout_root(self) -> None:
        # profiles/ と client_secret.json はここから探す。ずれると全プロファイルが
        # 「SLACK_BOT_TOKEN が設定されていません」で止まる。
        self.assertTrue((BASE_DIR / "pyproject.toml").exists())
        self.assertTrue((BASE_DIR / "main.py").exists())


class MentionCommandTests(unittest.TestCase):
    def test_commands(self) -> None:
        self.assertEqual(parse_mention("<@U1> url"), ShowUrl())
        self.assertEqual(parse_mention("<@U1|logbot> URL"), ShowUrl())
        self.assertEqual(parse_mention("<@U1> help"), ShowHelp())
        self.assertEqual(parse_mention("<@U1>"), ShowHelp())
        self.assertEqual(parse_mention("<@U1> なにか"), Unknown())

    def test_backfill_days(self) -> None:
        self.assertEqual(parse_mention("<@U1> backfill"), Backfill(90))
        self.assertEqual(parse_mention("<@U1> backfill 30"), Backfill(30))
        self.assertEqual(parse_mention("<@U1> backfill abc"), Backfill(90))


if __name__ == "__main__":
    unittest.main()
