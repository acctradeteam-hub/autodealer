"""Своя оценка KBB PP по похожим машинам."""

from __future__ import annotations

import unittest
from pathlib import Path

from lot_analyzer.bid import apply_to_rows, load_costs
from lot_analyzer.kbb import KbbEstimator
from lot_analyzer.market import Result
from lot_analyzer.schema import empty_row

COSTS = load_costs(Path(__file__).resolve().parent.parent / "config" / "costs.json")


def rec(vehicle, year, miles, kbb="", price="", vin="", ann=""):
    return Result(vehicle=vehicle, year=str(year), miles=str(miles), kbb=str(kbb), price=str(price),
                  status="Sold" if price else "", vin=vin, announcements=ann)


class TestKbbEstimator(unittest.TestCase):
    def setUp(self) -> None:
        self.records = [
            rec("Honda Civic LX", 2014, 100000, kbb=10000, vin="A" * 17),
            rec("Honda Civic EX", 2014, 100000, kbb=10400),
            rec("Honda Civic LX", 2015, 120000, kbb=10200),
            rec("Toyota Camry LE", 2014, 100000, price=7800),
            rec("Toyota Camry SE", 2015, 110000, price=8000),
            rec("Toyota Camry LE", 2013, 90000, price=7600),
            rec("Toyota Camry LE", 2014, 95000, price=7700),
            rec("Toyota Camry LE", 2014, 105000, price=7900),
            rec("Toyota Camry LE", 2014, 100000, price=3000, ann="Major Engine Defect"),   # дефектная — не в счёт
        ]
        self.est = KbbEstimator(self.records, COSTS)

    def test_exact_by_vin(self) -> None:
        e = self.est.estimate("Honda", "Civic LX", 2014, 100000, vin="A" * 17)
        self.assertEqual((e.value, e.source), (10000, "точно (KBB этой машины)"))

    def test_from_kbb_comps_adjusts_year_and_miles(self) -> None:
        same = self.est.estimate("Honda", "Civic", 2014, 100000)
        more_miles = self.est.estimate("Honda", "Civic", 2014, 150000)
        older = self.est.estimate("Honda", "Civic", 2013, 100000)
        self.assertTrue(same.source.startswith("по KBB похожих"))
        self.assertLess(more_miles.value, same.value)
        self.assertLess(older.value, same.value)
        self.assertAlmostEqual(same.value, 10200, delta=600)

    def test_from_market_divides_by_share(self) -> None:
        e = self.est.estimate("Toyota", "Camry", 2014, 100000)
        self.assertTrue(e.source.startswith("по рынку"))
        self.assertEqual(e.n, 5)                                   # дефектная не учтена
        self.assertAlmostEqual(e.value, 7800 / COSTS["market"]["kbb_clean"], delta=400)

    def test_unknown_model(self) -> None:
        self.assertIsNone(self.est.estimate("Ford", "Focus", 2014, 100000))

    def test_rows_get_estimate_and_page_kbb_feeds_others(self) -> None:
        a = empty_row(); a.update(auction="CarMax", year="2016", make="Mazda", model="Mazda3", odometer_miles="80000", kbb_private_party_usd="11000")
        b = empty_row(); b.update(auction="CarMax", year="2016", make="Mazda", model="Mazda3", odometer_miles="80000")
        c = empty_row(); c.update(auction="CarMax", year="2016", make="Mazda", model="Mazda3 Touring", odometer_miles="90000")
        apply_to_rows([a, b, c], COSTS, estimator=KbbEstimator([], COSTS))
        self.assertEqual(a["kbb_estimate_usd"], "")                # свой KBB есть — оценка не нужна
        self.assertTrue(b["kbb_estimate_usd"] and "по KBB похожих" in b["kbb_estimate_source"])
        self.assertIn("своя оценка", b["calc_breakdown"])


if __name__ == "__main__":
    unittest.main()
