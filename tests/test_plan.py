"""Планировщик по капиталу."""

from __future__ import annotations

import unittest
from pathlib import Path

from lot_analyzer.bid import load_costs
from lot_analyzer.plan import DEFAULT_PLAN, best_plans, car_economics, good_chance

COSTS = load_costs(Path(__file__).resolve().parent.parent / "config" / "costs.json")
PLAN = dict(DEFAULT_PLAN, **COSTS.get("plan", {}))


class TestPlan(unittest.TestCase):
    def test_economics_sell_at_kbb_minus_500(self) -> None:
        invested, profit, days = car_economics(10000, 0.70, {**COSTS, "kbb_private_party_offset_usd": -500}, PLAN)
        self.assertAlmostEqual(invested + profit, 10000 - 500)
        self.assertEqual(days, PLAN["prep_days"] + 10)   # KBB $10,000 — вторая ступень sale_days_by_kbb

    def test_chance_excludes_suspicious_cheap_lots(self) -> None:
        curve = [[0.5, 0.1], [0.6, 0.2], [0.8, 0.6]]
        self.assertAlmostEqual(good_chance(0.7, curve, 0.6), 0.2)
        self.assertEqual(good_chance(0.55, curve, 0.6), 0.0)

    def test_more_capital_never_less_profit(self) -> None:
        small = max(r["monthly"] for r in best_plans(10000, 15, COSTS, PLAN))
        big = max(r["monthly"] for r in best_plans(30000, 15, COSTS, PLAN))
        self.assertGreaterEqual(big, small)

    def test_invested_within_capital(self) -> None:
        for row in best_plans(8000, 15, COSTS, PLAN):
            self.assertLessEqual(row["invested"], 8000)


if __name__ == "__main__":
    unittest.main()
