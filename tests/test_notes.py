import json
import unittest

from lot_analyzer import notes
from lot_analyzer.sites_text import _NOTE_KBB, _NOTE_MMR, _NOTE_PROXY, _NOTE_SALE


class NotesTest(unittest.TestCase):
    ROW = {"vin": "19XFB2F57EE031621", "kbb_private_party_usd": "9000", "calc_verdict": "МОЖНО до $6,500; рынок ≈ $6,800",
           "market_estimate_usd": "6800", "calc_profit_market_usd": "-11",
           "calc_profit_items": json.dumps([["Продажа: KBB PP $9,000 − $500", 8500], ["Смог-тест", -40],
                                            ["Покупка по рынку: KBB $9,000 × 0.75", -6800], ["Сборы аукциона CarMax при цене $6,800", -485]])}

    def test_note_text(self):
        text = notes.lot_note(self.ROW)
        self.assertEqual(text.splitlines()[0], "KBB 9,000$")
        self.assertIn("LA МОЖНО до $6,500 · рынок ≈$6,800 · прибыль по рынку −$11", text)
        self.assertIn("LA Продажа +$8,500; Смог-тест −$40; Покупка по рынку −$6,800; Сборы аукциона CarMax −$485", text)

    def test_only_with_real_kbb(self):
        self.assertEqual(notes.lot_note({**self.ROW, "kbb_private_party_usd": ""}), "")
        self.assertEqual(list(notes.notes_for([self.ROW, {**self.ROW, "vin": "X" * 17, "kbb_private_party_usd": ""}])), [self.ROW["vin"]])

    def test_own_lines_not_read_back_as_user_numbers(self):
        user = "3 owners. MP 5600"
        own = notes.strip_own(user + "\n" + notes.lot_note(self.ROW))
        self.assertEqual(own, user + "\nKBB 9,000$")
        self.assertEqual(_NOTE_KBB.findall(own), ["9,000"])
        self.assertEqual(_NOTE_PROXY.findall(own), ["5600"])
        self.assertEqual(_NOTE_MMR.findall(own) + _NOTE_SALE.findall(own), [])


if __name__ == "__main__":
    unittest.main()
