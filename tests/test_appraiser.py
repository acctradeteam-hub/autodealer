"""Проверки расчёта: цифры считаны вручную и зафиксированы как ожидания."""
from __future__ import annotations

import copy
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from appraiser.bid import holdback_amount, solve_max_bid
from appraiser.config import DEFAULT_CONFIG, load_config, validate_config
from appraiser.costs import buyer_fee, recon_cost, transport_cost
from appraiser.io_tables import read_table, to_float
from appraiser.market import estimate_market_price
from appraiser.pipeline import evaluate_lot
from appraiser.rules import DECISION_BID, DECISION_MANUAL, DECISION_REJECT, pre_bid_checks

DATA = Path(__file__).resolve().parents[1] / "data" / "example"
TODAY = date(2026, 9, 22)


def cfg():
    return copy.deepcopy(DEFAULT_CONFIG)


class BuyerFeeTests(unittest.TestCase):
    def test_tiers(self):
        c = cfg()
        self.assertEqual(buyer_fee(900, c), 200)
        self.assertEqual(buyer_fee(1000, c), 200)      # граница включительно
        self.assertEqual(buyer_fee(1000.01, c), 500)
        self.assertEqual(buyer_fee(9999, c), 750)
        self.assertEqual(buyer_fee(15000, c), 1000)
        self.assertEqual(buyer_fee(100000, c), 1250)   # последняя ступень без потолка


class HoldbackTests(unittest.TestCase):
    def test_percent_wins_on_expensive_car(self):
        amount, basis = holdback_amount(30000, cfg())      # 12% = 3600 > 2000
        self.assertEqual(amount, 3600.0)
        self.assertIn("12", basis)

    def test_floor_wins_on_cheap_car(self):
        amount, basis = holdback_amount(10000, cfg())      # 12% = 1200 < 2000
        self.assertEqual(amount, 2000.0)
        self.assertIn("минимальная прибыль", basis)


class MaxBidTests(unittest.TestCase):
    def test_bid_plus_fee_fits_budget_and_next_step_does_not(self):
        c = cfg()
        market, fixed, hold = 18042.0, 2334.5, 2165.04
        result = solve_max_bid(market, fixed, hold, c)
        budget = market - fixed - hold
        self.assertLessEqual(result.max_bid + result.buyer_fee, budget)
        step = c["rules"]["bid_rounding_usd"]
        higher = result.max_bid + step
        self.assertGreater(higher + buyer_fee(higher, c), budget)
        self.assertEqual(result.max_bid % step, 0)

    def test_impossible_when_costs_exceed_market(self):
        result = solve_max_bid(5000.0, 4000.0, 2000.0, cfg())
        self.assertEqual(result.max_bid, 0.0)
        self.assertTrue(result.notes)


class MarketTests(unittest.TestCase):
    def setUp(self):
        self.comps = read_table(DATA / "comps.csv")
        self.ranges = read_table(DATA / "cargurus_ranges.csv")

    def test_camry_outlier_dropped_and_corridor_applied(self):
        lot = {"year": "2019", "make": "Toyota", "model": "Camry", "mileage": "62000",
               "condition_grade": "B"}
        est = estimate_market_price(lot, self.comps, self.ranges, cfg())
        self.assertEqual(est.comps_found, 7)
        self.assertEqual(est.comps_used, 6)               # C-007 за 26 900 отброшен
        self.assertNotIn("C-007", est.used_comp_ids)
        self.assertEqual(est.base_median, 18750.0)        # медиана шести цен
        self.assertEqual(est.median_mileage, 62000.0)     # пробег считается по тем же шести
        self.assertTrue(est.corridor_applied)
        self.assertEqual(est.corridor_cap, 18600.0)
        self.assertAlmostEqual(est.price, 18042.0, places=2)   # 18 600 − 3%

    def test_condition_grade_raises_price(self):
        lot = {"year": "2021", "make": "Ford", "model": "F-150", "mileage": "39000",
               "condition_grade": "A"}
        est = estimate_market_price(lot, self.comps, self.ranges, cfg())
        self.assertEqual(est.comps_used, 6)
        self.assertGreater(est.condition_adjustment, 0)
        self.assertAlmostEqual(est.price, 34726.0, places=2)   # потолок 35 800 − 3%

    def test_not_enough_comps_leaves_price_empty(self):
        lot = {"year": "2020", "make": "Nissan", "model": "Rogue", "mileage": "48000",
               "condition_grade": "B"}
        est = estimate_market_price(lot, self.comps, self.ranges, cfg())
        self.assertEqual(est.comps_used, 2)
        self.assertIsNone(est.price)

    def test_unknown_model_reports_zero_comps(self):
        lot = {"year": "2020", "make": "Tesla", "model": "Model Y", "mileage": "30000",
               "condition_grade": "B"}
        est = estimate_market_price(lot, self.comps, self.ranges, cfg())
        self.assertEqual(est.comps_found, 0)
        self.assertIsNone(est.price)


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.thresholds = read_table(DATA / "model_thresholds.csv")

    def test_salvage_title_rejected(self):
        lot = {"make": "Honda", "model": "Accord", "year": "2018", "mileage": "71000",
               "title_status": "salvage"}
        hits = pre_bid_checks(lot, self.thresholds, cfg(), today=TODAY)
        self.assertTrue(any(h.decision == DECISION_REJECT and "титул" in h.reason for h in hits))

    def test_mileage_and_age_thresholds_use_make_wildcard(self):
        lot = {"make": "BMW", "model": "328i", "year": "2015", "mileage": "142000",
               "title_status": "clean"}
        hits = pre_bid_checks(lot, self.thresholds, cfg(), today=TODAY)
        reasons = " ".join(h.reason for h in hits if h.decision == DECISION_REJECT)
        self.assertIn("пробег", reasons)
        self.assertIn("возраст", reasons)

    def test_clean_within_thresholds_passes(self):
        lot = {"make": "Toyota", "model": "Camry", "year": "2019", "mileage": "62000",
               "title_status": "clean"}
        self.assertEqual(pre_bid_checks(lot, self.thresholds, cfg(), today=TODAY), [])


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.carriers = read_table(DATA / "carriers.csv")
        self.lanes = read_table(DATA / "lanes.csv")

    def test_carrier_quote_preferred(self):
        lot = {"auction_location": "Manheim Riverside", "destination": "Riverside Yard"}
        result = transport_cost(lot, self.carriers, self.lanes, cfg())
        self.assertEqual(result.amount, 180.0)
        self.assertIn("Valley Auto Haul", result.carrier_name)

    def test_per_mile_rate_from_lane_table(self):
        lot = {"auction_location": "Copart Sacramento", "destination": "Riverside Yard"}
        result = transport_cost(lot, self.carriers, self.lanes, cfg())
        self.assertEqual(result.miles, 470.0)
        self.assertAlmostEqual(result.amount, 470 * 1.15, places=2)   # ступень до 500 миль

    def test_minimum_charge_applied_on_short_haul(self):
        lot = {"auction_location": "Nowhere", "destination": "Riverside Yard", "distance_miles": "40"}
        result = transport_cost(lot, [], [], cfg())
        self.assertEqual(result.amount, 225.0)                        # 40 × 2.20 = 88 < минимума
        self.assertTrue(result.notes)

    def test_missing_distance_returns_nothing(self):
        lot = {"auction_location": "Copart Bakersfield", "destination": "Riverside Yard"}
        result = transport_cost(lot, self.carriers, self.lanes, cfg())
        self.assertIsNone(result.amount)
        self.assertTrue(result.notes)


class ReconTests(unittest.TestCase):
    def test_grade_b_breakdown(self):
        result = recon_cost({"condition_grade": "B"}, cfg())
        self.assertEqual(result.amount, 750 + 150 + 90 + 185)

    def test_override_wins(self):
        result = recon_cost({"condition_grade": "B", "recon_override_usd": "2,400"}, cfg())
        self.assertEqual(result.amount, 2400.0)

    def test_unknown_grade_uses_worst_case(self):
        result = recon_cost({"condition_grade": "Z"}, cfg())
        self.assertEqual(result.breakdown["base_by_condition"], 2600.0)
        self.assertTrue(result.notes)


class ConfigTests(unittest.TestCase):
    def test_example_config_loads(self):
        loaded = load_config(DATA / "config.json")
        self.assertIn("margin", loaded)

    def test_broken_fee_table_rejected(self):
        bad = cfg()
        bad["auction_fees"]["buyer_fee_tiers"] = [{"up_to_usd": 1000, "fee_usd": 200}]
        with self.assertRaises(ValueError):
            validate_config(bad)


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        c = load_config(DATA / "config.json")
        lots = read_table(DATA / "lots.csv")
        comps = read_table(DATA / "comps.csv")
        ranges = read_table(DATA / "cargurus_ranges.csv")
        thresholds = read_table(DATA / "model_thresholds.csv")
        carriers = read_table(DATA / "carriers.csv")
        lanes = read_table(DATA / "lanes.csv")
        cls.results = {
            lot["lot_id"]: evaluate_lot(lot, comps, ranges, thresholds, carriers, lanes, c, today=TODAY)
            for lot in lots
        }

    def test_decisions(self):
        expected = {
            "TEST-001": DECISION_BID,
            "TEST-002": DECISION_REJECT,     # salvage
            "TEST-003": DECISION_REJECT,     # пробег и возраст
            "TEST-004": DECISION_MANUAL,     # 2 конкурента
            "TEST-005": DECISION_BID,
            "TEST-006": DECISION_MANUAL,     # нет расстояния
        }
        for lot_id, decision in expected.items():
            self.assertEqual(self.results[lot_id]["decision"], decision, lot_id)

    def test_camry_bid_matches_hand_calculation(self):
        row = self.results["TEST-001"]
        self.assertAlmostEqual(row["market_price_usd"], 18042.0, places=2)
        self.assertEqual(row["transport_usd"], round(470 * 1.15, 2))
        self.assertEqual(row["recon_usd"], 1175.0)
        self.assertEqual(row["other_fees_usd"], 259.0)
        self.assertEqual(row["selling_costs_usd"], 360.0)
        self.assertAlmostEqual(row["holdback_usd"], 2165.04, places=2)
        self.assertEqual(row["max_bid_usd"], 12525.0)

    def test_breakdown_adds_up(self):
        row = self.results["TEST-005"]
        total = (row["max_bid_usd"] + row["buyer_fee_usd"] + row["transport_usd"]
                 + row["recon_usd"] + row["other_fees_usd"] + row["selling_costs_usd"]
                 + row["holdback_usd"])
        self.assertLessEqual(total, row["market_price_usd"] + 0.01)
        self.assertGreater(total, row["market_price_usd"] - 26)   # недобор меньше шага ставки

    def test_rejected_lot_has_no_bid(self):
        row = self.results["TEST-002"]
        self.assertIsNone(row.get("max_bid_usd"))
        self.assertIn("титул", row["reasons"])

    def test_manual_lot_counts_comps_explicitly(self):
        row = self.results["TEST-004"]
        self.assertEqual((row["comps_found"], row["comps_used"]), (2, 2))
        self.assertIn("2", row["reasons"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
