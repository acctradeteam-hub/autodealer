"""Закладка: собранная строка совпадает с исходником и остаётся валидным JavaScript-URL."""

from __future__ import annotations

import html
import importlib.util
import unittest
from pathlib import Path
from urllib.parse import unquote

DIR = Path(__file__).resolve().parent.parent / "tools" / "bookmarklet"
spec = importlib.util.spec_from_file_location("bookmarklet_build", DIR / "build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class TestBookmarklet(unittest.TestCase):
    def setUp(self) -> None:
        self.source = (DIR / "save_auction_page.js").read_text(encoding="utf-8")
        self.url = (DIR / "bookmarklet.txt").read_text(encoding="utf-8").strip()

    def test_built_file_is_up_to_date(self) -> None:
        self.assertEqual(self.url, build.build_url(self.source), "запустите python3 tools/bookmarklet/build.py")
        page = (DIR / "install.html").read_text(encoding="utf-8")
        self.assertIn(f'href="{html.escape(self.url, quote=True)}"', page, "install.html устарел — запустите build.py")

    def test_single_line_javascript_url(self) -> None:
        self.assertTrue(self.url.startswith("javascript:(function"))
        self.assertNotIn("\n", self.url)
        self.assertNotIn(" ", self.url)
        code = unquote(self.url[len("javascript:"):])
        self.assertNotIn("/*", code)

    def test_privacy_rules_in_source(self) -> None:
        self.assertIn("'password'", self.source)       # пароли не сохраняются
        self.assertIn("script, style", self.source)     # скрипты выбрасываются
        # Никуда ничего не отправляет: единственный запрос — чтение страниц того же kbb.com (автопилот KBB),
        # без POST и без чужих адресов.
        self.assertEqual(self.source.count("fetch("), 1)
        self.assertIn("return fetch(url, { credentials: 'include' })", self.source)
        self.assertIn("var url = '/' + slugOf(car.mk)", self.source)             # адрес — путь на том же сайте
        self.assertNotIn("method:", self.source)
        self.assertNotIn("XMLHttpRequest", self.source)
        self.assertNotIn("sendBeacon", self.source)


if __name__ == "__main__":
    unittest.main()
