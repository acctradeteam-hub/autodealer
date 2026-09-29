"""«Одно окно»: подхват файлов закладки, отбор по машине, ссылки поиска, HTTP."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from lot_analyzer import app
from lot_analyzer.bid import load_costs

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
COSTS = ROOT / "config" / "costs.json"


class TestOneWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        # как будто закладка сохранила страницы в «Загрузки»
        shutil.copy(FIXTURES / "adesa_search_list.html", cls.tmp / "ADESA_ADESA_2026-09-29_1421.html")
        shutil.copy(FIXTURES / "carmax_watchlist.html", cls.tmp / "CarMax_Member_Dashboard_2026-09-29_1430.html")
        shutil.copy(FIXTURES / "manheim_search_list.html", cls.tmp / "Manheim_Search_Results_2026-09-29_1425.html")
        shutil.copy(FIXTURES / "adesa_search_list.html", cls.tmp / "notes.html")                     # не от закладки
        old = cls.tmp / "ACV_old_2026-09-01_0900.html"
        shutil.copy(FIXTURES / "acv_search_list.html", old)
        os.utime(old, (time.time() - 3 * 86400, time.time() - 3 * 86400))                            # старый файл

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp)

    def test_find_pages_only_bookmarklet_files_within_hours(self) -> None:
        names = {p.name for p in app.find_pages([self.tmp], hours=24)}
        self.assertEqual(names, {"ADESA_ADESA_2026-09-29_1421.html", "CarMax_Member_Dashboard_2026-09-29_1430.html", "Manheim_Search_Results_2026-09-29_1425.html"})
        self.assertIn("ACV_old_2026-09-01_0900.html", {p.name for p in app.find_pages([self.tmp], hours=24 * 7)})

    def test_one_car_across_auctions(self) -> None:
        rows = app.search_rows(app.find_pages([self.tmp], 24), app.PageCache(), load_costs(COSTS), "honda civic", 2013, 2016)
        self.assertEqual({r["auction"] for r in rows}, {"ADESA", "CarMax"})
        self.assertTrue(all(r["make"] == "Honda" and r["model"] == "Civic" and 2013 <= int(r["year"]) <= 2016 for r in rows))
        self.assertTrue(all(r.get("calc_verdict") for r in rows))
        ranks = [app.RANK.get(r["calc_verdict"].split(" ")[0].rstrip(":"), 5) for r in rows]
        self.assertEqual(ranks, sorted(ranks))                                                       # сначала «МОЖНО»

    def test_kbb_from_window_fills_only_missing(self) -> None:
        rows = app.search_rows(app.find_pages([self.tmp], 24), app.PageCache(), load_costs(COSTS), "toyota corolla", kbb=9000)
        self.assertTrue(rows)
        self.assertTrue(all(r["kbb_private_party_usd"] == "9000" for r in rows))
        # «Рынок» считается для всех, кроме отсеянных стоп-факторами (у них расчёт не нужен).
        self.assertTrue(all(r["market_estimate_usd"] for r in rows if not r["calc_verdict"].startswith("ПРОПУСТИТЬ")))

    def test_only_no_photos_filter(self) -> None:
        rows = app.search_rows(app.find_pages([self.tmp], 24), app.PageCache(), load_costs(COSTS), "", only_no_photos=True)
        self.assertTrue(all(r["no_photos"] == "да" for r in rows))

    def test_mileage_filter(self) -> None:
        rows = app.search_rows(app.find_pages([self.tmp], 24), app.PageCache(), load_costs(COSTS), "civic", max_miles=110000)
        self.assertTrue(rows and all(int(r["odometer_miles"]) <= 110000 for r in rows))

    def test_search_links_template_and_saved(self) -> None:
        links = {l["auction"]: l for l in app.search_links("honda civic", 2013, 2015)}
        self.assertIn("make-model-trim=Honda%3ACivic&year=2013-2015", links["ADESA"]["url"])
        self.assertNotIn("&year=-", app.search_links("honda civic", None, None)[-1]["url"])
        with tempfile.TemporaryDirectory() as tmp:
            old = app.LINKS_PATH
            app.LINKS_PATH = Path(tmp) / "links.json"
            try:
                app.save_link("Honda Civic", "ACV", "https://app.acvauctions.com/marketplace?s=%5B1%5D")
                acv = {l["auction"]: l for l in app.search_links("honda civic", None, None)}["ACV"]
                self.assertEqual((acv["url"], acv["kind"]), ("https://app.acvauctions.com/marketplace?s=%5B1%5D", "ваш сохранённый поиск"))
            finally:
                app.LINKS_PATH = old

    def test_inspection_page(self) -> None:
        from lot_analyzer.inspection import inspection_rows, render_html

        rows = app.search_rows(app.find_pages([self.tmp], 24), app.PageCache(), load_costs(COSTS), "")
        picked = inspection_rows(rows)
        self.assertTrue(picked)
        self.assertTrue(all(r["inspect"] and not r["calc_verdict"].startswith(("ПРОПУСТИТЬ", "НЕВЫГОДНО")) for r in picked))
        page = render_html(rows)
        self.assertIn("Потолок без дефекта", page)
        self.assertIn("A/70", page)                                  # Civic с «Major transmission defect»

    def test_http_api(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler([self.tmp], COSTS, app.PageCache()))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            page = urllib.request.urlopen(base + "/").read().decode("utf-8")
            self.assertIn("Одно окно", page)
            data = json.loads(urllib.request.urlopen(base + "/api/rows?q=honda+civic&y1=2013&y2=2016").read())
            self.assertTrue(data["rows"])
            self.assertEqual(len(data["files"]), 3)
            page = urllib.request.urlopen(base + "/inspection?q=").read().decode("utf-8")
            self.assertIn("Список на осмотр", urllib.request.urlopen(base + "/").read().decode("utf-8"))
            self.assertIn("На осмотр", page)
        finally:
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
