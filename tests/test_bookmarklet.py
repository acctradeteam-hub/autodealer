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
        self.assertIn("return fetch(url, { credentials: 'include', signal: stop.signal })", self.source)
        self.assertNotIn("prompt(", self.source)                                 # автопилот не ждёт ответов на невидимой вкладке
        self.assertIn("var base = '/' + slugOf(car.mk)", self.source)            # адрес — путь на том же сайте
        self.assertIn("var path = '/' + slugOf(car.mk)", self.source)
        self.assertNotIn("method:", self.source)
        self.assertNotIn("XMLHttpRequest", self.source)
        self.assertNotIn("sendBeacon", self.source)

    def test_carmax_loads_all_vehicles(self) -> None:
        # CarMax: жмём «Show more / Показать следующие», фильтры «More 2» (aria-label «see more») не трогаем.
        self.assertIn("auction === 'CarMax'", self.source)
        self.assertIn("show|load|see|view", self.source)
        self.assertIn("следующие машины", self.source)
        self.assertIn("/^see more$/i.test(label)", self.source)
        self.assertIn("go to next page", self.source)                      # постраничный список
        self.assertIn("hzn-button", self.source)                           # кнопки CarMax (web components)
        self.assertIn("save(null, null, cmOrder.length)", self.source)

    def test_version_matches_window(self) -> None:
        import re
        from lot_analyzer.app import BOOKMARKLET_VERSION
        self.assertEqual(re.search(r"var LA_VERSION = '([\d-]+)';", self.source).group(1), BOOKMARKLET_VERSION)
        self.assertIn("saved-by: lot_analyzer bookmarklet; version: ' + LA_VERSION", self.source)

    def test_kbb_trim_rules(self) -> None:
        # «Range» не решает (Standard / Long Range у Tesla), дорогие версии — только если они у лота; не нашли — базовая.
        self.assertIn("w !== 'range'", self.source)
        self.assertIn("var premium = /^(long|performance|plaid|dual|awd", self.source)
        self.assertIn("комплектация по умолчанию", self.source)
        # ACV / ADESA: весь список, по ссылкам на лоты
        self.assertIn("var lotList = function", self.source)

    def test_carmax_hidden_cards_are_parsed(self) -> None:
        # Карточки, убранные сайтом при прокрутке, закладка кладёт в скрытый блок — разбор их видит.
        import re
        from lot_analyzer.sites_text import find_carmax_cards
        page = (Path(__file__).parent / "fixtures" / "carmax_watchlist.html").read_text(encoding="utf-8")
        before = find_carmax_cards(page)
        vin = before[0]["vin"]
        start = page.index('copy-vin-button')
        card_id = re.findall(r'<div[^>]* id="(\d{5,})"', page[:start])[-1]
        open_at = page.rindex(f'id="{card_id}"', 0, start)
        open_at = page.rindex("<div", 0, open_at)
        depth, i = 0, open_at
        for m in re.finditer(r"<(/?)div\b", page[open_at:]):
            depth += -1 if m.group(1) else 1
            if depth == 0:
                i = open_at + m.end()
                break
        card = page[open_at:page.index(">", i) + 1]
        extra = card.replace(vin, "1HGCM82633A999999").replace(f'id="{card_id}"', 'id="99999999"')
        saved = page.replace("</body>", f'<div id="lot-analyzer-carmax-all" style="display:none">{extra}</div></body>')
        after = find_carmax_cards(saved)
        self.assertEqual(len(after), len(before) + 1)
        self.assertIn("1HGCM82633A999999", [c["vin"] for c in after])


if __name__ == "__main__":
    unittest.main()
