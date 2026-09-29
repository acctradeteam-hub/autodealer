"""Тесты расчёта максимальной ставки (lot_analyzer/bid.py)."""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

from lot_analyzer.bid import (
    BidInput,
    apply_to_rows,
    assess_history,
    auction_fee,
    calculate,
    estimate_recon,
    load_costs,
    resolve_auction,
)
from lot_analyzer.schema import empty_row

COSTS_PATH = Path(__file__).resolve().parent.parent / "config" / "costs.json"


def simple_costs() -> dict:
    """Круглые цифры, чтобы расчёт проверялся в уме."""
    return {
        "profit_min_usd": 1000,
        "profit_min_pct_of_sale": 0.0,
        "budget_max_bid_usd": 0,
        "bid_step_usd": 25,
        "kbb_private_party_factor": 1.0,
        "transport_usd": 0,
        "detailing_usd": 100,
        "smog_usd": 0,
        "dealer_fee_usd": 0,
        "selling_usd": 0,
        "days_to_sell": 0,
        "holding_per_day_usd": 0,
        "recon_default_usd": 400,
        "recon_keywords": {"check engine": 500, "dent": 200},
        "reserve_pct_of_sale": 0.0,
        "history_discounts": {"accident_minor": 0.05, "accident": 0.10, "accidents_multiple": 0.15, "owners_4_plus": 0.03, "rental_fleet": 0.03},
        "history_skip": ["branded_title", "odometer_problem", "structural_damage", "airbag_deployed", "flood"],
        "auctions": {
            "Manheim": {"fee_tiers": [[2999, 300], [5999, 400], [999999, 500]], "extra_fees_usd": {"internet": 100}},
        },
        "auction_aliases": {"OPENLANE": "ADESA"},
    }


class TestHistory(unittest.TestCase):
    def test_clean_carfax_has_no_flags(self) -> None:
        flags = assess_history("No accidents reported. No structural damage reported. 2 owners. Personal vehicle", simple_costs())
        self.assertEqual(flags.skip, [])
        self.assertEqual(flags.discounts, {})

    def test_single_accident_and_minor(self) -> None:
        self.assertEqual(list(assess_history("1 accident reported", simple_costs()).discounts.values()), [0.10])
        self.assertEqual(list(assess_history("Accident: minor damage", simple_costs()).discounts.values()), [0.05])

    def test_multiple_accidents_owners_rental(self) -> None:
        flags = assess_history("2 accidents, 5 owners, rental vehicle", simple_costs())
        self.assertAlmostEqual(sum(flags.discounts.values()), 0.15 + 0.03 + 0.03)

    def test_zero_accidents_is_clean(self) -> None:
        self.assertEqual(assess_history("0 accidents, 1 owner", simple_costs()).discounts, {})

    def test_stop_factors(self) -> None:
        for text in ("Salvage title", "REBUILT", "Odometer rollback", "Frame damage", "Airbags deployed", "Flood damage"):
            with self.subTest(text=text):
                self.assertTrue(assess_history(text, simple_costs()).skip)

    def test_negated_stop_factor_is_ignored(self) -> None:
        self.assertEqual(assess_history("No flood damage. No airbag deployed", simple_costs()).skip, [])

    def test_empty_history_asks_for_carfax(self) -> None:
        self.assertTrue(assess_history("", simple_costs()).notes)


class TestFeesAndRecon(unittest.TestCase):
    def test_fee_tiers_and_extras(self) -> None:
        costs = simple_costs()
        self.assertEqual(auction_fee(2999, "Manheim", costs), 400)
        self.assertEqual(auction_fee(3000, "Manheim", costs), 500)
        self.assertEqual(auction_fee(10**7, "Manheim", costs), 600)
        self.assertEqual(auction_fee(5000, "Неизвестный", costs), 0)

    def test_carmax_tier3_january_2026(self) -> None:
        # Распечатка CarMax Auctions, Tier 3 / January 2026, + cash fee $30.
        costs = load_costs(COSTS_PATH)
        for bid, fee in ((150, 160), (2500, 405), (3500, 385), (4999, 405), (5000, 430), (6999, 445), (7000, 465), (8000, 475), (20000, 595)):
            with self.subTest(bid=bid):
                self.assertEqual(auction_fee(bid, "CarMax", costs), fee + 30)

    def test_acv_schedule_from_may_2026(self) -> None:
        costs = load_costs(COSTS_PATH)
        for bid, fee in ((400, 100), (3000, 385), (4999, 420), (5000, 425), (6500, 475), (9250, 540), (59999, 695), (80000, 1000)):
            with self.subTest(bid=bid):
                self.assertAlmostEqual(auction_fee(bid, "ACV", costs), fee)

    def test_real_purchases_match(self) -> None:
        # Реальные сборы: Manheim $9,250 -> $535; ADESA, лот $4,900 -> $350.
        costs = load_costs(COSTS_PATH)
        self.assertEqual(auction_fee(9250, "Manheim", costs), 535)
        self.assertEqual(auction_fee(4900, "ADESA", costs), 350)

    def test_copart_and_iaa_add_flat_and_tiered_extras(self) -> None:
        costs = load_costs(COSTS_PATH)
        self.assertEqual(auction_fee(5000, "Copart", costs), 750 + 109 + 79)
        self.assertEqual(auction_fee(5000, "Copart Unsecured", costs), 1000 + 109 + 79)
        self.assertEqual(auction_fee(5000, "IAA", costs), 775 + 105 + 20)
        self.assertAlmostEqual(auction_fee(20000, "IAA", costs), 20000 * 0.075 + 125)

    def test_dealer_fee_and_profit_target_in_shipped_config(self) -> None:
        costs = load_costs(COSTS_PATH)
        self.assertEqual(costs["dealer_fee_usd"], 300)
        self.assertEqual(costs["profit_min_usd"], 1500)
        self.assertIn("ACV", costs["auctions"])

    def test_auction_names_resolve(self) -> None:
        costs = load_costs(COSTS_PATH)
        self.assertEqual(resolve_auction("OPENLANE", costs), "ADESA")
        self.assertEqual(resolve_auction("CarMax Auctions", costs), "CarMax")
        self.assertEqual(resolve_auction("Manheim Riverside", costs), "Manheim")
        self.assertEqual(resolve_auction("ACV Auctions", costs), "ACV")
        self.assertEqual(resolve_auction("IAAI", costs), "IAA")
        self.assertEqual(resolve_auction("Copart", costs), "Copart")
        self.assertEqual(resolve_auction("Copart Unsecured", costs), "Copart Unsecured")
        self.assertEqual(resolve_auction("SOCAL Auto Auction", costs), "")

    def test_recon_keywords_match_word_start_only(self) -> None:
        total, found = estimate_recon("1 accident reported", simple_costs())
        self.assertEqual((total, found), (400, []))  # «dent» внутри «accident» не считается
        total, _ = estimate_recon("Check engine light, small dent", simple_costs())
        self.assertEqual(total, 400 + 500 + 200)


class TestCalculate(unittest.TestCase):
    def test_hand_checked_example(self) -> None:
        # 8000 − ремонт 400 − детейлинг 100 − прибыль 1000 = 6500 «всё включено».
        # Ставка 6000 + сбор (500+100) = 6600 > 6500; ставка 5975 ещё в ступени «до 5999»:
        # 5975 + (400+100) = 6475 — влезает.
        result = calculate(BidInput(auction="Manheim", sale_price=8000, history_text="clean title"), simple_costs())
        self.assertEqual(result.max_bid, 5975)
        self.assertEqual(result.costs_over_bid, 500 + 500)
        self.assertAlmostEqual(result.profit_at_max, 8000 - 5975 - 1000)
        self.assertTrue(result.verdict.startswith("МОЖНО"))

    def test_fee_tier_boundary_lowers_bid(self) -> None:
        # «Всё включено» 3350: 3000+500 не влезает, 2975+400 тоже нет, 2950+400 = 3350 — влезает.
        result = calculate(BidInput(auction="Manheim", sale_price=4850, recon=400, history_text="clean"), simple_costs())
        self.assertEqual(result.max_bid, 2950)

    def test_kbb_used_when_no_own_estimate(self) -> None:
        costs = simple_costs()
        costs["kbb_private_party_factor"] = 0.9
        result = calculate(BidInput(auction="Manheim", kbb_private_party=10000, history_text="clean"), costs)
        self.assertEqual(result.sale_price, 9000)
        self.assertIn("KBB", result.sale_source)

    def test_no_price_means_no_verdict(self) -> None:
        result = calculate(BidInput(auction="Manheim"), simple_costs())
        self.assertIsNone(result.max_bid)
        self.assertTrue(result.verdict.startswith("НЕТ ОЦЕНКИ"))

    def test_stop_factor_skips(self) -> None:
        result = calculate(BidInput(auction="Manheim", sale_price=9000, history_text="Rebuilt title"), simple_costs())
        self.assertIsNone(result.max_bid)
        self.assertTrue(result.verdict.startswith("ПРОПУСТИТЬ"))

    def test_unprofitable(self) -> None:
        result = calculate(BidInput(auction="Manheim", sale_price=1200, history_text="clean"), simple_costs())
        self.assertTrue(result.verdict.startswith("НЕВЫГОДНО"))

    def test_current_bid_above_ceiling(self) -> None:
        result = calculate(BidInput(auction="Manheim", sale_price=8000, current_bid=6200, history_text="clean"), simple_costs())
        self.assertTrue(result.verdict.startswith("ДОРОЖЕ ПОТОЛКА"))

    def test_budget_caps_bid(self) -> None:
        costs = simple_costs()
        costs["budget_max_bid_usd"] = 5000
        result = calculate(BidInput(auction="Manheim", sale_price=8000, history_text="clean"), costs)
        self.assertEqual(result.max_bid, 5000)

    def test_profit_pct_wins_when_larger(self) -> None:
        costs = simple_costs()
        costs["profit_min_pct_of_sale"] = 0.2  # 20% от 10000 = 2000 > 1000
        result = calculate(BidInput(auction="Manheim", sale_price=10000, recon=0, history_text="clean"), costs)
        self.assertAlmostEqual(result.profit_at_max, 2000, delta=25)

    def test_shipped_config_gives_sane_bid_for_typical_car(self) -> None:
        # Типичная машина: продажа $8500 на Facebook Marketplace, чистая история.
        result = calculate(BidInput(auction="Manheim", sale_price=8500, history_text="clean title, 2 owners"), load_costs(COSTS_PATH))
        self.assertIsNotNone(result.max_bid)
        self.assertTrue(3500 <= result.max_bid <= 7000, result.max_bid)   # ступени прибыли: для продажи $8,500 цель $500


class TestStrategySettings(unittest.TestCase):
    def test_kbb_minus_500_and_far_transport(self) -> None:
        from lot_analyzer.bid import transport_cost

        costs = load_costs(COSTS_PATH)
        result = calculate(BidInput(auction="CarMax", kbb_private_party=10000, history_text="clean"), costs)
        self.assertEqual(result.sale_price, 10000 * costs["kbb_private_party_factor"] + costs["kbb_private_party_offset_usd"])
        self.assertEqual(transport_cost("CarMax Vegas Auction Center", costs), costs["transport_by_location"]["Vegas"])
        self.assertEqual(transport_cost("Chino, CA", costs), costs["transport_usd"])
        self.assertEqual(transport_cost("Boise, ID", costs), costs["transport_out_of_state_usd"])
        far = calculate(BidInput(auction="CarMax", location="Fresno, CA", kbb_private_party=10000, history_text="clean"), dict(costs, budget_max_bid_usd=0))
        near = calculate(BidInput(auction="CarMax", location="Chino, CA", kbb_private_party=10000, history_text="clean"), dict(costs, budget_max_bid_usd=0))
        self.assertLess(far.max_bid, near.max_bid)


class TestTiersAndChance(unittest.TestCase):
    def test_profit_tiers(self) -> None:
        from lot_analyzer.bid import target_profit

        costs = {"profit_tiers": [[7000, 600], [10000, 900], [9999999, 0.1]]}
        self.assertEqual(target_profit(6500, costs), 600)
        self.assertEqual(target_profit(9500, costs), 900)
        self.assertAlmostEqual(target_profit(15000, costs), 1500)
        self.assertEqual(target_profit(9500, {"profit_min_usd": 1500, "profit_min_pct_of_sale": 0.15}), 1500)

    def test_win_chance_interpolation(self) -> None:
        from lot_analyzer.bid import win_chance

        points = [[0.6, 0.1], [0.8, 0.5], [1.0, 0.9]]
        self.assertAlmostEqual(win_chance(7000, 10000, points), 0.3)
        self.assertAlmostEqual(win_chance(12000, 10000, points), 0.9)
        self.assertIsNone(win_chance(7000, None, points))

    def test_reg227_adds_holding_days(self) -> None:
        costs = load_costs(COSTS_PATH)
        plain = calculate(BidInput(auction="CarMax", kbb_private_party=10000, history_text="clean"), costs)
        r227 = calculate(BidInput(auction="CarMax", kbb_private_party=10000, history_text="Possible 227"), costs)
        self.assertLess(r227.max_bid, plain.max_bid)
        self.assertIn("REG 227", r227.verdict)


class TestRows(unittest.TestCase):
    def test_apply_to_rows_fills_calc_columns(self) -> None:
        row = empty_row()
        row.update(auction="Manheim", retail_estimate_usd="8000", title_type="Clean", carfax_autocheck="No accidents")
        rows = [row, copy.deepcopy(empty_row())]
        apply_to_rows(rows, simple_costs())
        self.assertEqual(rows[0]["calc_max_bid_usd"], "5975")
        self.assertEqual(rows[0]["sale_estimate_usd"], "8000")
        self.assertTrue(rows[0]["calc_breakdown"])
        self.assertTrue(rows[1]["calc_verdict"].startswith("НЕТ ОЦЕНКИ"))

    def test_row_recon_estimate_overrides_default(self) -> None:
        row = empty_row()
        row.update(auction="Manheim", retail_estimate_usd="8000", recon_estimate_usd="0", defects="check engine")
        apply_to_rows([row], simple_costs())
        # ремонт 0 вместо 400+500: «всё включено» 6900 -> 6300 + 600
        self.assertEqual(row["calc_max_bid_usd"], "6300")


if __name__ == "__main__":
    unittest.main()
