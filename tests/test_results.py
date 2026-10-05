import tempfile
import unittest
from pathlib import Path

from lot_analyzer import app, results
from lot_analyzer.bid import DEFAULT_COSTS_PATH, load_costs
from tests.test_manheim_csv import HEADER

LANE = ("Run #,Year/Make/Model,VIN,Color,CR,Odometer,MMR Low,MMR High,MMR Avg,Outcome,Sale Price\n"
        "3-1,2018 HONDA CIVIC LX,1HGCM82633A000001,Gray,4.0,85000,8000,10000,9000,Sold,9400\n"
        "3-2,2019 TOYOTA COROLLA LE,2T1BURHE0JC000002,Gray,4.2,60000,11000,13000,12000,IF Sale,11000\n"
        "3-3,2015 MAZDA CX-5,JM3KE000000000003,Gray,3.0,140000,5000,7000,6000,No Sale,\n")
LOTS = ['Simulcast,1HGCM82633A000001,2018,Honda,Civic,LX,Black,Gray,85000,mi,FWD,Automatic,"4 Cylinder",9000,4.0,'
        '"D","Manheim Southern California","CA - Manheim Southern California",2026-10-01T16:00:00Z,2026-10-02T06:59:59Z,3,1,,,,,Live,',
        'Simulcast,2T1BURHE0JC000002,2019,Toyota,Corolla,LE,Black,Gray,60000,mi,FWD,Automatic,"4 Cylinder",12000,4.2,'
        '"D","Manheim Southern California","CA - Manheim Southern California",2026-10-01T16:00:00Z,2026-10-02T06:59:59Z,3,2,,,,,Live,']


class ResultsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.old = results.HISTORY_PATH
        results.HISTORY_PATH = self.dir / "history.csv"

    def tearDown(self):
        results.HISTORY_PATH = self.old
        self.tmp.cleanup()

    def test_lane_csv_read_with_code_from_name(self):
        path = self.dir / "manheim_scaa_lane03_auction_results.csv"
        path.write_text(LANE, encoding="utf-8")
        self.assertTrue(results.is_lane_csv(path))
        recs = results.read_lane_csv(path)
        self.assertEqual([r["outcome"] for r in recs], ["Sold", "IF Sale", "No Sale"])
        self.assertEqual(recs[0]["auction"], "Manheim Southern California")

    def test_window_shows_result_and_keeps_history(self):
        downloads = self.dir / "Downloads"
        downloads.mkdir()
        (downloads / "export.csv").write_bytes(("\r".join([HEADER] + LOTS) + "\r").encode())
        (downloads / "manheim_scaa_lane03_auction_results.csv").write_text(LANE, encoding="utf-8")
        old_kbb = app.KBB_PATH
        app.KBB_PATH = self.dir / "kbb.json"
        try:
            rows = app.search_rows(app.find_pages([downloads], 24), app.PageCache(), load_costs(DEFAULT_COSTS_PATH), "")
        finally:
            app.KBB_PATH = old_kbb
        by_vin = {r["vin"]: r for r in rows}
        self.assertTrue(by_vin["1HGCM82633A000001"]["auction_result"].startswith("Продано $9,400 (× MMR 1.04)"))
        self.assertEqual(by_vin["1HGCM82633A000001"]["auction_result_price"], "9400")
        self.assertTrue(by_vin["2T1BURHE0JC000002"]["auction_result"].startswith("IF $11,000"))
        self.assertEqual(len(results.load_history()), 3)               # итоги сохранены в историю

    def test_location_market_used_when_enough_sales(self):
        history = {}
        for i in range(25):
            rec = {"key": f"k{i}", "date": "2026-10-01", "auction": "Manheim Southern California", "code": "SCAA", "lot": "",
                   "vin": f"V{i}", "year": "2018", "make": "HONDA", "model": "CIVIC", "miles": str(1000 + i), "cr": "",
                   "mmr": "8000", "outcome": "Sold", "price": "8800", "source": "t"}
            history[rec["key"]] = rec
        stats = results.stats(history)["Manheim Southern California"]
        self.assertTrue(stats["enough"])
        self.assertEqual(stats["median"], 1.1)
        from lot_analyzer.bid import BidInput, calculate
        costs = {**load_costs(DEFAULT_COSTS_PATH), "market_by_location": {"Manheim Southern California": stats}}
        here = calculate(BidInput(auction="Manheim", location="CA - Manheim Southern California", mmr=8000, kbb_private_party=12000), costs)
        other = calculate(BidInput(auction="Manheim", location="CA - Manheim California", mmr=8000, kbb_private_party=12000), costs)
        self.assertAlmostEqual(here.market_price, 8800)
        self.assertNotAlmostEqual(other.market_price, 8800)


if __name__ == "__main__":
    unittest.main()


class CarmaxTableTest(unittest.TestCase):
    def test_carmax_results_table_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "carmax_auction_results_10052026.csv"
            path.write_text("Location,Lane,Lot #,Year/Make/Model,VIN,Color,Mileage,Status,Sale Price (USD)\n"
                            "CarMax Oceanside,A,1,2010 Toyota Prius One,JTDKN3DUXA5152011,Gray,150387,Sold,4000\n"
                            "CarMax Roseville Auction Center,B,7,2017 Tesla Model 3 Long Range,5YJ3E1EB0HF000001,White,90000,No Sale,\n"
                            "CarMax Oceanside,A,172,2022 Hyundai Tucson Hybrid Blue,KM8JBCA1XNU057213,White,186413,Early Bid,\n", encoding="utf-8")
            self.assertTrue(results.is_results_file(path))
            recs = results.read_file(path)
        self.assertEqual([r["outcome"] for r in recs], ["Sold", "No Sale", "Early Bid"])
        self.assertEqual((recs[0]["date"], recs[0]["auction"], recs[0]["lot"], recs[0]["price"]), ("2026-10-05", "CarMax Oceanside", "A/1", "4000"))
        self.assertEqual((recs[1]["auction"], recs[1]["model"]), ("CarMax Roseville", "Model 3 Long Range"))
