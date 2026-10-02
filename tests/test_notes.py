import unittest

from lot_analyzer import notes
from lot_analyzer.sites_text import _NOTE_KBB, _NOTE_MMR, _NOTE_PROXY, _NOTE_SALE


class NotesTest(unittest.TestCase):
    ROW = {"vin": "19XFB2F57EE031621", "auction": "CarMax", "kbb_private_party_usd": "9000", "kbb_date": "2026-10-02",
           "auction_notes": "Major engine defect, Prior rental", "calc_verdict": "МОЖНО до $6,500"}

    def test_only_kbb_with_date_and_auction_remarks(self):
        self.assertEqual(notes.lot_note(self.ROW), "KBB 9,000$ 10/2/26\nCarMax: Major engine defect, Prior rental")

    def test_only_with_real_kbb(self):
        self.assertEqual(notes.lot_note({**self.ROW, "kbb_private_party_usd": ""}), "")
        self.assertEqual(list(notes.notes_for([self.ROW, {**self.ROW, "vin": "X" * 17, "kbb_private_party_usd": ""}])), [self.ROW["vin"]])

    def test_own_lines_not_read_back_as_user_numbers(self):
        user = "3 owners. MP 5600"
        own = notes.strip_own(user + "\n" + notes.lot_note(self.ROW) + "\nLA old calc MMR $5,000")
        self.assertEqual(own, user + "\nKBB 9,000$ 10/2/26")
        self.assertEqual(_NOTE_KBB.findall(own), ["9,000"])
        self.assertEqual(_NOTE_PROXY.findall(own), ["5600"])
        self.assertEqual(_NOTE_MMR.findall(own) + _NOTE_SALE.findall(own), [])


if __name__ == "__main__":
    unittest.main()
