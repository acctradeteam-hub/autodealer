"""Результаты торгов: чтение «My List», печатного run list, CSV всех дорожек; сводка «цена ÷ KBB»."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from lot_analyzer import market
from lot_analyzer.bid import BidInput, calculate, load_costs

ROOT = Path(__file__).resolve().parent.parent

MY_LIST = """Auction Type Run Lane Description MyMax HighBid Status Note
CarMax Murrieta Early Bid 1 A 2014 Honda Civic LX 6800 9600 Lost KBB 11,305$ 09/27/2026
CarMax Murrieta watch & note 38 A 2011 Toyota Sienna LE 11000 Lost KBB 9,735$ 09/27/2026
CarMax Murrieta Early Bid 26 D 2015 Honda Civic3050 5000 Lost KBB 9,040$ 09/27/2026
CarMax Murrieta watch & note 34 D 2018 Mazda Mazda3 Touring7250 Lost KBB 9,390$ 09/27/2026
CarMax Murrieta note 110 D 2016 Nissan Altima (no trim) Not Run No Pictures Updated photo
"""

RUN_LIST = """CarMax Auctions Page: 1 of 3
09/21/2026 10:28 am PT
www.carmaxauctions.com
Run
Year Make Model Trim
A/4
2012 Lexus IS 350 Base
133,375
N/A
JTHFE2C22C2508921
Red
Oceanside
No announcement(s)
KBB PP $9,990-12,340
1 Owners
1 Accidents
MMR $6,550
Est Retail $13,050
Sold $8,500
A/29
2019 Hyundai Elantra SE
74,821
2WD • Automatic • 2.0L
5NPD74LF3KH457167
Gray
Oceanside
Major Engine Defect
KBB PP $9,530-10,630
MMR $6,875
Est Retail $11,300
Sold $3,200
"""

ALL_LANES = '''"CarMax Auctions - Results, both auctions, all lanes"
"Generated: Mon Sep 28 2026 11:59:54 GMT-0700 (Pacific Daylight Time)"

"DETAILED RESULTS - ALL LANES"
"Auction","Lane","Run #","Year / Vehicle","VIN","Color","Mileage","Status","Sale Price"
"CarMax Murrieta","A","1","2014 Honda Civic LX","19XFB2F56EE232054","White","73,314","Sold","$9,600"
"CarMax Murrieta","A","38","2011 Toyota Sienna LE","5TDKK3DC2BS096500","Silver","150,000","Sold","$11,000"
"CarMax Murrieta","D","26","2015 Honda Civic LX","19XFB2F58FE281841","Black","134,061","Sold","$5,000"
"CarMax Murrieta","D","34","2019 Nissan Kicks S","3N1CP5CU0KL500000","Blue","60,000","Sold","$7,250"
'''

PRICE_TABLE = '''Аукцион / Лайн,Лот #,Год,Марка,Модель / Комплектация,VIN,Цвет,"Пробег, мили",Статус,"Цена покупки, $","KBB Private Party
(92620, Good)",MMR Adjusted Price
Oceanside - Lane A,4,2012,Lexus,IS 350,JTHFE2C22C2508921,Red,133375,Sold,8500,16207.38,15768.03
'''


class TestReaders(unittest.TestCase):
    def test_my_list_numbers_including_glued_ones(self) -> None:
        rows = {r.run: r for r in market.parse_my_list(MY_LIST, "my")}
        self.assertEqual((rows["1"].my_max, rows["1"].price, rows["1"].kbb, rows["1"].status), ("6800", "9600", "11305", "Lost"))
        self.assertEqual((rows["38"].my_max, rows["38"].price), ("", "11000"))
        self.assertEqual((rows["26"].vehicle, rows["26"].my_max, rows["26"].price), ("Honda Civic", "3050", "5000"))   # «Civic3050 5000»
        self.assertEqual((rows["34"].vehicle, rows["34"].price), ("Mazda Mazda3 Touring", "7250"))                   # «Touring7250»
        self.assertEqual((rows["110"].status, rows["110"].price), ("Not Run", ""))

    def test_run_list_blocks_and_kbb_range_midpoint(self) -> None:
        rows = market.parse_run_list(RUN_LIST, "run")
        self.assertEqual(len(rows), 2)
        lexus = rows[0]
        self.assertEqual((lexus.lane, lexus.run, lexus.vin, lexus.location, lexus.date), ("A", "4", "JTHFE2C22C2508921", "Oceanside", "2026-09-21"))
        self.assertEqual((lexus.kbb, lexus.mmr, lexus.est_retail, lexus.price), ("11165", "6550", "13050", "8500"))
        self.assertEqual(rows[1].announcements, "Major Engine Defect")

    def test_price_table_ignores_computed_kbb_mmr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.csv"
            path.write_text(PRICE_TABLE, encoding="utf-8")
            rec = market.read_results(path)[0]
        self.assertEqual((rec.vin, rec.price, rec.lane, rec.run), ("JTHFE2C22C2508921", "8500", "A", "4"))
        self.assertEqual((rec.kbb, rec.mmr), ("", ""))


class TestMergeAndSummary(unittest.TestCase):
    def setUp(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        csv_path = tmp / "lanes.csv"
        csv_path.write_text(ALL_LANES, encoding="utf-8")
        self.lanes = market.read_results(csv_path)
        self.my = market.parse_my_list(MY_LIST, "my")

    def test_my_list_joins_results_by_run_only_for_same_car(self) -> None:
        merged = {f"{r.lane}/{r.run}:{r.vehicle}": r for r in market.merge(self.my, market.merge(self.lanes, []))}
        civic = merged["A/1:Honda Civic LX"]
        self.assertEqual((civic.vin, civic.kbb, civic.my_max, civic.price), ("19XFB2F56EE232054", "11305", "6800", "9600"))
        # D/34: в результатах Nissan, в My List Mazda — это разные машины, не склеиваем.
        self.assertIn("D/34:Nissan Kicks S", merged)
        self.assertIn("D/34:Mazda Mazda3 Touring", merged)
        self.assertEqual(merged["D/34:Nissan Kicks S"].kbb, "")

    def test_ratio_summary_excludes_heavy_defects(self) -> None:
        recs = [market.Result(kbb="10000", price=str(p), status="Sold") for p in (7000, 7800, 8500)]
        recs.append(market.Result(kbb="10000", price="3000", status="Sold", announcements="Major Engine Defect"))
        summary = market.ratio_summary(recs, "kbb")
        self.assertEqual((summary.n, round(summary.median, 2)), (3, 0.78))

    def test_update_config_rewrites_only_market_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "costs.json"
            shutil.copy(ROOT / "config" / "costs.json", path)
            before = path.read_text(encoding="utf-8")
            market.update_config(path, {"kbb_clean": 0.8}, {"kbb_clean": 10})
            after = path.read_text(encoding="utf-8")
            self.assertEqual(json.loads(after)["market"]["kbb_clean"], 0.8)
            self.assertEqual(len(before.splitlines()), len(after.splitlines()))


class TestMarketInVerdict(unittest.TestCase):
    def setUp(self) -> None:
        self.costs = dict(load_costs(ROOT / "config" / "costs.json"), budget_max_bid_usd=0)

    def test_clean_car_market_note(self) -> None:
        result = calculate(BidInput(auction="CarMax", kbb_private_party=10000, history_text="clean"), self.costs)
        self.assertAlmostEqual(result.market_price, 10000 * self.costs["market"]["kbb_clean"])
        self.assertIn("выиграть вряд ли", result.verdict)       # при продаже по KBB и прибыли $1500 рынок выше

    def test_higher_sale_price_gives_a_chance(self) -> None:
        result = calculate(BidInput(auction="CarMax", sale_price=12000, kbb_private_party=10000, history_text="clean"), self.costs)
        self.assertIn("шанс есть", result.verdict)

    def test_heavy_defect_uses_heavy_share(self) -> None:
        result = calculate(BidInput(auction="CarMax", kbb_private_party=10000, history_text="Major Engine Defect"), self.costs)
        self.assertAlmostEqual(result.market_price, 10000 * self.costs["market"]["kbb_heavy"])


if __name__ == "__main__":
    unittest.main()
