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
        os.utime(old, (time.time() - 10 * 86400, time.time() - 10 * 86400))                          # старый файл (старше 7 дней)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp)

    def test_find_pages_only_bookmarklet_files_within_hours(self) -> None:
        names = {p.name for p in app.find_pages([self.tmp], hours=24)}
        self.assertEqual(names, {"ADESA_ADESA_2026-09-29_1421.html", "CarMax_Member_Dashboard_2026-09-29_1430.html", "Manheim_Search_Results_2026-09-29_1425.html"})
        self.assertIn("ACV_old_2026-09-01_0900.html", {p.name for p in app.find_pages([self.tmp], hours=24 * 11)})

    def test_page_from_yesterday_stays_until_its_sale(self) -> None:
        """Страница CarMax, сохранённая 2 дня назад, — в окне, хоть «Файлы за, часов» = 24: торги её машин впереди."""
        tmp = Path(tempfile.mkdtemp())
        page = tmp / "CarMax_Member_Dashboard_2026-10-16_0900.html"
        shutil.copy(FIXTURES / "carmax_watchlist.html", page)
        os.utime(page, (time.time() - 2 * 86400, time.time() - 2 * 86400))
        kbb = tmp / "KBB_2015_honda_civic_19XFB2F58FE000000.html"
        shutil.copy(FIXTURES / "carmax_watchlist.html", kbb)
        os.utime(kbb, (time.time() - 2 * 86400, time.time() - 2 * 86400))
        self.assertEqual([p.name for p in app.find_pages([tmp], hours=24)], [page.name])     # страница KBB — по «часам»
        undated = {"sale_date": "", "_saved": str(time.time() - 2 * 86400)}
        app.mark_passed([undated])
        self.assertIn("без даты торгов", undated["passed"])
        shutil.rmtree(tmp)

    def test_one_car_across_auctions(self) -> None:
        rows = app.search_rows(app.find_pages([self.tmp], 24), app.PageCache(), load_costs(COSTS), "honda civic", 2013, 2016)
        self.assertEqual({r["auction"] for r in rows}, {"ADESA", "CarMax"})
        self.assertTrue(all(r["make"] == "Honda" and r["model"] == "Civic" and 2013 <= int(r["year"]) <= 2016 for r in rows))
        self.assertTrue(all(r.get("calc_verdict") for r in rows))
        ranks = [app.RANK.get(r["calc_verdict"].split(" ")[0].rstrip(":"), 5) for r in rows]
        self.assertEqual(ranks, sorted(ranks))                                                       # сначала «МОЖНО»

    def test_kbb_from_window_fills_only_missing(self) -> None:
        from lot_analyzer import analytics
        analytics.KBB_LOG_PATH.unlink(missing_ok=True)       # своя база KBB пустая: иначе KBB этих машин — из неё
        rows = app.search_rows(app.find_pages([self.tmp], 24), app.PageCache(), load_costs(COSTS), "toyota corolla", kbb=9000)
        self.assertTrue(rows)
        self.assertTrue(all(r["kbb_private_party_usd"] == "9000" for r in rows))
        # «Рынок» считается для всех, кроме отсеянных стоп-факторами (у них расчёт не нужен).
        self.assertTrue(all(r["market_estimate_usd"] for r in rows if not r["calc_verdict"].startswith("ПРОПУСТИТЬ")))

    def test_kbb_entered_in_window_by_vin(self) -> None:
        costs = load_costs(COSTS)
        with tempfile.TemporaryDirectory() as tmp:
            old = app.KBB_PATH
            app.KBB_PATH = Path(tmp) / "kbb.json"
            try:
                rows = app.search_rows(app.find_pages([self.tmp], 24), app.PageCache(), costs, "")
                target = next(r for r in rows if r.get("vin") and not r.get("kbb_private_party_usd") and r["calc_max_bid_usd"])
                self.assertTrue(target["calc_verdict"].startswith("НУЖЕН KBB"))          # без настоящего KBB — только предварительно
                app.save_kbb(target["vin"], 14250, target["odometer_miles"])
                again = next(r for r in app.search_rows(app.find_pages([self.tmp], 24), app.PageCache(), costs, "") if r["vin"] == target["vin"])
                self.assertEqual(again["kbb_private_party_usd"], "14250")
                self.assertFalse(again["calc_verdict"].startswith("НУЖЕН KBB"))
                app.save_kbb(target["vin"], None)
                self.assertEqual(app.load_kbb(), {})
            finally:
                app.KBB_PATH = old


    def test_saved_copy_of_own_window_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            own = Path(tmp) / "auction_page_2026-09-29_2041.html"
            own.write_text("<!DOCTYPE html>\n<!-- saved-by: lot_analyzer bookmarklet; saved-at: x; url: http://127.0.0.1:8765/ -->\n<html></html>")
            shutil.copy(FIXTURES / "adesa_search_list.html", Path(tmp) / "ADESA_ADESA_2026-09-29_1421.html")
            self.assertEqual([p.name for p in app.find_pages([Path(tmp)], 24)], ["ADESA_ADESA_2026-09-29_1421.html"])

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
        self.assertIn("Моя оценка", page)
        self.assertNotIn("Почему в списке", page)
        self.assertIn("A/70", page)                                  # Civic с «Major transmission defect»
        self.assertIn("Убрать из списка", page)                      # перед печатью позиции можно убрать
        self.assertIn("Вернуть всё", page)

    def test_inspection_one_group_per_place_and_day(self) -> None:
        """PDF-список даёт «Oct 19» или без даты, сайт — «Oct 19, 9:00am PT»: Oceanside всё равно одним списком."""
        import re
        from lot_analyzer.inspection import render_html

        base = {"auction": "CarMax", "inspect": "объявлен Major Engine Defect", "calc_verdict": "МОЖНО"}
        rows = [dict(base, vin="V1", lot_number="A/1", location="Oceanside, CA", sale_date="Oct 19, 9:00am PT"),
                dict(base, vin="V2", lot_number="A/2", location="Oceanside, CA", sale_date="Oct 19"),
                dict(base, vin="V3", lot_number="A/3", location="Oceanside, CA", sale_date=""),
                dict(base, vin="V4", lot_number="B/1", location="Murrieta, CA", sale_date="Oct 19, 9:00am PT")]
        heads = re.findall(r"<h2>(.*?)</h2>", render_html(rows))
        self.assertEqual(heads, ["CarMax Murrieta · Oct 19, 9:00am PT", "CarMax Oceanside · Oct 19, 9:00am PT"])

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


class PopularTest(unittest.TestCase):
    def test_popular_models(self):
        from lot_analyzer.bid import DEFAULT_COSTS_PATH, load_costs
        from lot_analyzer.popular import is_popular
        costs = load_costs(DEFAULT_COSTS_PATH)
        yes = [("2014", "Honda", "Civic", "LX"), ("2018", "Toyota", "Camry", "Hybrid LE"), ("2016", "MAZDA", "MAZDA3", "i Sport"),
               ("2019", "Honda", "CR-V", "Hybrid EX"), ("2017", "Toyota", "RAV4", "Hybrid XLE"), ("2015", "Toyota", "Prius", "Two"),
               ("2022", "Tesla", "Model 3", "Standard Range"), ("2020", "Mazda", "CX-5", "Touring"), ("2012", "Toyota", "Corolla", "LE"),
               ("2014", "Lexus", "CT", "CT 200h"), ("2013", "Lexus", "CT 200h", "Base"), ("2016", "Lexus", "RX 450h", ""), ("2018", "Lexus", "RX", "350"),
               ("2016", "Lexus", "IS", "200t Base"), ("2014", "Lexus", "IS 250", ""), ("2017", "Lexus", "ES 350", ""), ("2020", "Lexus", "ES", "300h"), ("2018", "Honda", "Clarity Plug-in Hybrid", "Base"), ("2017", "Honda", "Clarity", "Electric"),
               ("2019", "Honda", "Insight", "EX"), ("2012", "Mazda", "Mazda3", "i Touring"),
               ("2018", "Lexus", "GX", "460"), ("2019", "Lexus", "NX", "300"), ("2015", "Lexus", "LS 460", "")]   # все Lexus
        no = [("2021", "Toyota", "Corolla Cross", "LE"), ("2022", "Tesla", "Model 3", "Long Range"), ("2023", "Tesla", "Model 3", "Standard Range"),
              ("2018", "Honda", "Civic Type R", "Touring"), ("2015", "Toyota", "Prius v", "Three"), ("2020", "Mazda", "CX-30", ""), ("2016", "Ford", "Focus", "SE"),
              ("2011", "Mazda", "Mazda3", "s Sport")]
        for y, mk, md, tr in yes:
            self.assertTrue(is_popular({"year": y, "make": mk, "model": md, "trim": tr}, costs), md)
        for y, mk, md, tr in no:
            self.assertFalse(is_popular({"year": y, "make": mk, "model": md, "trim": tr}, costs), f"{y} {md} {tr}")


class PickupTest(unittest.TestCase):
    def test_pickups(self):
        from lot_analyzer.popular import is_pickup
        for mk, md, tr in [("Ford", "F-150", "XLT"), ("Ram", "1500", "Big Horn"), ("Chevrolet", "Silverado 2500HD", "LT"),
                           ("Toyota", "Tacoma", "SR5"), ("GMC", "Sierra 1500", ""), ("Honda", "Ridgeline", "RTL"), ("Jeep", "Gladiator", "Sport")]:
            self.assertTrue(is_pickup({"make": mk, "model": md, "trim": tr}), md)
        for mk, md, tr in [("Toyota", "RAV4", "XLE"), ("Ford", "Explorer", "XLT"), ("Jeep", "Wrangler", "Sport"), ("BMW", "3 Series", "330i")]:
            self.assertFalse(is_pickup({"make": mk, "model": md, "trim": tr}), md)


class PlaceTest(unittest.TestCase):
    def test_place_names_and_filter(self):
        self.assertEqual(app.place_of_row({"auction": "CarMax", "location": "Murrieta"}), "CarMax Murrieta")
        self.assertEqual(app.place_of_row({"auction": "CarMax", "location": "Chino, CA"}), "CarMax Chino")
        self.assertEqual(app.place_of_row({"auction": "Manheim", "location": "CA - Manheim California"}), "Manheim California")
        self.assertEqual(app.place_of_row({"auction": "Manheim", "location": "CA - SANTA ANA"}), "Manheim — у продавца (не на площадке)")
        rows = [{"place": "CarMax Murrieta", "kbb_private_party_usd": "9000"}, {"place": "CarMax Murrieta"}, {"place": "CarMax Oceanside"}]
        only, places = app.by_place(rows, {"place": "CarMax Murrieta"})
        self.assertEqual(len(only), 2)
        self.assertEqual(places, [["CarMax Murrieta", 2], ["CarMax Oceanside", 1]])
        self.assertEqual(len(app.by_place(rows, {"place": "CarMax Murrieta", "picked": "1"})[0]), 1)
        rows.append({"place": "CarMax Chino"})
        self.assertEqual(len(app.by_place(rows, {"place": "CarMax Murrieta|CarMax Chino"})[0]), 3)    # несколько площадок сразу
        self.assertEqual(len(app.by_place(rows, {"place": "CarMax Oceanside|CarMax Chino"})[0]), 2)


class PassedTest(unittest.TestCase):
    def test_auction_passed_after_noon(self):
        import datetime as dt
        from lot_analyzer.schedule import passed
        saved = dt.datetime(2026, 10, 4, 15, 52)                          # список сохранён в воскресенье
        monday_morning, monday_noon = dt.datetime(2026, 10, 5, 11, 0), dt.datetime(2026, 10, 5, 12, 30)
        self.assertEqual(passed({"sale_date": "Oct 5, 9:00am PT"}, saved, monday_morning), "")
        self.assertEqual(passed({"sale_date": "Oct 5, 9:00am PT"}, saved, monday_noon), "")      # день торгов — весь день в окне (сверка)
        self.assertTrue(passed({"sale_date": "Oct 5, 9:00am PT"}, saved, dt.datetime(2026, 10, 6, 0, 5)))   # на следующий день — прошли
        self.assertEqual(passed({"sale_date": "Oct 6, 9:00am PT"}, saved, monday_noon), "")      # завтрашние — остаются
        self.assertTrue(passed({"sale_date": "Ends Mon 10/05 11:00 a.m."}, saved, monday_morning.replace(hour=11, minute=30)))
        self.assertEqual(passed({"sale_date": "Make Offer", "lot_status": "Make Offer"}, saved, monday_noon), "")
        self.assertTrue(passed({"sale_date": "16:48:07"}, saved, monday_noon))                   # ACV: отсчёт от сохранения

    def test_stats_targets(self):
        from lot_analyzer.bid import DEFAULT_COSTS_PATH, load_costs
        from lot_analyzer.popular import is_stats_target
        costs = load_costs(DEFAULT_COSTS_PATH)
        self.assertTrue(is_stats_target({"year": "2023", "make": "Tesla", "model": "Model 3", "trim": "Long Range"}, costs))
        self.assertTrue(is_stats_target({"year": "2014", "make": "Honda", "model": "Civic", "trim": "LX"}, costs))
        self.assertFalse(is_stats_target({"year": "2020", "make": "Tesla", "model": "Model 3", "trim": "Long Range"}, costs))


class PageScriptTest(unittest.TestCase):
    def test_window_script_has_no_syntax_errors(self):
        """Ошибка в скрипте окна гасит всё окно (кнопки аукционов, список) — ловим её до выпуска."""
        import re
        import shutil
        import subprocess
        import tempfile
        from lot_analyzer import app
        node = shutil.which("node")
        if not node:
            self.skipTest("нет node")
        scripts = re.findall(r"<script>(.*?)</script>", app.PAGE, re.S)
        self.assertTrue(scripts)
        for code in scripts:
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
                handle.write(code)
            result = subprocess.run([node, "--check", handle.name], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


class ExtensionVersionTest(unittest.TestCase):
    def test_window_knows_extension_version(self):
        import json
        from pathlib import Path
        from lot_analyzer import app
        manifest = json.loads((Path(__file__).resolve().parent.parent / "tools" / "kbb_extension" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], app.EXTENSION_VERSION)

    def test_no_announcements_is_not_a_remark(self):
        from lot_analyzer.notes import lot_note
        note = lot_note({"kbb_private_party_usd": "12850", "kbb_date": "2026-10-05", "auction": "CarMax", "auction_notes": "no announcements"})
        self.assertEqual(note, "KBB 12,850$ 10/5/26")


class CarmaxTableWithDateTest(unittest.TestCase):
    def test_new_export_with_date_column(self):
        """CarMax добавил колонку «Date» — файл всё равно итоги торгов (и уходит Claude)."""
        import tempfile
        from pathlib import Path
        from lot_analyzer import results
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "carmax_auction_results_full_10062026.csv"
            path.write_text("Location,Lane,Lot #,Date,Year/Make/Model,VIN,Color,Mileage,Status,Sale Price (USD)\n"
                            "CarMax Chino Auction Center,A,1,10/6/2026,2025 Land Rover Range Rover Velar S,SALYJ2EX1SA392364,Black,20902,Sold,31000\n",
                            encoding="utf-8")
            self.assertTrue(results.is_results_file(path))
            rec = results.read_file(path)[0]
            self.assertEqual((rec["date"], rec["auction"], rec["lot"], rec["make"], rec["price"]),
                             ("2026-10-06", "CarMax Chino", "A/1", "Land Rover", "31000"))


class HybridTest(unittest.TestCase):
    def test_hybrid_detection_and_own_model(self):
        from lot_analyzer.bid import is_hybrid
        from lot_analyzer.normalize import split_model
        for model, trim in [("Civic Hybrid", "Base"), ("Prius", "Two"), ("Accord", "Plug in Hybrid"), ("ES", "300h Base"), ("Insight", "EX"), ("Clarity", "Plug in Hybrid")]:
            self.assertTrue(is_hybrid({"make": "Honda", "model": model, "trim": trim}), model)
        for make, model, trim in [("Honda", "Civic", "LX"), ("Tesla", "Model 3", ""), ("Chevrolet", "Bolt EV", "LT"), ("Lexus", "ES", "350")]:
            self.assertFalse(is_hybrid({"make": make, "model": model, "trim": trim}), model)
        self.assertEqual(split_model("Civic Hybrid Base"), ("Civic Hybrid", "Base"))

    def test_high_miles_hybrid_gets_battery_reserve_and_market_factor(self):
        from lot_analyzer.bid import BidInput, DEFAULT_COSTS_PATH, calculate, expected_market_price, load_costs
        costs = load_costs(DEFAULT_COSTS_PATH)
        costs["market"] = {**(costs.get("market") or {}), "kbb_clean": 0.8}
        costs["hybrid"] = {"high_miles": 130000, "battery_reserve_usd": 750, "market_factor_default": 0.9}
        base = dict(auction="CarMax", kbb_private_party=8000, consider=True)
        plain = calculate(BidInput(**base, miles=135000), costs)
        hybrid = calculate(BidInput(**base, miles=135000, hybrid=True), costs)
        young = calculate(BidInput(**base, miles=60000, hybrid=True), costs)
        self.assertEqual(plain.max_bid - hybrid.max_bid, 750)
        self.assertEqual(plain.max_bid, young.max_bid)
        price, note = expected_market_price(BidInput(**base, miles=135000, hybrid=True), costs)
        self.assertAlmostEqual(price, 8000 * 0.72)
        self.assertIn("гибрид", note)


class ManheimAllPagesTest(unittest.TestCase):
    def test_listings_from_api_in_any_key_order(self):
        """Закладка собирает Manheim из API: поля объявления могут идти в любом порядке."""
        import json
        from lot_analyzer import manheim
        items = [{"id": f"Simulcast.RAA.{i}", "vin": f"1HGCM82633A00{i:04d}", "source": "Simulcast", "year": 2015} for i in range(3)]
        page = ('<html><body><div id="lot-analyzer-all-pages" style="display:none">'
                + "".join(f'<div class="StockWaveInfo stockwave-vehicle-info">{json.dumps(x)}</div>' for x in items) + "</div></body></html>")
        self.assertEqual([x["vin"] for x in manheim.find_listings(page)], [x["vin"] for x in items])


class TmuTest(unittest.TestCase):
    def test_tmu_is_flagged_not_skipped(self):
        """TMU / Not actual miles — пометка «особое внимание», а не «пропустить»."""
        from lot_analyzer.bid import DEFAULT_COSTS_PATH, assess_history, load_costs
        costs = load_costs(DEFAULT_COSTS_PATH)
        flags = assess_history("Not actual miles, Prior lease", costs)
        self.assertEqual(flags.skip, [])
        self.assertTrue(any("TMU" in n for n in flags.notes))
        self.assertTrue(assess_history("Odometer rollback", costs).skip)


class ManheimLanesTest(unittest.TestCase):
    def test_all_lanes_export(self):
        import tempfile
        from pathlib import Path
        from lot_analyzer import results
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Manheim_California_All_13_Lanes_Auction_Results.csv"
            path.write_text("Lane,Lane Name,Lot,Year/Make/Model,VIN,Color (Ext/Int),CR,Odometer,Engine,Trans,Adj MMR Range,Adj MMR,Result\n"
                            '01,Lane 01 - TRA,97-1,2019 TOYOTA CAMRY LE,4T1B11HK0KU000001,Gray / Gry,4.1,61000,4G,AT,"$15,950 - $17,500","$16,700",Sold $15200\n'
                            '01,Lane 01 - TRA,97-2,2013 HONDA CIVIC LX,19XFB2F50DE000002,Black / Blk,3.2,140000,4G,AT,"$5,700 - $8,450","$7,075","IF Sale $5,500"\n',
                            encoding="utf-8")
            self.assertTrue(results.is_results_file(path))
            a, b = results.read_file(path)
            self.assertEqual((a["auction"], a["outcome"], a["price"], a["mmr"], a["make"]), ("Manheim California", "Sold", "15200", "16700", "Toyota"))
            self.assertEqual((b["outcome"], b["price"]), ("If Sale", "5500"))


class SavedTimeTest(unittest.TestCase):
    def test_copied_old_page_stays_old(self):
        """Скопированный (с новым временем файла) старый список не становится «свежим»: время — из заголовка или имени."""
        import tempfile
        import time
        from pathlib import Path
        from lot_analyzer import app
        with tempfile.TemporaryDirectory() as tmp:
            header = Path(tmp) / "CarMax_Search_page.html"
            header.write_text("<!DOCTYPE html>\n<!-- saved-by: lot_analyzer bookmarklet; version: 2026-10-07.1; saved-at: 2026-10-01T18:00:00.000Z; url: x -->\n<html></html>")
            named = Path(tmp) / "CarMax_Search_2026-10-01_1757.html"
            named.write_text("<html></html>")
            plain = Path(tmp) / "CarMax_Search.html"
            plain.write_text("<html></html>")
            self.assertLess(app.saved_time_from_file(header), time.time() - 86400)
            self.assertLess(app.saved_time_from_file(named), time.time() - 86400)
            self.assertAlmostEqual(app.saved_time_from_file(plain), plain.stat().st_mtime)


class RemovedFavoritesListTest(unittest.TestCase):
    def test_unstarred_car_leaves_main_list(self):
        """Сняли звёздочку на CarMax — машина уходит из общего списка в «☆ Убранные из избранного», окно узнаёт сразу."""
        tmp = Path(tempfile.mkdtemp())
        shutil.copy(FIXTURES / "carmax_watchlist.html", tmp / "CarMax_Member_Dashboard_2026-09-29_1430.html")
        app.FAVORITES_PATH.unlink(missing_ok=True)
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler([tmp], COSTS, app.PageCache()))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            before = json.loads(urllib.request.urlopen(base + "/api/rows?passed=1").read())
            files_before = urllib.request.urlopen(base + "/api/files").read()
            vin = next(r["vin"] for r in before["rows"] if r.get("vin"))
            self.assertEqual(app.remove_favorites([{"vin": vin}]), 1)
            after = json.loads(urllib.request.urlopen(base + "/api/rows?passed=1").read())
            removed = json.loads(urllib.request.urlopen(base + "/api/rows?passed=1&removed=1").read())
            self.assertNotIn(vin, [r["vin"] for r in after["rows"]])
            self.assertEqual([r["vin"] for r in removed["rows"]], [vin])
            self.assertEqual(after["removed_total"], 1)
            self.assertNotEqual(urllib.request.urlopen(base + "/api/files").read(), files_before)   # окно пересчитает сразу
        finally:
            server.shutdown()
            app.FAVORITES_PATH.unlink(missing_ok=True)
            shutil.rmtree(tmp)


class PhotoWaitTest(unittest.TestCase):
    def test_wait_pictures_then_arrived(self):
        from lot_analyzer import photo_wait
        data = {}
        day1, day2 = time.mktime((2026, 10, 22, 14, 0, 0, 0, 0, -1)), time.mktime((2026, 10, 23, 12, 0, 0, 0, 0, -1))
        row = {"vin": "WBAPH5G5XBNM00001", "no_photos": "да"}
        self.assertTrue(photo_wait.update([row], data, now=day1))
        self.assertEqual(row["photo_wait"], "2026-10-22")
        row = {"vin": "WBAPH5G5XBNM00001", "no_photos": ""}
        self.assertTrue(photo_wait.update([row], data, now=day2))
        self.assertEqual((row.get("photo_wait"), row["photo_arrived"]), (None, "2026-10-23"))
        row = {"vin": "WBAPH5G5XBNM00001", "no_photos": ""}
        photo_wait.update([row], data, now=day2 + 8 * 86400)      # через неделю пометка уходит
        self.assertNotIn("photo_arrived", row)


class OwnersTest(unittest.TestCase):
    def test_owners_saved_needed_and_shown(self):
        app.OWNERS_PATH.unlink(missing_ok=True)
        try:
            rows = [{"auction": "CarMax", "vin": "1HGCR2F30FA000001", "group": "popular", "calc_verdict": "МОЖНО до $5,000", "calc_profit_market_usd": "900"},
                    {"auction": "CarMax", "vin": "1HGCR2F30FA000002", "group": "other", "calc_verdict": "ПРОПУСТИТЬ: рама"},
                    {"auction": "Manheim", "vin": "1HGCR2F30FA000003", "group": "popular", "calc_verdict": "МОЖНО"}]
            self.assertEqual(app.owners_needed(rows), ["1HGCR2F30FA000001"])
            self.assertEqual(app.save_owners([{"vin": "1HGCR2F30FA000001", "owners": 3, "note": "Number of Owners 3"}]), 1)
            self.assertEqual(app.save_owners([{"vin": "1HGCR2F30FA000001", "owners": 0, "note": "не нашёл"}]), 0)   # не затирает
            self.assertEqual(app.load_owners()["1HGCR2F30FA000001"]["owners"], 3)
            self.assertEqual(app.owners_needed(rows), [])
            app.OWNERS_SCOPE.update(vins=["1HGCR2F30FA000001", "1HGCR2F30FA000009"], at=time.time())   # список окна
            self.assertEqual(app.owners_needed(rows), ["1HGCR2F30FA000009"])          # известные — пропускаем
        finally:
            app.OWNERS_SCOPE.update(vins=[], at=0.0)
            app.OWNERS_PATH.unlink(missing_ok=True)


class SaleDateFromOlderPageTest(unittest.TestCase):
    def test_newest_page_without_date_takes_it_from_older(self):
        """Свежая страница (watch list) без даты торгов, на вчерашней — «Oct 26»: машина не прячется как «без даты»."""
        from unittest import mock
        vin = "JTDS4RCE7LJ000001"
        old = {"auction": "CarMax", "vin": vin, "year": "2020", "make": "Toyota", "model": "Corolla", "sale_date": "Oct 26, 9:00am PT"}
        new = {**old, "sale_date": ""}
        tmp = Path(tempfile.mkdtemp())
        p_old, p_new = tmp / "CarMax_Search_2026-10-22_2047.html", tmp / "CarMax_Search_2026-10-23_1215.html"
        p_old.write_text("x"), p_new.write_text("x")
        cache = app.PageCache()
        with mock.patch.object(cache, "rows", side_effect=lambda p: [dict(new if p == p_new else old)]):
            (row,) = app._search_rows([p_new, p_old], cache, load_costs(COSTS), "")
        self.assertEqual(row["sale_date"], "Oct 26, 9:00am PT")
        self.assertNotIn("без даты", row.get("passed", ""))
        shutil.rmtree(tmp)


class PrintListNoPicturesTest(unittest.TestCase):
    def test_no_pictures_in_print_list_with_notes(self):
        from lot_analyzer.inspection import render_html
        base = {"auction": "CarMax", "location": "Murrieta, CA", "sale_date": "Oct 26, 9:00am PT", "favorite_known": "1"}
        rows = [dict(base, vin="V1", lot_number="A/1", year="2015", make="Honda", model="Civic", no_photos="да", calc_verdict="НУЖЕН KBB",
                     site_notes="KBB 9,000$ 10/22/26\nправая дверь помята", kbb_private_party_usd="9100", kbb_date="2026-10-25"),
                dict(base, vin="V2", lot_number="A/2", no_photos="да", calc_verdict="ПРОПУСТИТЬ: нет титула"),     # не смотрим
                dict(base, vin="V3", lot_number="A/3", no_photos="да", calc_verdict="МОЖНО", favorite_removed="1"),  # сняли звёздочку
                dict(base, vin="V4", lot_number="A/4", calc_verdict="МОЖНО", favorite="1", my_proxy_usd="5200")]
        page = render_html(rows)
        self.assertIn("data-id='V1'", page)
        self.assertIn("NO PICTURES", page)
        self.assertIn("Мои заметки: правая дверь помята", page)
        self.assertNotIn("Мои заметки: KBB", page)            # строка программы — не «моя»
        self.assertNotIn("data-id='V2'", page)
        self.assertNotIn("data-id='V3'", page)
        self.assertIn("data-id='V4'", page)
        self.assertIn("$5,200", page)                          # моя ставка (MP)


class MyBidsFilterTest(unittest.TestCase):
    def test_only_cars_with_my_bid_by_profit(self):
        tmp = Path(tempfile.mkdtemp())
        shutil.copy(FIXTURES / "carmax_watchlist.html", tmp / "CarMax_Member_Dashboard_2026-09-29_1430.html")
        old_bids = app.BIDS_PATH.read_bytes() if app.BIDS_PATH.exists() else None
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler([tmp], COSTS, app.PageCache()))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            allrows = json.loads(urllib.request.urlopen(base + "/api/rows?passed=1").read())["rows"]
            vins = [r["vin"] for r in allrows if r.get("vin")][:3]
            app.BIDS_PATH.write_text(json.dumps({v: {"usd": 1000 + 500 * i} for i, v in enumerate(vins)}), encoding="utf-8")
            got = json.loads(urllib.request.urlopen(base + "/api/rows?passed=1&bids=1&sort=market").read())
            rows = got["rows"]
            had_mp = [r["vin"] for r in allrows if r.get("my_proxy_usd")]     # ставка уже в заметке (MP)
            self.assertEqual(sorted(r["vin"] for r in rows), sorted(set(vins) | set(had_mp)))
            profits = [float(r.get("profit_at_my_bid_usd") or -1e9) for r in rows]
            self.assertEqual(profits, sorted(profits, reverse=True))     # сверху — самая прибыльная при ставке
            lanes = json.loads(urllib.request.urlopen(base + "/api/rows?passed=1&sort=lane").read())["rows"]
            keys = [(app.place_of_row(r), *(r.get("lot_number") or "~/999999").split("/")) for r in lanes if r.get("group") == "popular"]
            keys = [(pl, a.strip(), int(b)) for pl, a, b in keys if b.strip().isdigit()]
            self.assertEqual(keys, sorted(keys))                         # по площадкам; внутри — A/1, A/2 … B/1 …
        finally:
            server.shutdown()
            if old_bids is None:
                app.BIDS_PATH.unlink(missing_ok=True)
            else:
                app.BIDS_PATH.write_bytes(old_bids)
            shutil.rmtree(tmp)


class VoiceNotesTest(unittest.TestCase):
    def test_list_to_phone_and_notes_back(self):
        """«📱 На телефон» кладёт список в закрытый репозиторий; заметки с телефона — в строки и в Notes («Голос: …»)."""
        import base64
        from unittest import mock
        from lot_analyzer import inbox, voice
        from lot_analyzer.notes import lot_note, strip_own
        voice.LOCAL_PATH.unlink(missing_ok=True)
        calls = []
        notes_file = {"notes": {"1hgcr2f30fa000001": {"text": "коробка переключается мягко", "tags": ["дефекта нет", "беру"]}}}

        def fake(method, url, token, body=None):
            calls.append((method, url.split("/contents/")[-1], body))
            if method == "GET" and url.endswith("voice/notes.json"):
                return 200, {"content": base64.b64encode(json.dumps(notes_file).encode()).decode()}
            return (404, {}) if method == "GET" else (201, {})
        rows = [{"auction": "CarMax", "vin": "1HGCR2F30FA000001", "lot_number": "A/5", "place": "CarMax Murrieta", "favorite_known": "1",
                 "favorite": "1", "year": "2015", "make": "Honda", "model": "Accord", "kbb_private_party_usd": "9100", "calc_verdict": "МОЖНО"}]
        with mock.patch.object(inbox, "load_settings", return_value={"repo": "me/inbox", "token": "t"}), \
             mock.patch.object(inbox, "_is_private", return_value=True), mock.patch.object(inbox, "_request", side_effect=fake):
            self.assertEqual(voice.push_list(rows), {"ok": True, "cars": 1})
            sent = json.loads(base64.b64decode(calls[-1][2]["content"]))
            self.assertEqual((calls[-1][1], sent["cars"][0]["lot"], sent["cars"][0]["car"]), ("voice/list.json", "A/5", "2015 Honda Accord"))
            self.assertTrue(voice.pull())
        voice.attach(rows)
        self.assertEqual(rows[0]["voice_notes"], "дефекта нет, беру · коробка переключается мягко")
        self.assertIn("Голос: дефекта нет, беру · коробка переключается мягко", lot_note(rows[0]))
        self.assertEqual(strip_own("мой текст\nГолос: беру"), "мой текст")
        with mock.patch.object(inbox, "load_settings", return_value={"repo": "me/inbox", "token": "t"}), \
             mock.patch.object(inbox, "_is_private", return_value=False):
            self.assertFalse(voice.push_list(rows)["ok"])            # открытый репозиторий — никогда
        voice.LOCAL_PATH.unlink(missing_ok=True)


class NoPicturesNoteTest(unittest.TestCase):
    def test_photo_on_page_beats_old_no_pictures_note(self):
        from lot_analyzer.parsers import empty_row
        from lot_analyzer.sites_text import row_from_carmax_card
        base = {"vin": "1HGCR2F87DA000001", "title": "2013 Honda Accord EX-L", "lane_run": "B/128", "location": "Murrieta, CA",
                "miles": "120000", "announcements": "", "notes": "no pictures", "start": "Oct 26, 9:00am PT"}
        row = empty_row()
        row_from_carmax_card(row, dict(base, photo="https://img2.carmax.com/assets/1/image/1.jpg"))
        self.assertNotEqual(row.get("no_photos"), "да")        # фото появились — заметка устарела
        row = empty_row()
        row_from_carmax_card(row, dict(base, photo=""))
        self.assertEqual(row.get("no_photos"), "да")
        row = empty_row()
        row_from_carmax_card(row, dict(base, photo="x"))       # по странице не видно — верим заметке
        self.assertEqual(row.get("no_photos"), "да")


class NewerPageWinsTest(unittest.TestCase):
    def test_photos_added_today_not_no_photo(self):
        """Вчера фото не было, сегодня появились: фильтр «без фото» не берёт вчерашнюю строку этой машины."""
        from unittest import mock
        vin = "WBAPH5G5XBNM00001"
        old = {"auction": "CarMax", "vin": vin, "year": "2011", "make": "BMW", "model": "328i", "no_photos": "да", "source_file": "old"}
        new = {**old, "no_photos": "", "photo_main_url": "https://img2.carmax.com/x/1.jpg", "source_file": "new"}
        tmp = Path(tempfile.mkdtemp())
        p_old, p_new = tmp / "CarMax_Search_2026-10-22_1408.html", tmp / "CarMax_Search_2026-10-23_1248.html"
        p_old.write_text("x"), p_new.write_text("x")
        cache = app.PageCache()
        with mock.patch.object(cache, "rows", side_effect=lambda p: [dict(new if p == p_new else old)]):
            rows = app._search_rows([p_new, p_old], cache, load_costs(COSTS), "", only_no_photos=True)
        self.assertEqual([r["vin"] for r in rows], [])
        shutil.rmtree(tmp)


class CarmaxNoPhotoTest(unittest.TestCase):
    def test_check_back_for_images_means_no_photos(self):
        from lot_analyzer.sites_text import find_carmax_cards
        card = ('<html><body>carmax<div id="28670552"><button><span>Check back for images</span></button>'
                '<p class="MuiTypography-caption">B/119 • Oxnard, CA</p><button>2014 Chevrolet Sonic LT</button>'
                '<p><span>1G1JC5SH6E4000000</span><button data-testid="copy-vin-button"></button></p><p>98,000 mi</p></div></body></html>')
        cards = find_carmax_cards(card)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["photo"], "")


class KbbFromOwnBaseTest(unittest.TestCase):
    def test_kbb_from_base_by_vin_when_page_is_gone(self):
        """KBB, полученный раньше, подставляется по VIN из своей базы — страницы KBB в «Загрузках» уже нет."""
        import datetime as dt
        from lot_analyzer import analytics, app
        from lot_analyzer.bid import DEFAULT_COSTS_PATH, load_costs
        tmp = Path(tempfile.mkdtemp())
        (tmp / "ADESA_ADESA_2026-09-29_1421.html").write_bytes((FIXTURES / "adesa_civic_lot.html").read_bytes())
        pages = app.find_pages([tmp], 24)
        if not pages:
            self.skipTest("нет образца страницы")
        row = next(r for r in app.PageCache().rows(pages[0]) if r.get("vin"))
        row["kbb_private_party_usd"] = ""
        log = {row["vin"]: {"vin": row["vin"], "date": dt.date.today().isoformat(), "year": row["year"], "make": row["make"], "model": row["model"],
                            "trim": "", "miles": row["odometer_miles"], "kbb": "11111", "mmr": "", "auction": "", "location": "", "remarks": "", "source": "test"}}
        analytics.save_kbb_log(log)
        rows = app.search_rows(pages, app.PageCache(), load_costs(DEFAULT_COSTS_PATH))
        hit = next(r for r in rows if r["vin"] == row["vin"])
        self.assertEqual(hit["kbb_private_party_usd"], "11111")
        self.assertIn("из вашей базы KBB", hit["kbb_entered"])


class PreKbbTest(unittest.TestCase):
    def test_preliminary_kbb_from_similar_in_own_base(self):
        from lot_analyzer import analytics
        from lot_analyzer.bid import DEFAULT_COSTS_PATH, load_costs
        costs = load_costs(DEFAULT_COSTS_PATH)
        log = {f"VIN{i:014d}": {"vin": f"VIN{i:014d}", "date": "2026-10-01", "year": "2016", "make": "Honda", "model": "Civic", "trim": "LX",
                                "miles": "100000", "kbb": "10000", "mmr": "", "auction": "", "location": "", "remarks": "", "source": "t"} for i in range(3)}
        log["TESLA000000000001"] = {**log["VIN00000000000000"], "vin": "TESLA000000000001", "make": "Tesla", "model": "Model X", "kbb": "30000"}
        est = analytics.PreKbb(log, costs)
        civic = est.estimate("Honda", "Civic", 2016, 100000, vin="NEW")
        self.assertAlmostEqual(civic.value, 10000)
        self.assertIn("вашей базе KBB", civic.source)
        self.assertLess(est.estimate("Honda", "Civic", 2016, 150000, vin="NEW").value, 10000)      # больше пробег — дешевле
        self.assertIsNone(est.estimate("Tesla", "Model 3", 2016, 100000, vin="NEW"))             # Model X — не похожая на Model 3


class SkipOrderTest(unittest.TestCase):
    def test_structural_above_branded_above_no_title(self):
        from lot_analyzer.app import verdict_rank
        structural = verdict_rank("ПРОПУСТИТЬ: повреждение рамы / кузова")
        branded = verdict_rank("ПРОПУСТИТЬ: брендированный титул (salvage/rebuilt/lemon…)")
        no_title = verdict_rank("ПРОПУСТИТЬ: повреждение рамы / кузова, нет титула на руках (Title Absent) — продать нельзя")
        self.assertLess(structural, branded)
        self.assertLess(branded, no_title)
        self.assertLess(no_title, verdict_rank("ПРОДАНА за $5,000 — купить нельзя"))


class PastSalesNoteTest(unittest.TestCase):
    def test_past_sales_go_to_auction_notes(self):
        from lot_analyzer.notes import lot_note, strip_own
        row = {"kbb_private_party_usd": "6610", "kbb_date": "2026-10-05", "auction": "CarMax", "auction_notes": "Prior lease",
               "seen_before": "был на аукционе 9/27/26 CarMax Murrieta — итога нет; был на аукционе 9/28/26 CarMax Murrieta — продана $1,600; "
                              "был на аукционе 10/5/26 CarMax Murrieta — не продана"}
        self.assertEqual(lot_note(row), "KBB 6,610$ 10/5/26\nБыл: 9/28/26 CarMax Murrieta — продана $1,600\nБыл: 10/5/26 CarMax Murrieta — не продана\nCarMax: Prior lease")
        # Без KBB и ставки — тоже пишем, если машина уже была на торгах.
        self.assertEqual(lot_note({"seen_before": "был на аукционе 9/28/26 CarMax Chino — продана $10,250"}), "Был: 9/28/26 CarMax Chino — продана $10,250")
        self.assertEqual(strip_own("мой текст\nБыл: 9/28/26 CarMax Chino — продана $10,250"), "мой текст")


class ReturnedAfterSaleTest(unittest.TestCase):
    def test_car_sold_before_and_back_on_sale(self):
        """Продана 10/5, снова на торгах 10/19 — «вернулась после продажи»; итог самого лота 10/19 — не «раньше»."""
        import datetime as dt
        from lot_analyzer import app, results
        old = results.HISTORY_PATH.read_bytes() if results.HISTORY_PATH.exists() else None
        vin = "19XFC2F50GE000001"
        hist = {}
        results.merge(hist, [
            {"key": f"CarMax Murrieta|2026-10-05|{vin}", "date": "2026-10-05", "auction": "CarMax Murrieta", "lot": "A/1", "vin": vin, "outcome": "Sold", "price": "7000"},
            {"key": f"CarMax Murrieta|2026-10-19|{vin}", "date": "2026-10-19", "auction": "CarMax Murrieta", "lot": "A/117", "vin": vin, "outcome": "Sold", "price": "4500"}])
        results.save_history(hist)
        try:
            saved = dt.datetime(2026, 10, 18, 12).timestamp()
            row = {"vin": vin, "sale_date": "Oct 19, 9:00am PT", "_saved": saved, "auction": "CarMax"}
            undated = {"vin": vin, "sale_date": "", "_saved": dt.datetime(2026, 10, 5, 8).timestamp(), "auction": "CarMax"}
            app.attach_results([row, undated], [], app.PageCache(), {})
            self.assertEqual((row["returned"], row["returned_price"]), ("10/5/26 CarMax Murrieta — продана $7,000", "7000"))
            self.assertEqual(undated["returned"], "")          # сохранена в день своих торгов — свой итог не «раньше»
        finally:
            if old is None:
                results.HISTORY_PATH.unlink(missing_ok=True)
            else:
                results.HISTORY_PATH.write_bytes(old)


class CarmaxRunListPdfTest(unittest.TestCase):
    def test_runlist_pdf_is_ignored(self):
        """«CarMaxList (8).pdf» — печать готового списка с сайта CarMax: ни машин, ни итогов из него не берём.
        Данные выдуманные: настоящие PDF в открытый репозиторий не кладём."""
        from unittest import mock
        from lot_analyzer import app, market
        fake = [market.Result(date="2026-10-10", auction="CarMax", location="Murrieta", lane="A", run="44", year="2008",
                              vehicle="Honda Accord EX", vin="1HGCP36708A000000", miles="194172",
                              announcements="Major Engine Defect", notes="", kbb="", mmr="", est_retail="", my_max="",
                              price="", status="", source="x.pdf")]
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "CarMaxList (8).pdf"
        path.write_bytes(b"%PDF-1.4 test")
        with mock.patch.object(market, "read_results", return_value=fake):
            cache = app.PageCache()
            self.assertEqual(cache.rows(path), [])
            self.assertIsNone(cache.kbb(path))                   # и в базу итогов не идёт
            self.assertEqual(app.find_pages([tmp], 24), [])


class VelocicastExportTest(unittest.TestCase):
    def test_export_csv_read_as_results(self):
        """«velocicast-auction-detailed-….csv»: SOLD / NOSALE, цена, день торгов по времени ставок (Калифорния). Данные выдуманные."""
        from lot_analyzer import results
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "Carmax_auctions_ results_19102026.csv"
        path.write_text("site,laneId,run,finalStatus,finalAmount,highMoney,bidFirstTs,year,make,model,trim,vin,miles,announcement,myNote,myPrebid\n"
                        "CarMax Oceanside,14104,97,SOLD,2750,,2026-10-20T01:10:00Z,2004,Lexus,RX 330,(no trim),JTJHA31U000000001,128341,"
                        "MAJOR ENGINE DEFECT | PRIOR RENTAL,\"KBB 5,140$ 10/16/26\",2800\n"
                        "CarMax Roseville Auction Center,14063,5,NOSALE,0,4600,,2015,Honda,Civic,LX,19XFB2F50FE000002,90000,,,\n", encoding="utf-8")
        self.assertTrue(results.is_results_file(path))
        sold, nosale = results.read_file(path)
        self.assertEqual((sold["date"], sold["auction"], sold["outcome"], sold["price"], sold["kbb"]), ("2026-10-19", "CarMax Oceanside", "Sold", "2750", "5140"))
        self.assertIn("Major engine defect", sold["remarks"])
        self.assertIn("ваша представка $2,800", sold["remarks"])
        self.assertEqual((nosale["auction"], nosale["outcome"], nosale["price"], nosale["date"]), ("CarMax Roseville", "No Sale", "", "2026-10-19"))
        self.assertIn("высшая ставка $4,600", nosale["remarks"])


class CarmaxRuTableTest(unittest.TestCase):
    def test_russian_headers_table(self):
        """«Carmax_results_10202026.csv» с русскими колонками: продана / не продана / не торговалась (Pending). Данные выдуманные."""
        from lot_analyzer import results
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "Carmax_results_10202026.csv"
        path.write_text('"Аукцион","Лейн","Лот","Год","Марка","Модель","Полное наименование","VIN","Цвет","Пробег (миль)","Пробег число",'
                        '"Статус","Продана/Не продана","Цена продажи","Цена число","Покупатель","Бейдж покупателя"\n'
                        '"CarMax Chino Auction Center","A","1","2014","Volkswagen","Jetta TDI","2014 Volkswagen Jetta TDI","3VWLL7AJ1EM000001","Silver","115,358","115358","Sold","Продана","$1,600","1600","",""\n'
                        '"CarMax Fremont","A","2","2015","Honda","Civic LX","2015 Honda Civic LX","19XFB2F50FE000002","Black","90,000","90000","Pending","Не торговалась","","","",""\n',
                        encoding="utf-8")
        self.assertTrue(results.is_results_file(path))
        sold, pending = results.read_file(path)
        self.assertEqual((sold["date"], sold["auction"], sold["lot"], sold["outcome"], sold["price"], sold["miles"]),
                         ("2026-10-20", "CarMax Chino", "A/1", "Sold", "1600", "115358"))
        self.assertEqual((pending["outcome"], pending["price"]), ("нет итога", ""))


class KbbLearningTest(unittest.TestCase):
    def test_prelim_then_real_kbb_is_a_lesson(self):
        """Прикидка $10,000 запомнена; пришёл настоящий KBB $9,000 — «прикидка была ≈ $10,000 (+11%)», поправка ×0.9."""
        from lot_analyzer import kbb_learning
        data = {"prelim": {}, "learned": {}}
        cars = [(f"1HGCR2F3XFA00000{i}", 10000, 9000) for i in range(5)]
        for vin, est, _ in cars:
            self.assertTrue(kbb_learning.update([{"vin": vin, "make": "Honda", "model": "Accord", "kbb_estimate_usd": str(est)}], data))
        self.assertEqual(len(data["prelim"]), 5)
        row = {}
        for vin, _, real in cars:
            row = {"vin": vin, "make": "Honda", "model": "Accord", "kbb_private_party_usd": str(real)}
            kbb_learning.update([row], data)
        self.assertEqual((row["kbb_prelim_usd"], row["kbb_prelim_pct"]), ("10000", "+11"))
        self.assertEqual((len(data["prelim"]), len(data["learned"])), (0, 5))
        table = kbb_learning.factors(data["learned"])
        self.assertAlmostEqual(table["*"][0], 0.9)
        factor, note = kbb_learning.correction(table, "Honda", "Accord EX")
        self.assertAlmostEqual(factor, 0.9)
        self.assertIn("этой модели", note)
        self.assertEqual(kbb_learning.summary(data["learned"]), {"n": 5, "bias": 11, "abs": 11, "factor": 0.9})


class KbbMilesCapTest(unittest.TestCase):
    def test_low_miles_not_extrapolated(self):
        """Corolla 2022 с 14 тыс. миль по похожим со 100 тыс.: не ×2, а не больше ×1.6."""
        from lot_analyzer.kbb import Comp, KbbEstimator
        est = KbbEstimator([], {})
        value = est._adjust(Comp("Toyota", "Corolla", 2022, 100000, 15000, ""), 2022, 14000)
        self.assertLessEqual(value, 15000 * 1.6 + 1)
        self.assertAlmostEqual(est._adjust(Comp("Toyota", "Corolla", 2022, 100000, 15000, ""), 2022, 100000), 15000)


class ReviewTest(unittest.TestCase):
    def test_forecast_frozen_and_compared_with_results(self):
        import datetime as dt
        from lot_analyzer import review
        review.FORECASTS_PATH.unlink(missing_ok=True)
        row = {"vin": "1HGCM82633A000001", "auction": "CarMax", "location": "Oceanside, CA", "lot_number": "A/1", "year": "2014",
               "make": "Honda", "model": "Accord", "sale_date": "Oct 5, 9:00am PT", "_saved": dt.datetime(2026, 10, 4, 12).timestamp(),
               "kbb_private_party_usd": "10000", "market_estimate_usd": "8000", "calc_max_bid_usd": "8500", "calc_profit_market_usd": "1000",
               "calc_verdict": "МОЖНО до $8,500; …", "my_proxy_usd": "8200"}
        self.assertTrue(review.remember([row], now=dt.datetime(2026, 10, 4, 20)))
        self.assertFalse(review.remember([{**row, "market_estimate_usd": "9999"}], now=dt.datetime(2026, 10, 6, 9)))   # после торгов — заморожен
        history = {"k": {"vin": row["vin"], "date": "2026-10-05", "auction": "CarMax Oceanside", "outcome": "Sold", "price": "7600"}}
        (block,) = review.compare(review.load(), history)
        self.assertEqual((block["place"], block["day"], block["sold"]), ("CarMax Oceanside", "2026-10-05", 1))
        self.assertAlmostEqual(block["err_median"], 0.05)
        (f, r, profit, _), = block["could"]
        self.assertEqual(profit, 1400)                          # 1000 + (8000 − 7600)
        (_, _, enough, _), = block["bids"]
        self.assertTrue(enough)
        self.assertIn("Разбор торгов", review.render(history))


class CarmaxSiteCsvTest(unittest.TestCase):
    def test_auction_site_export(self):
        from lot_analyzer import results
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "carmax_auction_results_Oceanside__Murietta_10122026.csv"
        path.write_text('Auction_Site,Lane,Lot,Year,Make,Model,Mileage,Status,Price,VIN\n'
                        '"Murrieta","A","1","2021","Nissan","Sentra S","49,162","Sold","$8,200","3N1AB8BV9MY000001"\n'
                        '"Murietta","B","7","2015","Honda","Civic LX","90,000","Sold (You Win)","$6,100","19XFB2F50FE000002"\n', encoding="utf-8")
        self.assertTrue(results.is_results_file(path))
        a, b = results.read_file(path)
        self.assertEqual((a["auction"], a["date"], a["lot"], a["price"]), ("CarMax Murrieta", "2026-10-12", "A/1", "8200"))
        self.assertEqual((b["auction"], b["outcome"], b["remarks"]), ("CarMax Murrieta", "Sold", "купили вы"))


class FavoritesTest(unittest.TestCase):
    def test_star_on_carmax_adds_car_and_asks_kbb(self):
        from lot_analyzer import app
        from lot_analyzer.bid import DEFAULT_COSTS_PATH, load_costs
        app.FAVORITES_PATH.unlink(missing_ok=True)
        costs = load_costs(DEFAULT_COSTS_PATH)
        answer = app.add_favorites([{"vin": "1HGCR2F35FA000001", "title": "2015 Honda Accord LX", "miles": "120,500",
                                     "lane_run": "A/5", "location": "Murrieta, CA", "stock": "123456", "start": "Oct 20, 9:00am PT"},
                                    {"vin": "1HGCR2F35FA000002", "title": "2015 Honda Accord LX", "miles": "120,500", "notes": "KBB 9,100$ 10/14/26"}], costs)
        self.assertEqual((answer["added"], answer["need_kbb"]), (2, 1))           # у второй KBB уже в заметке
        self.assertIn("kbb.com/honda/accord/2015", answer["url"])
        rows = {r["vin"]: r for r in app.favorite_rows(set())}
        self.assertEqual(rows["1HGCR2F35FA000001"]["lot_number"], "A/5")
        self.assertEqual(rows["1HGCR2F35FA000002"]["kbb_private_party_usd"], "9100")
        app.FAVORITES_PATH.unlink(missing_ok=True)

    def test_star_removed_drops_car_from_print_list_and_notes(self):
        """Сняли звёздочку на CarMax — машины нет в списке на печать и заметку ей не пишем; поставили снова — вернулась."""
        from lot_analyzer import app
        from lot_analyzer.inspection import inspection_rows
        app.FAVORITES_PATH.unlink(missing_ok=True)
        vin = "1HGCR2F35FA000003"
        row = {"auction": "CarMax", "vin": vin, "carmax_favorite": "1", "site_notes": "KBB 9,100$ 10/14/26"}
        page_time = time.time() - 3600
        app.set_favorite(row, page_time, None)
        self.assertEqual((row["favorite"], row["favorite_known"]), ("1", "1"))
        self.assertEqual(len(inspection_rows([row])), 1)
        self.assertEqual(app.remove_favorites([{"vin": vin}]), 1)
        row = {"auction": "CarMax", "vin": vin, "carmax_favorite": "1", "site_notes": "KBB 9,100$ 10/14/26"}
        app.set_favorite(row, page_time, app.load_favorites()[vin])      # щелчок свежее страницы
        self.assertEqual((row["favorite"], row.get("favorite_removed")), ("", "1"))
        self.assertEqual(inspection_rows([row]), [])
        self.assertFalse(app.favorite_rows(set()))
        # страница, сохранённая после щелчка, со звёздочкой — снова в избранном
        row = {"auction": "CarMax", "vin": vin, "carmax_favorite": "1"}
        app.set_favorite(row, time.time() + 5, app.load_favorites()[vin])
        self.assertEqual(row["favorite"], "1")
        # заметка есть, а звёздочки на странице нет — убрали сами
        row = {"auction": "CarMax", "vin": "1HGCR2F35FA000004", "carmax_favorite": "0", "site_notes": "KBB 5,000$"}
        app.set_favorite(row, page_time, None)
        self.assertEqual(row.get("favorite_removed"), "1")
        app.FAVORITES_PATH.unlink(missing_ok=True)

    def test_star_state_read_from_saved_carmax_card(self):
        from bs4 import BeautifulSoup
        from lot_analyzer.sites_text import _carmax_star
        on = BeautifulSoup('<div id="70139392"><button data-click-event="FavoriteRemove" data-vehicle-id="70139392"></button></div>', "lxml").div
        off = BeautifulSoup('<div id="70139393"><button data-click-event="FavoriteAdd" data-vehicle-id="70139393"></button></div>', "lxml").div
        self.assertEqual((_carmax_star(on), _carmax_star(off), _carmax_star(BeautifulSoup("<div></div>", "lxml").div)), ("1", "0", ""))
