"""Страницы-списки (сохранённые поиски) ADESA, ACV и Manheim: строка на каждую машину."""

from __future__ import annotations

import unittest
from pathlib import Path

from lot_analyzer.bid import apply_to_rows, assess_history, load_costs
from lot_analyzer.parsers import parse_page

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
COSTS = load_costs(ROOT / "config" / "costs.json")


def rows_of(name: str) -> list[dict[str, str]]:
    rows = parse_page((FIXTURES / name).read_text(encoding="utf-8"), source_name=name)
    apply_to_rows(rows, COSTS)
    return rows


class TestAdesaList(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = rows_of("adesa_search_list.html")
        cls.by_miles = {r["odometer_miles"]: r for r in cls.rows}

    def test_every_card_is_a_row(self) -> None:
        self.assertEqual(len(self.rows), 7)
        self.assertTrue(all(r["auction"] == "ADESA" and r["make"] == "Honda" and r["model"] == "Civic" for r in self.rows))

    def test_split_vin_is_joined(self) -> None:
        self.assertEqual(self.by_miles["103283"]["vin"], "19XFB2F53DE271909")   # «19XFB2F53DE» + «271909»
        self.assertEqual(self.by_miles["103283"]["vin_valid"], "да")

    def test_card_values(self) -> None:
        r = self.by_miles["103283"]
        self.assertEqual((r["year"], r["trim"], r["condition_grade"]), ("2013", "LX", "2.7"))
        self.assertEqual((r["current_bid_usd"], r["auction_retail_usd"]), ("7400", "11214"))
        self.assertEqual(r["location"], "Mira Loma, CA")
        self.assertTrue(r["sale_date"].startswith("Ends"))
        self.assertTrue(r["lot_url"].startswith("https://marketplace.adesa.com/details/"))

    def test_upcoming_lot_without_bid(self) -> None:
        r = self.by_miles["134867"]
        self.assertEqual(r["current_bid_usd"], "")
        self.assertIn("Run F162", r["lot_description"])

    def test_list_verdict_is_preliminary(self) -> None:
        self.assertIn("предварительно", self.by_miles["106698"]["calc_verdict"])


class TestAcvList(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = rows_of("acv_search_list.html")

    def test_search_cards_only_not_watchlist_sidebar(self) -> None:
        self.assertEqual(len(self.rows), 5)
        self.assertTrue(all(r["lot_number"].isdigit() for r in self.rows))

    def test_card_values_and_lights(self) -> None:
        mini = next(r for r in self.rows if r["make"] == "Mini")
        self.assertEqual((mini["year"], mini["model"], mini["trim"], mini["odometer_miles"]), ("2005", "Cooper", "Base", "107422"))
        self.assertEqual((mini["current_bid_usd"], mini["location"], mini["lot_number"]), ("150", "CORONA, CA", "16490187"))
        self.assertIn("as-is", mini["history_page"])                  # красный огонь
        self.assertNotIn("Title absent", mini["history_page"])       # синий — не трактуем
        self.assertEqual(mini["lot_url"], "https://app.acvauctions.com/auction/16490187")


class TestManheimList(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = rows_of("manheim_search_list.html")
        cls.by_miles = {r["odometer_miles"]: r for r in cls.rows}

    def test_every_listing_json_is_a_row(self) -> None:
        self.assertEqual(len(self.rows), 6)
        self.assertEqual({r["model"] for r in self.rows}, {"Corolla"})

    def test_timed_ove_listing(self) -> None:
        r = self.by_miles["90886"]
        self.assertEqual((r["current_bid_usd"], r["mmr_adjusted_usd"]), ("8000", "9375"))
        self.assertIn("Buy Now: $10,500", r["lot_description"])
        self.assertTrue(r["sale_date"].startswith("до 2026-09-30"))

    def test_frame_damage_and_disclosures_skip(self) -> None:
        r = self.by_miles["137066"]
        self.assertIn("Further Disclosures: Structural Damage", r["defects"])
        self.assertTrue(r["calc_verdict"].startswith("ПРОПУСТИТЬ"))

    def test_only_negative_disclosures_kept(self) -> None:
        self.assertNotIn("Airbags: No Issues", " ".join(r["defects"] for r in self.rows))

    def test_salvage_listing_skipped(self) -> None:
        self.assertIn("брендированный титул", self.by_miles["96953"]["calc_verdict"])

    def test_tail_lamp_water_is_not_flood(self) -> None:
        self.assertEqual(assess_history("LR Tail Lamp: Water Damage Replacement Required", COSTS).skip, [])
        self.assertNotIn("затопление", self.by_miles["149539"]["calc_verdict"])

    def test_mmr_fallback_sale_price(self) -> None:
        r = self.by_miles["42498"]                                   # ни KBB, ни ритейла — только MMR $9,800
        self.assertEqual(r["sale_estimate_usd"], str(round(9800 * COSTS["mmr_retail_factor"])))
        self.assertIn("MMR", r["calc_breakdown"])


if __name__ == "__main__":
    unittest.main()
