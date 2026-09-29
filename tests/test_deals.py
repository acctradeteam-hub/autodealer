"""Журнал сделок: прибыль, сроки, сверка сбора аукциона."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lot_analyzer import deals
from lot_analyzer.bid import load_costs

COSTS = load_costs(Path(__file__).resolve().parent.parent / "config" / "costs.json")
CIVIC = deals.Deal(vehicle="2015 Honda Civic", auction="CarMax", bid=4500, paid=4945, repair=28, prep=0, dealer=300,
                   sale=8300, days_to_list=3, days_listed=2, no_photos="да", kbb=9410,
                   announced_defect="Major Transmission Defect", defect_confirmed="нет")


class TestDeals(unittest.TestCase):
    def test_civic_numbers(self) -> None:
        self.assertEqual(CIVIC.invested, 5273)
        self.assertEqual(CIVIC.profit, 3027)
        self.assertEqual(CIVIC.days, 5)

    def test_report_checks_auction_fee(self) -> None:
        text = "\n".join(deals.report([CIVIC], COSTS))
        self.assertIn("прибыль $3,027 = 36% от продажи, 57% на вложенное", text)
        self.assertIn("реально $445, по нашей сетке $445", text)
        self.assertIn("лот без фото", text)
        self.assertIn("не подтвердилось", text)
        self.assertIn("не подтвердились 1 из 1", text)
        self.assertIn("продажа = 88% KBB", text)

    def test_save_and_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "deals.csv"
            deals.save([CIVIC], path)
            loaded = deals.load(path)
        self.assertEqual(loaded[0].profit, CIVIC.profit)
        self.assertEqual((loaded[0].days_listed, loaded[0].no_photos), (2, "да"))


if __name__ == "__main__":
    unittest.main()
