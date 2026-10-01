import json
import shutil
import tempfile
import unittest
from pathlib import Path

from lot_analyzer import manheim_results
from lot_analyzer.bid import DEFAULT_COSTS_PATH, BidInput, calculate, load_costs

HEADER = "Run #,Year/Make/Model,VIN,Color,CR,Odometer,MMR Low,MMR High,MMR Avg,Outcome,Sale Price\n"


class ManheimResultsTest(unittest.TestCase):
    def test_lane_csv_calibration_and_config_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            lane = Path(tmp) / "lane.csv"
            rows = []
            for i in range(30):
                mmr = 3000 + i * 1000
                rows.append(f"3-{i},2015 HONDA CIVIC,VIN{i:014d},Gray,4.0,100000,0,0,{mmr},Sold,{int(mmr * (1.1 if mmr < 5000 else 1.0))}")
            rows.append("3-99,2015 HONDA CIVIC,X,Gray,4.0,1,0,0,9000,No Sale,8000")
            lane.write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")
            sales = manheim_results.read_lane_csv(lane)
            self.assertEqual(len(sales), 30)                          # «No Sale» не считается
            summary = manheim_results.summarize(sales)
            self.assertEqual(summary["median"], 1.0)
            costs = Path(tmp) / "costs.json"
            shutil.copy(DEFAULT_COSTS_PATH, costs)
            manheim_results.update_config(summary, costs, "lane.csv")
            manheim = json.loads(costs.read_text(encoding="utf-8"))["market_by_auction"]["Manheim"]
            self.assertEqual(manheim["mmr_clean"], 1.0)
            self.assertIn("lane.csv", manheim["based_on"])

    def test_price_band_share_used_for_market(self):
        costs = load_costs(DEFAULT_COSTS_PATH)
        cheap = calculate(BidInput(auction="Manheim", mmr=4000, kbb_private_party=9000), costs)
        dear = calculate(BidInput(auction="Manheim", mmr=25000, kbb_private_party=32000), costs)
        bands = dict((u, s) for u, s in costs["market_by_auction"]["Manheim"]["mmr_bands"])
        self.assertAlmostEqual(cheap.market_price, 4000 * bands[5000])
        self.assertAlmostEqual(dear.market_price, 25000 * bands[1e9])


if __name__ == "__main__":
    unittest.main()
