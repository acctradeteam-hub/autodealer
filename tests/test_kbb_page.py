import json
import tempfile
import unittest
from pathlib import Path

from lot_analyzer import app, kbb_page
from lot_analyzer.bid import DEFAULT_COSTS_PATH, load_costs
from tests.test_manheim_csv import HEADER


def kbb_html(miles: int, zipcode: str = "92620") -> str:
    """Страница kbb.com в том виде, как её сохраняет закладка: встроенный __NEXT_DATA__."""
    params = {"make": "subaru", "mileage": miles, "model": "forester", "trim": "25i-premium-sport-utility-4d", "year": "2017", "zipcode": zipcode}
    prices = [{"priceType": "Private Party", "condition": c, "configuredValue": v} for c, v in
              (("Fair", 10730), ("Good", 12180), ("Very Good", 12780), ("Excellent", 13280))]
    prices += [{"priceType": "Trade-In", "condition": "Good", "configuredValue": 8375},
               {"priceType": "Retail", "condition": None, "configuredValue": 14390},
               {"priceType": "FPP", "condition": None, "configuredValue": 13570}]
    data = {"props": {"apolloState": {"ROOT_QUERY": {f"valuations({json.dumps(params, separators=(',', ':'))})": {"prices": prices}}}},
            "query": {"make": "subaru", "model": "forester", "year": "2017", "trim": "25i-premium-sport-utility-4d"}}
    return ('<!DOCTYPE html>\n<!-- saved-by: lot_analyzer bookmarklet; url: https://www.kbb.com/subaru/forester/2017/25i-premium-sport-utility-4d/ -->\n'
            f'<html><head><title>Kelley Blue Book</title></head><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></body></html>')


LOT = ('Simulcast,JF2SJAGC0HH000001,2017,Subaru,Forester,2.5i Premium,Black,Gray,90000,mi,AWD,Automatic,"4 Cylinder",8200,3.8,'
       '"Dealer","Manheim California","CA - Manheim California",2026-10-07T16:00:00Z,2026-10-08T06:59:59Z,3,21,,,,,Live,')


class KbbPageTest(unittest.TestCase):
    def test_parse_private_party_by_condition(self):
        record = kbb_page.parse(kbb_html(91000))
        self.assertEqual(record["private_party"]["good"], 12180)
        self.assertEqual(record["private_party"]["verygood"], 12780)
        self.assertEqual((record["miles"], record["zip"], record["fpp"]), (91000, "92620", 13570))

    def test_page_fills_kbb_of_matching_lot_only_with_mileage(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "export.csv").write_bytes(("\r".join([HEADER, LOT]) + "\r").encode())
            old = app.KBB_PATH
            app.KBB_PATH = folder / "kbb.json"
            try:
                (folder / "KBB_2017_Subaru_Forester_2026-09-29_1936.html").write_text(kbb_html(0))
                costs = load_costs(DEFAULT_COSTS_PATH)
                row = app.search_rows(app.find_pages([folder], 24), app.PageCache(), costs, "forester")[0]
                self.assertEqual(row["kbb_private_party_usd"], "")                 # пробег не указан — не подставляем
                (folder / "KBB_2017_Subaru_Forester_2026-09-29_1940.html").write_text(kbb_html(91000))
                row = app.search_rows(app.find_pages([folder], 24), app.PageCache(), costs, "forester")[0]
                self.assertEqual(row["kbb_private_party_usd"], "12180")
                self.assertIn("страница KBB", row["kbb_entered"])
                self.assertFalse(row["calc_verdict"].startswith("НУЖЕН KBB"))
            finally:
                app.KBB_PATH = old

    def test_value_page_text_private_party(self):
        url = ("https://www.kbb.com/lexus/ct/2014/ct-200h-hatchback-4d/?vehicleid=395230&mileage=113421&intent=trade-in-sell"
               "&pricetype=private-party&condition=good&zipcode=92620")
        html = (f"<!DOCTYPE html>\n<!-- saved-by: lot_analyzer bookmarklet; saved-at: 2026-09-30T03:25:43Z; url: {url} -->\n"
                "<html><body><h1>This car’s value</h1><p>Your car's value and next steps</p><button>Trade or sell to dealer</button>"
                "<button>Sell it yourself</button><p>$11,640 - $13,510</p>"
                '<span class="svg-text"> Private Party Value $12,575 </span>'
                "<p>Mileage: Edit mileage, currently 113,421 ZIP Code: Edit ZIP code, currently 92620 Condition Good</p></body></html>")
        self.assertTrue(kbb_page.is_kbb(html))                                # без __NEXT_DATA__ — по адресу страницы
        record = kbb_page.parse(html)
        self.assertEqual((record["private_party"], record["miles"], record["zip"], record["trim"]),
                         ({"good": 12575}, 113421, "92620", "ct-200h-hatchback-4d"))
        chart = html.replace('<span class="svg-text"> Private Party Value $12,575 </span>', '<span class="svg-text"> $600$800$1000 </span>')
        self.assertEqual(kbb_page.parse(chart)["private_party"], {"good": 12575})   # чужой график не берём — середина диапазона
        trade = kbb_page.parse(html.replace("pricetype=private-party", "pricetype=trade-in"))
        self.assertEqual(trade["private_party"], {})                          # вкладка Trade-In — Private Party нет
        self.assertIn("Sell it yourself", trade["note"])

    def test_far_mileage_does_not_match(self):
        record = kbb_page.parse(kbb_html(60000))
        self.assertFalse(kbb_page.matches(record, {"year": "2017", "make": "Subaru", "model": "Forester", "odometer_miles": "90000"}, 3000))


if __name__ == "__main__":
    unittest.main()
