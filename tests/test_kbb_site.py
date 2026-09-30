import tempfile
import unittest
from pathlib import Path

from lot_analyzer import kbb_site
from lot_analyzer.bid import DEFAULT_COSTS_PATH, load_costs
from tests.test_kbb_page import kbb_html

MODEL_PAGE = """<html><script id="__NEXT_DATA__">{}</script>
<a href="/subaru/forester/2017/25i-sport-utility-4d/">2.5i</a>
<a href="/subaru/forester/2017/25i-premium-sport-utility-4d/?intent=buy-used">2.5i Premium</a>
<a href="https://www.kbb.com/subaru/forester/2017/25i-limited-sport-utility-4d/">2.5i Limited</a>
<a href="/subaru/forester/2017/20xt-touring-sport-utility-4d/">2.0XT Touring</a>
<a href="/subaru/forester/2017/cost-to-own/">Cost to own</a></html>"""


class KbbSiteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = kbb_site.CACHE_PATH
        kbb_site.CACHE_PATH = Path(self.tmp.name) / "cache.json"
        self.costs = load_costs(DEFAULT_COSTS_PATH)
        self.costs["kbb_site"] = {**self.costs.get("kbb_site", {}), "enabled": True}
        self.requested = []

    def tearDown(self):
        kbb_site.CACHE_PATH = self.old
        self.tmp.cleanup()

    def fake_fetch(self, url):
        self.requested.append(url)
        if url.endswith("/subaru/forester/2017/"):
            return MODEL_PAGE
        if "/25i-premium-sport-utility-4d/" in url:
            return kbb_html(90000)
        raise kbb_site.KbbSiteError("404")

    def test_trim_links_and_pick(self):
        slugs = kbb_site.trim_links(MODEL_PAGE, "subaru", "forester", "2017")
        self.assertEqual(slugs, ["25i-sport-utility-4d", "25i-premium-sport-utility-4d", "25i-limited-sport-utility-4d", "20xt-touring-sport-utility-4d"])
        self.assertEqual(kbb_site.pick_trim(slugs, "2.5i Premium")[0], "25i-premium-sport-utility-4d")
        self.assertEqual(kbb_site.pick_trim(slugs, "2.0XT Touring")[0], "20xt-touring-sport-utility-4d")
        picked, candidates = kbb_site.pick_trim(slugs, "")                 # трим неизвестен — спросить человека
        self.assertIsNone(picked)
        self.assertEqual(len(candidates), 4)

    def test_lookup_private_party_for_lot_mileage(self):
        got = kbb_site.lookup(self.costs, "JF2SJAGC0HH000001", "2017", "Subaru", "Forester", "2.5i Premium", "90,000", fetch=self.fake_fetch)
        self.assertEqual(got["usd"], 12180)
        self.assertEqual(got["trim_slug"], "forester/25i-premium-sport-utility-4d")
        self.assertIn("mileage=90000&zipcode=92620", self.requested[-1])
        again = kbb_site.lookup(self.costs, "JF2SJAGC0HH000001", "2017", "Subaru", "Forester", "2.5i Premium", "90000",
                                fetch=lambda url: self.fail("должно браться из памяти"))
        self.assertTrue(again["cached"])

    def test_ambiguous_trim_returns_candidates_then_choice(self):
        got = kbb_site.lookup(self.costs, "JF2SJAGC0HH000002", "2017", "Subaru", "Forester", "", "90000", fetch=self.fake_fetch)
        self.assertIn("forester/25i-premium-sport-utility-4d", got["candidates"])
        got = kbb_site.lookup(self.costs, "JF2SJAGC0HH000002", "2017", "Subaru", "Forester", "", "90000",
                              chosen="forester/25i-premium-sport-utility-4d", fetch=self.fake_fetch)
        self.assertEqual(got["usd"], 12180)

    def test_mileage_not_applied_is_an_error(self):
        fetch = lambda url: MODEL_PAGE if url.endswith("/2017/") else kbb_html(0)
        with self.assertRaises(kbb_site.KbbSiteError):
            kbb_site.lookup(self.costs, "JF2SJAGC0HH000003", "2017", "Subaru", "Forester", "2.5i Premium", "90000", fetch=fetch)

    def test_disabled_and_no_mileage(self):
        with self.assertRaises(kbb_site.KbbSiteError):
            kbb_site.lookup({**self.costs, "kbb_site": {"enabled": False}}, "X", "2017", "Subaru", "Forester", "", "90000", fetch=self.fake_fetch)
        with self.assertRaises(kbb_site.KbbSiteError):
            kbb_site.lookup(self.costs, "X", "2017", "Subaru", "Forester", "", "", fetch=self.fake_fetch)

    def test_browser_url_has_mileage_and_zip(self):
        self.assertEqual(kbb_site.browser_url(self.costs, "2014", "Lexus", "CT", "113,421"),
                         "https://www.kbb.com/lexus/ct/2014/?intent=trade-in-sell&mileage=113421&zipcode=92620")

    def test_daily_limit_stops_requests(self):
        cfg = {**kbb_site.settings(self.costs), "daily_limit": 0, "min_interval_sec": 0}
        with self.assertRaises(kbb_site.KbbSiteError) as ctx:
            kbb_site._http_get("https://www.kbb.com/", cfg, {})
        self.assertIn("лимит", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
