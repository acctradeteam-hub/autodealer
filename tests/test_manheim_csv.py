import tempfile
import unittest
from pathlib import Path

from lot_analyzer import manheim_csv
from lot_analyzer.bid import BidInput, calculate, grade_recon, load_costs, DEFAULT_COSTS_PATH, market_config

HEADER = ("Inventory,Vin,Year,Make,Model,Trim,Interior Color,Exterior Color,Odometer Value,Odometer Units,Drivetrain,"
          "Transmission Type,Engine Type,MMR,Condition Report Grade,Seller Name,Auction House,Pickup Location,Starts At,"
          "Ends At,Lane,Run,Bid Amount,Buy Now Price,Event Sale Name,Seller Comments,Status,Notes")
LIVE = ('Simulcast,1HGCM82633A000001,2018,Honda,Civic,LX,Black,Gray,85000,mi,FWD,Automatic,"4 Cylinder",9000,3.2,'
        '"Some Dealer","Manheim California","CA - Manheim California",2026-09-30T16:00:00Z,2026-10-01T06:59:59Z,5,12,,,,'
        '"AS IS NO ARBITRATION",Live,')
TIMED = ('Timed Sale,1HGCM82633A000001,2018,Honda,Civic,LX,Black,Gray,85000,mi,FWD,Automatic,"4 Cylinder",9000,3.2,'
         '"Some Dealer","Manheim California","CA - Manheim California",2026-09-25T07:00:00Z,2026-09-30T07:00:00Z,,,7600,8900,,,Live,')
OTHER = ('Simulcast,2T1BURHE0JC000002,2019,Toyota,Corolla,LE,Black,White,60000,mi,FWD,Automatic,"4 Cylinder",12000,,'
         '"Other","Manheim California","CA - Manheim California",2026-09-30T16:00:00Z,2026-10-01T06:59:59Z,8,40,,,,,Live,')


class ManheimCsvTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "export.csv"
        # Выгрузка Manheim разделяет строки символом \r
        self.path.write_bytes(("\r".join([HEADER, LIVE, TIMED, OTHER]) + "\r").encode("utf-8"))

    def tearDown(self):
        self.dir.cleanup()

    def test_detects_export_by_header(self):
        self.assertTrue(manheim_csv.is_export(self.path))
        other = Path(self.dir.name) / "x.csv"
        other.write_text("a,b\n1,2\n")
        self.assertFalse(manheim_csv.is_export(other))

    def test_rows_merge_live_and_ove(self):
        rows = manheim_csv.read_export(self.path)
        self.assertEqual(len(rows), 2)
        civic = rows[0]
        self.assertEqual((civic["auction"], civic["lot_number"], civic["sale_date"]), ("Manheim", "5-12", "2026-09-30"))
        self.assertEqual((civic["mmr_adjusted_usd"], civic["condition_grade"], civic["odometer_miles"]), ("9000", "3.2", "85000"))
        self.assertIn("OVE: ставка $7,600, Buy Now $8,900", civic["lot_description"])
        self.assertIn("AS IS", civic["defects"])
        self.assertIn("из списка:", civic["needs_review"])
        self.assertIn("нет CR grade", rows[1]["needs_review"])


class ManheimMarketTest(unittest.TestCase):
    def setUp(self):
        self.costs = load_costs(DEFAULT_COSTS_PATH)

    def test_manheim_market_is_mmr_based(self):
        self.assertEqual(market_config("Manheim", self.costs)["prefer"], "mmr")
        self.assertNotIn("prefer", market_config("CarMax", self.costs))
        result = calculate(BidInput(auction="Manheim", kbb_private_party=15000, mmr=10000), self.costs)
        self.assertAlmostEqual(result.market_price, 10000 * self.costs["market_by_auction"]["Manheim"]["mmr_clean"])

    def test_low_grade_adds_recon_only_on_manheim(self):
        self.assertGreater(grade_recon(1.5, "Manheim", self.costs), grade_recon(3.5, "Manheim", self.costs))
        self.assertEqual(grade_recon(4.5, "Manheim", self.costs), 0)
        self.assertEqual(grade_recon(1.5, "CarMax", self.costs), 0)

    def test_profit_at_market_uses_market_price_not_ceiling(self):
        result = calculate(BidInput(auction="Manheim", kbb_private_party=12000, mmr=7000), self.costs)
        sale = 12000 + self.costs["kbb_private_party_offset_usd"]
        # Прибыль по рынку = прибыль при потолке + (потолок + сборы) − (рынок + сборы рынка): чем дороже рынок, тем меньше.
        cheaper = calculate(BidInput(auction="Manheim", kbb_private_party=12000, mmr=6000), self.costs)
        self.assertGreater(cheaper.profit_at_market, result.profit_at_market)
        self.assertLess(result.profit_at_market, sale - 7000)
        self.assertNotEqual(round(result.profit_at_market), round(result.profit_at_max))

    def test_estimated_kbb_capped_by_mmr(self):
        cap = self.costs["kbb_estimate_max_to_mmr"]
        est = calculate(BidInput(auction="Manheim", kbb_private_party=20000, kbb_source="по похожим", mmr=5000), self.costs)
        self.assertAlmostEqual(est.sale_price, 5000 * cap)
        official = calculate(BidInput(auction="Manheim", kbb_private_party=20000, mmr=5000), self.costs)
        self.assertGreater(official.sale_price, 5000 * cap)


if __name__ == "__main__":
    unittest.main()
