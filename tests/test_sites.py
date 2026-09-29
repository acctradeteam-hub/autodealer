"""Тесты разбора реальных (сокращённых) страниц Manheim, ADESA и CarMax Auctions."""

from __future__ import annotations

import unittest
from pathlib import Path

from lot_analyzer.bid import assess_history, calculate, estimate_recon, input_from_row, load_costs
from lot_analyzer.parsers import parse_lot

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
COSTS = load_costs(ROOT / "config" / "costs.json")


def parse(name: str) -> dict[str, str]:
    return parse_lot((FIXTURES / name).read_text(encoding="utf-8"), source_name=name)


class TestManheim(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.row = parse("manheim_corolla_lot.html")

    def test_fields_from_listing_json_not_filters(self) -> None:
        r = self.row
        self.assertEqual(r["auction"], "Manheim")
        self.assertEqual((r["year"], r["make"], r["model"], r["trim"]), ("2021", "Toyota", "Corolla", "XSE"))  # не 2027 / «Select Make»
        self.assertEqual(r["vin"], "JTNC4MBE3M3123227")
        self.assertEqual(r["odometer_miles"], "143827")
        self.assertEqual(r["lot_number"], "4864178")
        self.assertEqual(r["sale_date"], "2026-09-29")
        self.assertIn("Fontana, CA", r["location"])

    def test_valuations_and_condition(self) -> None:
        r = self.row
        self.assertEqual(r["mmr_adjusted_usd"], "10600")
        self.assertEqual(r["auction_retail_usd"], "17200")
        self.assertEqual(r["condition_grade"], "4.2 Clean")
        self.assertIn("1 accidents", r["history_page"])
        self.assertIn("2 owners", r["history_page"])
        self.assertEqual(r["keys_present"], "да (1)")

    def test_only_actionable_damages_go_to_defects(self) -> None:
        self.assertIn("Front Bumper Cover: Scratch Heavy", self.row["defects"])
        self.assertNotIn("Prev Repair", self.row["defects"])       # «No Action Required»
        self.assertIn("Prev Repair", self.row["condition_report"])  # но в отчёте видно

    def test_non_runner_contradiction_flagged(self) -> None:
        self.assertIn("NON RUNNER", self.row["needs_review"])
        self.assertIn("as-is", self.row["needs_review"])

    def test_verdict_uses_auction_retail_and_warns(self) -> None:
        result = calculate(input_from_row(self.row), COSTS)
        self.assertIn("ритейл аукциона", result.sale_source)
        self.assertIn("ДТП", result.breakdown())
        self.assertIn("no runner", result.breakdown())
        self.assertIn("не на ходу", result.verdict)


class TestAdesa(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.row = parse("adesa_civic_lot.html")

    def test_fields_from_lot_not_similar_vehicles(self) -> None:
        r = self.row
        self.assertEqual(r["auction"], "ADESA")
        self.assertEqual((r["year"], r["make"], r["model"], r["trim"]), ("2013", "Honda", "Civic", "LX"))  # не 2015 из карусели
        self.assertEqual(r["vin"], "2HGFB2F50DH582798")
        self.assertEqual(r["odometer_miles"], "135435")
        self.assertEqual(r["current_bid_usd"], "4900")   # не $8,600 похожей машины
        self.assertEqual(r["location"], "ADESA Los Angeles")

    def test_values_grade_history(self) -> None:
        r = self.row
        self.assertEqual((r["wholesale_usd"], r["auction_retail_usd"], r["condition_grade"]), ("5491", "9474", "2.6"))
        self.assertIn("2 Accidents", r["history_page"])
        self.assertIn("сбор ADESA в карточке: $350", r["lot_description"])
        self.assertIn("протектор: 10/32, 10/32, 9/32, 11/32", r["lot_description"])  # запаска 4/32 не в счёт

    def test_damages_table(self) -> None:
        for item in ("Wheel Cover - LR: Missing", "Qtr Panel - R: Mult Dents", "Air Conditioner: Blows Warm", "Warning - Tire Pressure: On"):
            self.assertIn(item, self.row["defects"])

    def test_no_title_issues_is_not_title_absent(self) -> None:
        self.assertEqual(assess_history(self.row["history_page"], COSTS).notes, [])

    def test_verdict_opening_bid_above_ceiling(self) -> None:
        result = calculate(input_from_row(self.row), COSTS)
        self.assertTrue(result.verdict.startswith("ДОРОЖЕ ПОТОЛКА"))
        self.assertIn("2+ ДТП", result.breakdown())
        self.assertIn("substd repair", result.breakdown())


class TestCarMax(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.row = parse("carmax_civic_lot.html")

    def test_fields_from_open_card(self) -> None:
        r = self.row
        self.assertEqual(r["auction"], "CarMax")
        self.assertEqual((r["year"], r["make"], r["model"], r["trim"]), ("2013", "Honda", "Civic", "EX"))
        self.assertEqual(r["vin"], "19XFB2F80DE240336")
        self.assertEqual(r["odometer_miles"], "184398")
        self.assertEqual(r["lot_number"], "A/21")
        self.assertEqual(r["location"], "Chino, CA")
        self.assertTrue(r["sale_date"].startswith("9/29/2026"))

    def test_announcements_damage_tires(self) -> None:
        r = self.row
        self.assertTrue(r["defects"].startswith("Major transmission defect"))
        self.assertIn("Front right fender: Dented, 2 to 8 inches", r["defects"])
        self.assertIn("протектор: 1/32, 1/32, 2/32, 1/32", r["lot_description"])

    def test_recon_counts_transmission_and_worn_tires(self) -> None:
        total, found = estimate_recon(r_text(self.row), COSTS)
        joined = " ".join(found)
        self.assertIn("major transmission defect +2500", joined)
        self.assertIn("шины ×4", joined)

    def test_no_values_on_page_means_no_verdict_until_kbb(self) -> None:
        result = calculate(input_from_row(self.row), COSTS)
        self.assertTrue(result.verdict.startswith("НЕТ ОЦЕНКИ"))
        row = dict(self.row, kbb_private_party_usd="6500")
        self.assertIsNotNone(calculate(input_from_row(row), COSTS).sale_price)


def r_text(row: dict[str, str]) -> str:
    return f"{row['defects']} {row['condition_report']}"


class TestCarMaxVocabulary(unittest.TestCase):
    def test_announcement_words(self) -> None:
        self.assertTrue(assess_history("Salvage history, Total loss history", COSTS).skip)
        self.assertTrue(assess_history("Excessive water intrusion", COSTS).skip)
        self.assertTrue(assess_history("Not actual miles", COSTS).skip)
        self.assertIn("был в угоне", assess_history("Prior theft history", COSTS).discounts)
        self.assertIn("аренда/флит", assess_history("Prior police", COSTS).discounts)
        relaxed = dict(COSTS, title_required=False)
        self.assertTrue(any("227" in n for n in assess_history("Possible 227", relaxed).notes))
        self.assertTrue(assess_history("App 227", COSTS).skip)            # «только с титулом»
        self.assertTrue(assess_history("Title absent", COSTS).skip)
        self.assertFalse(assess_history("No Title Issues", COSTS).skip)


if __name__ == "__main__":
    unittest.main()


class TestCarMaxWatchlist(unittest.TestCase):
    """Сохранённый watch-лист CarMax: строка на каждую машину, заметки покупателя в расчёте."""

    @classmethod
    def setUpClass(cls) -> None:
        from lot_analyzer.parsers import parse_page

        cls.rows = parse_page((FIXTURES / "carmax_watchlist.html").read_text(encoding="utf-8"), source_name="watch")
        cls.by_lot = {r["lot_number"]: r for r in cls.rows}

    def test_one_row_per_card_without_nearby_block(self) -> None:
        self.assertEqual(len(self.rows), 8)
        self.assertNotIn("Fiesta", " ".join(r["model"] for r in self.rows))

    def test_card_fields(self) -> None:
        r = self.by_lot["A/88"]
        self.assertEqual((r["vin"], r["year"], r["make"], r["model"], r["trim"]), ("1HGCR2F30FA107196", "2015", "Honda", "Accord", "LX"))
        self.assertEqual((r["odometer_miles"], r["location"]), ("38864", "Chino, CA"))
        self.assertEqual(r["defects"], "Major engine defect, Structural damage")

    def test_notes_give_kbb_mmr_and_history(self) -> None:
        self.assertEqual(self.by_lot["A/88"]["kbb_private_party_usd"], "14380")        # «KBB 14,380$»
        self.assertEqual(self.by_lot["A/70"]["kbb_private_party_usd"], "11905")        # «KBB $11905»
        # «KBB PP $19,940 (92620, 9/18) MMR $13,850 Est Retail $20,000 KBB 19,530$» — берётся последнее, более свежее
        lexus = self.by_lot["B/95"]
        self.assertEqual((lexus["kbb_private_party_usd"], lexus["mmr_adjusted_usd"]), ("19530", "13850"))
        self.assertIn("продана за $8400", self.by_lot["A/126"]["lot_description"])
        self.assertIn("ваша ставка: $6,500", self.by_lot["A/70"]["lot_description"])

    def test_opened_card_merged_with_its_list_entry(self) -> None:
        r = self.by_lot["A/162"]
        self.assertIn("3 owners, 1 accident", r["carfax_autocheck"])
        self.assertTrue(r["sale_date"].startswith("9/29/2026"))                       # из открытой карточки

    def test_verdicts(self) -> None:
        verdict = {lot: calculate(input_from_row(r), COSTS).verdict for lot, r in self.by_lot.items()}
        self.assertTrue(verdict["A/88"].startswith("ПРОПУСТИТЬ"))       # Structural damage
        self.assertTrue(verdict["B/10"].startswith("ПРОПУСТИТЬ"))       # Not actual miles
        self.assertTrue(verdict["A/70"].startswith("МОЖНО"))            # KBB из заметки
        self.assertTrue(verdict["A/9"].startswith("НЕТ ОЦЕНКИ"))        # KBB не вписан

    def test_history_in_words_and_dmv_fee(self) -> None:
        from lot_analyzer.bid import dmv_fees

        self.assertEqual(assess_history("Two owners, three accidents", COSTS).discounts, {"2+ ДТП": 0.15})
        self.assertEqual(dmv_fees(self.by_lot["A/21"]["defects"]), 71)

    def test_my_proxy_note_compared_with_ceiling(self) -> None:
        from lot_analyzer.bid import apply_to_rows

        row = dict(self.by_lot["A/162"], kbb_private_party_usd="9000")   # «3 owners, 1 accident. MP 5600»
        self.assertEqual(row["my_proxy_usd"], "5600")
        apply_to_rows([row], COSTS)
        self.assertIn("ваш прокси $5,600 ВЫШЕ потолка", row["calc_verdict"])

    def test_fb_price_note(self) -> None:
        from lot_analyzer.sites_text import row_from_carmax_card
        from lot_analyzer.schema import empty_row

        row = empty_row()
        row_from_carmax_card(row, {"vin": "19XFB2F51EE212231", "title": "2014 Honda Civic LX", "notes": "KBB $11905 FB 9,800"})
        self.assertEqual((row["kbb_private_party_usd"], row["retail_estimate_usd"]), ("11905", "9800"))
