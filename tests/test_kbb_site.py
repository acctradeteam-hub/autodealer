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



BMW_PAGE = """<html><script id="__NEXT_DATA__">{}</script>
<a href="/bmw/5-series/2014/528i-sedan-4d/">528i</a>
<a href="/bmw/5-series/2014/535i-sedan-4d/">535i</a>
<a href="/bmw/5-series/2014/535i-xdrive-sedan-4d/">535i xDrive</a>
<a href="/bmw/5-series/2014/535d-sedan-4d/">535d</a>
<a href="/bmw/5-series/2014/550i-sedan-4d/">550i</a></html>"""

BMW_CASES = [(("535", "I"), (["5-series"], "535i")),
             (("535", "I Gran Turismo"), (["5-series-gran-turismo", "5-series"], "535i Gran Turismo")),
             (("328", "I"), (["3-series"], "328i")),
             (("328", "Base"), (["3-series"], "328i Base")),
             (("328", "XI"), (["3-series"], "328xi 328i xDrive")),
             (("328", "D"), (["3-series"], "328d")),
             (("330", "Plug In Hybrid Base"), (["3-series"], "330e Base")),
             (("530e", "Plug-in Hybrid Base"), (["5-series"], "530e Base")),
             (("M340", "I"), (["3-series"], "m340i")),
             (("430", "I Gran Coupe"), (["4-series-gran-coupe", "4-series"], "430i Gran Coupe")),
             (("750", "Li"), (["7-series"], "750li")),
             (("X3", "XDrive28i"), None), (("M5", "Base"), None), (("Z4", "3.0i"), None), (("I3", "Giga World"), None),
             (("5 Series", "535i"), None)]


class BmwSeriesTest(unittest.TestCase):
    """BMW: на аукционах модель — номер («535» + «I»), на KBB — серия («5 Series»), а номер — комплектация («535i»)."""

    def test_mapping(self):
        from lot_analyzer import kbb_page
        for (model, trim), want in BMW_CASES:
            got = kbb_page.bmw_series(model, trim)
            self.assertEqual((got[0], got[1]) if got else None, want, (model, trim))

    def test_url_lookup_and_saved_page(self):
        from lot_analyzer import kbb_page
        self.assertIn("/bmw/5-series/2014/?", kbb_site.browser_url({}, "2014", "BMW", "535", "80000"))
        self.assertIn("/bmw/3-series/2016/?", kbb_site.browser_url({}, "2016", "BMW", "328", "80000"))
        self.assertIn("/bmw/x3/2016/?", kbb_site.browser_url({}, "2016", "BMW", "X3", "80000"))
        self.assertIn("/honda/civic/2016/?", kbb_site.browser_url({}, "2016", "Honda", "Civic", "80000"))
        self.assertEqual(kbb_site.pick_trim(kbb_site.trim_links(BMW_PAGE, "bmw", "5-series", "2014"), "535i")[0], "535i-sedan-4d")
        self.assertEqual(kbb_site.pick_trim(kbb_site.trim_links(BMW_PAGE, "bmw", "5-series", "2014"), "535i xDrive")[0], "535i-xdrive-sedan-4d")
        tmp = tempfile.TemporaryDirectory()
        old = kbb_site.CACHE_PATH
        kbb_site.CACHE_PATH = Path(tmp.name) / "cache.json"
        asked = []

        def fetch(url):
            asked.append(url)
            if url.endswith("/bmw/5-series/2014/"):
                return BMW_PAGE
            if "/535i-xdrive-sedan-4d/" in url:
                return kbb_html(80000)
            raise kbb_site.KbbSiteError("404")
        try:
            costs = load_costs(DEFAULT_COSTS_PATH)
            costs["kbb_site"] = {**costs.get("kbb_site", {}), "enabled": True}
            got = kbb_site.lookup(costs, "WBA5B3C50ED000001", "2014", "BMW", "535", "I xDrive", "80000", fetch=fetch)
        finally:
            kbb_site.CACHE_PATH = old
            tmp.cleanup()
        self.assertEqual(got["trim_slug"], "5-series/535i-xdrive-sedan-4d")
        self.assertFalse(any("/bmw/535" in u for u in asked), asked)
        record = {"year": "2014", "make": "bmw", "model": "5-series", "miles": 80500}                 # сохранена вручную, без VIN
        self.assertTrue(kbb_page.matches(record, {"year": "2014", "make": "BMW", "model": "535", "trim": "I", "odometer_miles": "80000"}, 3000))
        self.assertFalse(kbb_page.matches(record, {"year": "2014", "make": "BMW", "model": "335", "trim": "I", "odometer_miles": "80000"}, 3000))

    def test_extension_same_as_program(self):
        """Автопилот KBB в расширении (content.js) переводит BMW так же, как программа."""
        import json
        import re
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("нет node")
        src = (Path(__file__).resolve().parent.parent / "tools" / "kbb_extension" / "content.js").read_text(encoding="utf-8")
        fn = re.search(r"var bmwSeries = (function \(model, trim\) \{.*?\n    \});", src, re.S).group(1)
        script = f"var f = {fn};\nvar cases = {json.dumps([list(c) for c, _ in BMW_CASES])};\n" \
                 "console.log(JSON.stringify(cases.map(function (c) { var r = f(c[0], c[1]); return r ? [r.models, r.t] : null; })));"
        out = json.loads(subprocess.run([node, "-e", script], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out, [list(w) if w else None for _, w in BMW_CASES])
        self.assertIn("var models = bmw ? bmw.models :", src)


MB_PAGE = """<html><script id="__NEXT_DATA__">{}</script>
<a href="/mercedes-benz/c-class/2016/c-300-sedan-4d/">C 300</a>
<a href="/mercedes-benz/c-class/2016/c-300-4matic-sedan-4d/">C 300 4MATIC</a>
<a href="/mercedes-benz/c-class/2016/c-300-coupe-2d/">C 300 Coupe</a>
<a href="/mercedes-benz/c-class/2016/c-450-amg-sedan-4d/">C 450 AMG</a>
<a href="/mercedes-benz/c-class/2016/c-63-amg-sedan-4d/">C 63 AMG</a></html>"""

MB_CASES = [(("C300", "Base", "2016"), (["c-class"], "C 300 Base")),
            (("C300", "Sport", "2012"), (["c-class"], "C 300 Sport")),
            (("E350", "Luxury", "2014"), (["e-class"], "E 350 Luxury")),
            (("GLE350", "Base", "2018"), (["gle"], "GLE 350 Base")),
            (("GLE53", "AMG Coupe Base", "2021"), (["mercedes-amg-gle-coupe", "mercedes-amg-gle", "gle-coupe", "gle"], "GLE 53 AMG Coupe Base")),
            (("E63", "AMG S", "2018"), (["mercedes-amg-e-class", "e-class"], "E 63 AMG S")),
            (("E63", "AMG S", "2012"), (["e-class", "mercedes-amg-e-class"], "E 63 AMG S")),
            (("C43", "AMG Base", "2018"), (["mercedes-amg-c-class", "c-class"], "C 43 AMG Base")),
            (("CLA250", "Base", "2015"), (["cla-class", "cla"], "CLA 250 Base")),
            (("CLA250", "Base", "2018"), (["cla", "cla-class"], "CLA 250 Base")),
            (("GLC300", "Base", "2019"), (["glc"], "GLC 300 Base")),
            (("GLC350E", "Plug In Hybrid Base", "2019"), (["glc"], "GLC 350e Base")),
            (("ML350", "Base", "2014"), (["m-class"], "ML 350 Base")),
            (("GL450", "Base", "2014"), (["gl-class"], "GL 450 Base")),
            (("GLK350", "Base", "2013"), (["glk-class"], "GLK 350 Base")),
            (("GLS450", "Base", "2020"), (["gls"], "GLS 450 Base")),
            (("S550", "Base", "2015"), (["s-class"], "S 550 Base")),
            (("SLC300", "Base", "2018"), (["slc", "slc-class"], "SLC 300 Base")),
            (("A220", "Base", "2020"), (["a-class"], "A 220 Base")),
            (("EQB", "300 Base", "2023"), (["mercedes-eq-eqb"], "EQB 300 Base")),
            (("EQS450+", "Base", "2023"), (["mercedes-eq-eqs"], "EQS 450+ Base")),
            (("AMG", "GT Base", "2018"), (["mercedes-amg-gt"], "GT Base")),
            (("B C300W C 300", "", "2018"), (["c-class"], "C 300")),                       # Manheim
            (("G63W4 Amg Amg G 63", "", "2021"), (["mercedes-amg-g-class", "g-class"], "G 63 AMG")),
            (("Sprinter", "2500 Base", "2019"), None), (("B-Class", "Electric Drive", "2017"), None),
            (("Maybach", "S 580", "2022"), None), (("Metris", "Base", "2018"), None)]


class MercedesClassTest(unittest.TestCase):
    """Mercedes: на аукционах модель — «C300», на KBB — класс («C-Class»), а «C 300» — комплектация."""

    def test_mapping(self):
        from lot_analyzer import kbb_page
        for (model, trim, year), want in MB_CASES:
            got = kbb_page.mercedes_class(model, trim, year)
            self.assertEqual((got[0], got[1]) if got else None, want, (model, trim, year))
        self.assertEqual(kbb_page.kbb_model("Mercedes-Benz", "C300", "Base", "2016")[0], ["c-class"])
        self.assertIsNone(kbb_page.kbb_model("Honda", "C300", "", "2016"))

    def test_url_lookup_and_saved_page(self):
        from lot_analyzer import kbb_page
        self.assertIn("/mercedes-benz/c-class/2016/?", kbb_site.browser_url({}, "2016", "Mercedes-Benz", "C300", "60000"))
        self.assertIn("/mercedes-benz/gle/2018/?", kbb_site.browser_url({}, "2018", "Mercedes-Benz", "GLE350", "60000"))
        links = kbb_site.trim_links(MB_PAGE, "mercedes-benz", "c-class", "2016")
        self.assertEqual(kbb_site.pick_trim(links, kbb_page.mercedes_class("C300", "Base", "2016")[1])[0], "c-300-sedan-4d")
        tmp = tempfile.TemporaryDirectory()
        old = kbb_site.CACHE_PATH
        kbb_site.CACHE_PATH = Path(tmp.name) / "cache.json"
        asked = []

        def fetch(url):
            asked.append(url)
            if url.endswith("/mercedes-benz/c-class/2016/"):
                return MB_PAGE
            if "/c-300-sedan-4d/" in url:
                return kbb_html(60000)
            raise kbb_site.KbbSiteError("404")
        try:
            costs = load_costs(DEFAULT_COSTS_PATH)
            costs["kbb_site"] = {**costs.get("kbb_site", {}), "enabled": True}
            got = kbb_site.lookup(costs, "55SWF4JB0GU000001", "2016", "Mercedes-Benz", "C300", "Base", "60000", fetch=fetch)
        finally:
            kbb_site.CACHE_PATH = old
            tmp.cleanup()
        self.assertEqual(got["trim_slug"], "c-class/c-300-sedan-4d")
        self.assertFalse(any("/c300/" in u or "/c-300/" in u for u in asked), asked)
        record = {"year": "2016", "make": "mercedes-benz", "model": "c-class", "miles": 60400}
        self.assertTrue(kbb_page.matches(record, {"year": "2016", "make": "Mercedes-Benz", "model": "C300", "trim": "Base", "odometer_miles": "60000"}, 3000))

    def test_extension_same_as_program(self):
        """Автопилот KBB в расширении (content.js) переводит Mercedes так же, как программа."""
        import json
        import re
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("нет node")
        src = (Path(__file__).resolve().parent.parent / "tools" / "kbb_extension" / "content.js").read_text(encoding="utf-8")
        block = re.search(r"(var MB_CLASS = .*?var mbClass = function \(model, trim, year\) \{.*?\n    \};)", src, re.S).group(1)
        script = f"{block}\nvar cases = {json.dumps([list(c) for c, _ in MB_CASES])};\n" \
                 "console.log(JSON.stringify(cases.map(function (c) { var r = mbClass(c[0], c[1], c[2]); return r ? [r.models, r.t] : null; })));"
        out = json.loads(subprocess.run([node, "-e", script], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out, [list(w) if w else None for _, w in MB_CASES])
        self.assertIn("mbClass(car.md, car.t, car.y)", src)

if __name__ == "__main__":
    unittest.main()
