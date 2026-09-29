"""Тесты разбора карточки лота ACV и расчёта по её находкам."""

from __future__ import annotations

import unittest
from pathlib import Path

from lot_analyzer.acv import find_detail
from lot_analyzer.bid import calculate, estimate_recon, input_from_row, load_costs
from lot_analyzer.parsers import parse_lot

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "tests" / "fixtures" / "acv_kia_sportage_lot.html"
COSTS = ROOT / "config" / "costs.json"


class TestAcvDetail(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = PAGE.read_text(encoding="utf-8")
        cls.detail = find_detail(cls.html)
        cls.row = parse_lot(cls.html, source_name=PAGE.name)

    def test_detail_found_with_announcements(self) -> None:
        self.assertIsNotNone(self.detail)
        titles = [t for t, _, _ in self.detail.announcements]
        for expected in ("Structural Alteration", "Carfax", "Body Damage", "Climate Control Not Working", "Title Absent (30 Days)"):
            self.assertIn(expected, titles)
        self.assertTrue(any(yellow for t, _, yellow in self.detail.announcements if t == "Structural Alteration"))

    def test_carfax_alerts_read(self) -> None:
        carfax = [d for t, d, _ in self.detail.announcements if t == "Carfax"]
        self.assertEqual(len(carfax), 3)
        self.assertTrue(any("Recovered Theft" in d for d in carfax))
        self.assertTrue(any("Mileage inconsistency" in d for d in carfax))

    def test_obd_and_tires(self) -> None:
        self.assertEqual(self.detail.obd_codes, ["P0038", "P0070", "P0071"])
        self.assertEqual(self.detail.tire_depths, [5, 5, 6, 6])

    def test_row_fields_from_lot_card_not_sidebar(self) -> None:
        row = self.row
        self.assertEqual(row["auction"], "ACV")
        self.assertEqual(row["lot_number"], "16496768")
        self.assertEqual(row["vin"], "KNDPM3AC5J7459014")
        self.assertEqual((row["year"], row["make"], row["model"], row["trim"]), ("2018", "Kia", "Sportage", "LX"))
        self.assertEqual(row["odometer_miles"], "54879")
        self.assertEqual(row["current_bid_usd"], "3500")  # не $3,800 соседнего Camaro
        self.assertEqual(row["transport_quote_usd"], "199")
        self.assertEqual(row["location"], "Fontana, CA")
        self.assertEqual(row["keys_present"], "да (1)")
        self.assertEqual(row["acv_estimate_usd"], "")      # не «1550» из адреса площадки

    def test_sidebar_filter_words_do_not_leak(self) -> None:
        text = " ".join(self.row[k] for k in ("defects", "history_page", "condition_report", "title_type")).lower()
        for word in ("salvage", "flood", "airbag deployed", "rebuilt", "coolant intermix", "inop"):
            self.assertNotIn(word, text)
        self.assertEqual(self.row["run_and_drive"], "да")

    def test_review_notes(self) -> None:
        self.assertIn("жёлтое объявление ACV", self.row["needs_review"])
        self.assertNotIn("дата продажи", self.row["needs_review"])


class TestAcvCalculation(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.costs = load_costs(COSTS)
        cls.row = parse_lot(PAGE.read_text(encoding="utf-8"), source_name=PAGE.name)
        cls.row["retail_estimate_usd"] = "11000"

    def test_kia_is_skipped_for_structural_alteration_and_mileage(self) -> None:
        result = calculate(input_from_row(self.row), self.costs)
        self.assertTrue(result.verdict.startswith("ПРОПУСТИТЬ"))
        self.assertIn("пробег", result.verdict)
        self.assertIn("рамы / кузова", result.verdict)

    def test_stop_factors_win_even_without_sale_price(self) -> None:
        row = dict(self.row, retail_estimate_usd="")
        self.assertTrue(calculate(input_from_row(row), self.costs).verdict.startswith("ПРОПУСТИТЬ"))

    def test_without_stop_factors_theft_discount_and_title_note(self) -> None:
        row = {k: v.replace("Structural Alteration", "SA").replace("Mileage inconsistency", "MI") for k, v in self.row.items()}
        result = calculate(input_from_row(row), self.costs)
        self.assertIsNotNone(result.max_bid)
        self.assertIn("был в угоне", result.breakdown())
        self.assertIn("нет титула", result.verdict)

    def test_recon_itemized_from_acv_findings(self) -> None:
        text = f"{self.row['defects']} {self.row['condition_report']}"
        total, found = estimate_recon(text, self.costs)
        joined = " ".join(found)
        for item in ("does not blow cold", "punctured bumper", "chipped windshield", "коды OBD ×3", "comes with 1 key"):
            self.assertIn(item, joined)
        self.assertNotIn("шины ×", joined)  # протектор 5–6/32 — менять не нужно
        self.assertGreater(total, 2000)

    def test_worn_tires_and_negations(self) -> None:
        total, found = estimate_recon("Front Right Tire: 3 /32 Front Left Tire: 4 /32 Back Left Tire: 7 /32", self.costs)
        self.assertIn("шины ×2", " ".join(found))
        base, _ = estimate_recon("", self.costs)
        self.assertEqual(estimate_recon("No check engine light", self.costs)[0], base)


if __name__ == "__main__":
    unittest.main()
