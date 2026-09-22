"""Тесты «Аналитика лотов». Запуск: python3 -m unittest discover -s tests -v

Каждый дефект, найденный при отладке, закреплён отдельным тестом, чтобы он не
вернулся при правке синонимов или разметки аукционов.
"""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from lot_analyzer import normalize
from lot_analyzer.pages import collect_inputs, read_page
from lot_analyzer.parsers import detect_auction, parse_lot
from lot_analyzer.extract import LotDocument
from lot_analyzer.report import (
    apply_manual_values,
    load_manual_values,
    merge_cumulative,
    read_tsv,
    write_manual_template,
    write_tsv,
    write_xlsx,
)
from lot_analyzer.schema import COLUMN_TITLES, MANUAL_KEYS

FIXTURES = Path(__file__).parent / "fixtures"


def _parse(name: str) -> dict[str, str]:
    path = FIXTURES / name
    return parse_lot(read_page(path), source_name=path.name)


class TestVin(unittest.TestCase):
    def test_check_digit_accepts_valid_vin(self) -> None:
        # Каноничный тестовый VIN из стандарта ISO 3779.
        self.assertTrue(normalize.vin_check_digit_ok("1HGCM82633A004352"))

    def test_check_digit_rejects_broken_vin(self) -> None:
        self.assertFalse(normalize.vin_check_digit_ok("1HGCM82633A004353"))

    def test_check_digit_is_none_when_nothing_to_check(self) -> None:
        self.assertIsNone(normalize.vin_check_digit_ok("12345"))

    def test_model_year_uses_seventh_position_for_epoch(self) -> None:
        # Буква в 7-й позиции -> 2010 год и позже.
        self.assertEqual(normalize.vin_model_year("4T1BF1FK1HU123456"), 2017)
        self.assertEqual(normalize.vin_model_year("1HGCM82633A004352"), 2003)

    def test_clean_vin_strips_separators_and_rejects_short(self) -> None:
        self.assertEqual(normalize.clean_vin(" 4t1bf1fk1hu123456 "), "4T1BF1FK1HU123456")
        self.assertEqual(normalize.clean_vin("ABC123"), "")


class TestNormalize(unittest.TestCase):
    def test_money_formats(self) -> None:
        self.assertEqual(normalize.parse_money("$12,345.00"), 12345.0)
        self.assertEqual(normalize.parse_money("USD 12 345"), 12345.0)
        self.assertEqual(normalize.parse_money("1.234,56"), 1234.56)
        self.assertIsNone(normalize.parse_money("нет данных"))

    def test_odometer_brand_and_km_conversion(self) -> None:
        self.assertEqual(normalize.parse_odometer("123,456 mi (ACTUAL)"), (123456, "Actual", False))
        self.assertEqual(normalize.parse_odometer("45,000 (NOT ACTUAL)")[1], "Not Actual")
        miles, brand, converted = normalize.parse_odometer("200 000 km")
        self.assertEqual((miles, converted), (124274, True))

    def test_dates_normalize_to_iso(self) -> None:
        self.assertEqual(normalize.parse_date("Tue Oct 06, 2026 9:00 AM PDT"), "2026-10-06")
        self.assertEqual(normalize.parse_date("10/14/2026"), "2026-10-14")

    def test_placeholders_recognised(self) -> None:
        self.assertTrue(normalize.is_placeholder("N/A"))
        self.assertTrue(normalize.is_placeholder("—"))
        self.assertFalse(normalize.is_placeholder("Toyota"))


class TestExtract(unittest.TestCase):
    def test_json_ld_wrapper_keeps_parent_label(self) -> None:
        # {"mileageFromOdometer": {"value": ...}} — по ключу "value" поле не опознать.
        doc = LotDocument(
            '<html><head><script type="application/ld+json">'
            '{"@type":"Vehicle","mileageFromOdometer":{"@type":"QuantitativeValue","value":"78452"}}'
            "</script></head><body>x</body></html>"
        )
        self.assertEqual(doc.find(("odometer", "mileage"))[0], "78452")

    def test_schema_metadata_never_becomes_a_value(self) -> None:
        doc = LotDocument(
            '<html><head><script type="application/ld+json">'
            '{"@type":"Vehicle","offers":{"@type":"Offer","price":"4250","priceCurrency":"USD"}}'
            "</script></head><body>x</body></html>"
        )
        self.assertEqual(doc.find(("current bid", "price"))[0], "4250")

    def test_placeholder_values_need_explicit_opt_in(self) -> None:
        doc = LotDocument("<html><body><table><tr><th>Key</th><td>None</td></tr></table></body></html>")
        self.assertEqual(doc.find(("key",))[0], "")
        self.assertEqual(doc.find(("key",), allow_weak=True)[0], "None")


class TestCopartPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.row = _parse("synthetic_copart_lot.html")

    def test_auction_and_identifiers(self) -> None:
        self.assertEqual(self.row["auction"], "Copart")
        self.assertEqual(self.row["lot_number"], "58392011")
        self.assertEqual(self.row["vin"], "4T1BF1FK1HU123456")
        self.assertEqual(self.row["vin_valid"], "да")

    def test_vehicle_and_odometer(self) -> None:
        self.assertEqual((self.row["year"], self.row["make"], self.row["model"]), ("2017", "TOYOTA", "CAMRY"))
        self.assertEqual(self.row["odometer_miles"], "78452")
        self.assertEqual(self.row["odometer_brand"], "Actual")

    def test_condition_and_money(self) -> None:
        self.assertEqual(self.row["title_type"], "CA SALVAGE CERTIFICATE")
        self.assertEqual(self.row["title_state"], "CA")
        self.assertEqual(self.row["damage_primary"], "FRONT END")
        self.assertEqual(self.row["keys_present"], "да")
        self.assertEqual(self.row["run_and_drive"], "да")
        self.assertEqual(self.row["current_bid_usd"], "4250")
        self.assertEqual(self.row["acv_estimate_usd"], "18940")
        self.assertEqual(self.row["sale_date"], "2026-10-06")

    def test_defects_include_text_markers(self) -> None:
        self.assertIn("подушки сработали", self.row["defects"])
        self.assertIn("salvage-титул", self.row["defects"])

    def test_photos_collected_without_logo(self) -> None:
        self.assertEqual(self.row["photo_count"], "3")
        self.assertNotIn("copart-logo", self.row["photo_urls"])

    def test_complete_page_has_no_review_notes(self) -> None:
        self.assertEqual(self.row["needs_review"], "")


class TestIaaiPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.row = _parse("synthetic_iaai_lot.html")

    def test_run_and_drive_reads_value_not_label(self) -> None:
        # Регрессия: подпись «Run and Drive Verified» содержит искомые слова,
        # а значение в ней — «No». Поиск по тексту давал ложное «да».
        self.assertEqual(self.row["run_and_drive"], "нет")

    def test_keys_none_means_no_keys(self) -> None:
        # Регрессия: «None» — это ответ «ключей нет», а не отсутствие данных.
        self.assertEqual(self.row["keys_present"], "нет")

    def test_state_taken_from_title_type_prefix(self) -> None:
        self.assertEqual(self.row["title_state"], "TX")

    def test_odometer_brand_not_actual(self) -> None:
        self.assertEqual(self.row["odometer_miles"], "142905")
        self.assertEqual(self.row["odometer_brand"], "Not Actual")

    def test_flood_and_non_repairable_flagged(self) -> None:
        self.assertIn("залив/вода", self.row["defects"])
        self.assertIn("неремонтопригоден", self.row["defects"])


class TestManheimPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.row = _parse("synthetic_manheim_lot.html")

    def test_fields_come_from_embedded_json(self) -> None:
        self.assertEqual(self.row["auction"], "Manheim")
        self.assertEqual(self.row["vin"], "5NPE24AFXGH123456")
        self.assertEqual(self.row["odometer_miles"], "96310")
        self.assertEqual(self.row["odometer_brand"], "Actual")

    def test_mmr_goes_to_its_own_column(self) -> None:
        # Регрессия: Adjusted MMR попадал в «Оценку ACV».
        self.assertEqual(self.row["mmr_adjusted_usd"], "8150")
        self.assertEqual(self.row["acv_estimate_usd"], "")

    def test_condition_grade_is_not_run_and_drive(self) -> None:
        # Регрессия: оценка состояния «3.2» вставала в колонку «На ходу».
        self.assertEqual(self.row["condition_report"], "3.2")
        self.assertEqual(self.row["run_and_drive"], "")

    def test_photos_from_json_list(self) -> None:
        self.assertEqual(self.row["photo_count"], "4")

    def test_dealer_auction_not_asked_for_damage_field(self) -> None:
        # У дилерских аукционов поля «повреждение» нет — ложного замечания быть не должно.
        self.assertNotIn("повреждение", self.row["needs_review"])


class TestRobustness(unittest.TestCase):
    def test_empty_page_does_not_crash_and_flags_everything(self) -> None:
        row = parse_lot("<html><body>Страница не загрузилась</body></html>", source_name="broken.html")
        self.assertEqual(row["vin"], "")
        self.assertIn("не найдено", row["needs_review"])

    def test_auction_detected_from_domain(self) -> None:
        doc = LotDocument('<html><body><a href="https://www.iaai.com/x">x</a></body></html>')
        self.assertEqual(detect_auction(doc), "IAAI")

    def test_vin_recovered_from_body_text_when_field_missing(self) -> None:
        row = parse_lot(
            "<html><body><p>Vehicle ident 4T1BF1FK1HU123456 sold as is</p></body></html>",
            source_name="text_only.html",
        )
        self.assertEqual(row["vin"], "4T1BF1FK1HU123456")
        self.assertIn("VIN взят из текста", row["needs_review"])


class TestReport(unittest.TestCase):
    def test_tsv_roundtrip_preserves_values(self) -> None:
        row = _parse("synthetic_copart_lot.html")
        with tempfile.TemporaryDirectory() as tmp:
            path = write_tsv([row], Path(tmp) / "run.tsv")
            with path.open(encoding="utf-8-sig") as handle:
                header = next(csv.reader(handle, delimiter="\t"))
            self.assertEqual(tuple(header), COLUMN_TITLES)
            restored = read_tsv(path)
            self.assertEqual(restored[0]["vin"], row["vin"])
            self.assertEqual(restored[0]["current_bid_usd"], row["current_bid_usd"])

    def test_xlsx_written_with_two_sheets(self) -> None:
        from openpyxl import load_workbook

        row = _parse("synthetic_copart_lot.html")
        with tempfile.TemporaryDirectory() as tmp:
            path = write_xlsx([row], Path(tmp) / "run.xlsx")
            workbook = load_workbook(path)
            self.assertIn("Памятка", workbook.sheetnames)
            sheet = workbook["Лоты"]
            self.assertEqual(sheet.cell(row=1, column=1).value, "Аукцион")
            # Числовые колонки должны быть числами, иначе в Excel не посчитать.
            bid_column = COLUMN_TITLES.index("Текущая ставка, $") + 1
            self.assertEqual(sheet.cell(row=2, column=bid_column).value, 4250)

    def test_manual_values_join_by_vin(self) -> None:
        row = _parse("synthetic_copart_lot.html")
        with tempfile.TemporaryDirectory() as tmp:
            manual_path = Path(tmp) / "manual_values.tsv"
            write_manual_template(manual_path, [row["vin"]])
            text = manual_path.read_text(encoding="utf-8-sig").splitlines()
            header = text[0].split("\t")
            values = dict(zip(header, [row["vin"]] + [""] * (len(header) - 1)))
            values["KBB Private Party, $"] = "14500"
            values["Adjusted MMR, $"] = "9200"
            with manual_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
                writer.writerow(header)
                writer.writerow([values.get(column, "") for column in header])

            manual = load_manual_values(manual_path)
            self.assertEqual(apply_manual_values([row], manual), 1)
            self.assertEqual(row["kbb_private_party_usd"], "14500")
            self.assertEqual(row["mmr_adjusted_usd"], "9200")

    def test_manual_template_keeps_existing_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manual_values.tsv"
            write_manual_template(path, ["4T1BF1FK1HU123456"])
            first = path.read_text(encoding="utf-8-sig")
            write_manual_template(path, ["1FTFW1ET7EFA12345"])
            second = path.read_text(encoding="utf-8-sig")
            self.assertIn("4T1BF1FK1HU123456", second)
            self.assertIn("1FTFW1ET7EFA12345", second)
            self.assertEqual(len(second.splitlines()), len(first.splitlines()) + 1)

    def test_cumulative_merge_dedupes_and_keeps_manual_work(self) -> None:
        row = _parse("synthetic_copart_lot.html")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lots_cumulative.tsv"
            merged, added, updated = merge_cumulative([row], path)
            write_tsv(merged, path)
            self.assertEqual((added, updated), (1, 0))

            # Оператор вписал оценки прямо в накопительную таблицу.
            stored = read_tsv(path)
            stored[0]["kbb_private_party_usd"] = "14500"
            stored[0]["max_bid_usd"] = "6800"
            write_tsv(stored, path)

            # Повторный разбор той же страницы не должен затирать ручные колонки.
            merged, added, updated = merge_cumulative([_parse("synthetic_copart_lot.html")], path)
            self.assertEqual((added, updated), (0, 1))
            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0]["kbb_private_party_usd"], "14500")
            self.assertEqual(merged[0]["max_bid_usd"], "6800")

    def test_manual_keys_cover_all_valuation_columns(self) -> None:
        for key in ("kbb_private_party_usd", "mmr_adjusted_usd", "cargurus_retail_usd", "max_bid_usd"):
            self.assertIn(key, MANUAL_KEYS)


class TestInputs(unittest.TestCase):
    def test_collect_inputs_finds_pages_in_folder(self) -> None:
        pages = collect_inputs([str(FIXTURES)])
        self.assertEqual(len(pages), 3)

    def test_mhtml_is_read(self) -> None:
        # Chrome умеет сохранять «Веб-страница, один файл» — это MHTML.
        mhtml = (
            "From: <Saved by Blink>\r\n"
            "Subject: lot\r\n"
            "MIME-Version: 1.0\r\n"
            'Content-Type: multipart/related; boundary="----=_B"\r\n\r\n'
            "------=_B\r\n"
            "Content-Type: text/html\r\n"
            "Content-Transfer-Encoding: quoted-printable\r\n\r\n"
            "<html><body><p>VIN 4T1BF1FK1HU123456</p></body></html>\r\n"
            "------=_B--\r\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lot.mhtml"
            path.write_text(mhtml, encoding="utf-8")
            self.assertIn("4T1BF1FK1HU123456", read_page(path))

    def test_missing_target_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            collect_inputs(["не-существует.html"])


if __name__ == "__main__":
    unittest.main()
