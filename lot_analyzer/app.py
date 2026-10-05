"""«Одно окно»: поиск одной машины сразу по всем аукционам.

    python3 -m lot_analyzer.app            # откроется http://127.0.0.1:8765

1. В окне вводите машину («Honda Civic»), годы, пробег и, если знаете, KBB.
2. Кнопки открывают поиск этой машины на каждом аукционе.
3. На каждой вкладке нажимаете закладку «💾 Сохранить для анализа» — файл ложится
   в «Загрузки», окно само его подхватывает и показывает общую таблицу по всем
   аукционам: потолок ставки, «рынок», вердикт, ссылка на лот.

Работает только на вашем компьютере (127.0.0.1), в сеть ничего не отправляет.
Ссылки поиска по аукционам — config/search_urls.json; свои сохранённые поиски
можно вписать прямо в окне, они хранятся в data/search_links.json.
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from .bid import DEFAULT_COSTS_PATH, apply_to_rows, is_electric, load_costs
from .normalize import parse_money
from .inspection import render_html
from . import analytics, inbox, kbb_page, kbb_site, manheim_csv, notes, popular, results
from .pages import read_page
from .parsers import parse_page
from .paths import DATA_DIR

SEARCH_URLS_PATH = Path("config/search_urls.json")
LINKS_PATH = DATA_DIR / "search_links.json"
KBB_PATH = DATA_DIR / "kbb_values.json"            # KBB PP из приложения, вписанный в окне: {VIN: {"usd": …, "miles": …, "date": …}}
SAVED_BY_BOOKMARKLET = re.compile(r"^(CarMax|ACV|Manheim|ADESA|KBB|auction)_.+\.html?$", re.I)
SAVED_KBB = re.compile(r"kelley[\s_-]*blue[\s_-]*book.*\.(html?|mhtml?)$", re.I)     # страница KBB, сохранённая через Cmd+S
SHOW_ROWS = 300                              # в окне — лучшие 300, иначе браузер тормозит на тысячах машин
GROUPS = ("popular", "ev", "truck", "other")
# Версия закладки (как LA_VERSION в tools/bookmarklet/save_auction_page.js): файлы старой закладки окно помечает.
BOOKMARKLET_VERSION = "2026-10-05.4"


def bookmarklet_version(path: Path) -> str | None:
    """Версия закладки, сохранившей файл: «2026-10-05», «» — старая (без версии), None — файл не закладки."""
    if path.suffix.lower() not in (".html", ".htm"):
        return None
    try:
        with path.open("rb") as handle:
            head = handle.read(700).decode("utf-8", errors="replace")
    except OSError:
        return None
    if "saved-by: lot_analyzer bookmarklet" not in head:
        return None
    found = re.search(r"saved-by: lot_analyzer bookmarklet; version: ([\d.-]+);", head)
    return found.group(1) if found else ""
RANK = {"НУЖЕН": 0.5, "МОЖНО": 0, "ОСМОТР:": 0, "ОСМОТР": 0, "ДОРОЖЕ": 1, "НЕТ": 2, "НЕВЫГОДНО": 3, "ПРОПУСТИТЬ": 4}


# ---------------------------------------------------------------- файлы из «Загрузок»

class PageCache:
    """Разбор сохранённых страниц с кэшем по времени изменения файла."""

    def __init__(self) -> None:
        self._cache: dict[Path, tuple[float, list[dict[str, str]]]] = {}
        self._kbb: dict[Path, dict | None] = {}
        self._lock = threading.Lock()

    def kbb(self, path: Path) -> dict | None:
        """Значения KBB, если файл — сохранённая страница kbb.com (иначе None)."""
        self.rows(path)
        with self._lock:
            return self._kbb.get(path)

    def rows(self, path: Path) -> list[dict[str, str]]:
        mtime = path.stat().st_mtime
        with self._lock:
            cached = self._cache.get(path)
            if cached and cached[0] == mtime:
                return [dict(r) for r in cached[1]]
        try:
            record = None
            if results.is_results_file(path):
                rows, record = [], {"results": results.read_file(path)}     # итоги торгов — не лоты
            elif path.suffix.lower() == ".csv":
                rows = manheim_csv.read_export(path)
            else:
                html = read_page(path)
                if kbb_page.is_kbb(html):
                    record, rows = kbb_page.parse(html), []
                    if record:
                        record["file"] = path.name
                    elif 'id="kbb-report"' in html:            # отчёт автопилота KBB: что получилось и что нет
                        from bs4 import BeautifulSoup
                        report = BeautifulSoup(html, "lxml").select_one("#kbb-report")
                        record = {"report": report.get_text("\n", strip=True) if report else ""}
                else:
                    rows = parse_page(html, source_name=path.name)
        except Exception as error:  # битый файл не должен ронять окно
            record = None
            rows = [{"auction": "?", "source_file": path.name, "needs_review": f"не разобрано: {error}"}]
        with self._lock:
            self._cache[path] = (mtime, rows)
            self._kbb[path] = record
        return [dict(r) for r in rows]


OWN_WINDOW_RE = re.compile(r"url: https?://(?:127\.0\.0\.1|localhost)[:/]")


def is_own_window(path: Path) -> bool:
    """Копия самого окна программы (закладку нажали на 127.0.0.1) — не страница аукциона, пропускаем."""
    if path.suffix.lower() not in (".html", ".htm"):
        return False
    try:
        with path.open("rb") as handle:
            head = handle.read(600).decode("utf-8", errors="replace")
    except OSError:
        return False
    return bool(OWN_WINDOW_RE.search(head))


def find_pages(folders: list[Path], hours: float) -> list[Path]:
    """Страницы, сохранённые закладкой (имя «CarMax_…», «ACV_…» и т.п.) за последние `hours` часов."""
    cutoff = time.time() - hours * 3600
    found = []
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if not path.is_file() or path.stat().st_mtime < cutoff:
                continue
            if (SAVED_BY_BOOKMARKLET.match(path.name) or SAVED_KBB.search(path.name) or manheim_csv.is_export(path)
                    or results.is_results_file(path)) and not is_own_window(path):
                found.append(path)
    # Новые файлы первыми; CSV-выгрузки — в конце: строка со страницы подробнее (AutoCheck, объявления).
    return sorted(found, key=lambda p: (p.suffix.lower() == ".csv", -p.stat().st_mtime))


# ---------------------------------------------------------------- отбор и расчёт

def matches(row: dict[str, str], query: str, year_from: int | None, year_to: int | None, max_miles: int | None) -> bool:
    text = " ".join(row.get(k, "") for k in ("year", "make", "model", "trim")).lower().replace("-", " ")
    for token in query.lower().replace("-", " ").split():
        if token not in text:
            return False
    year = int(row["year"]) if str(row.get("year", "")).isdigit() else None
    if year is not None and ((year_from and year < year_from) or (year_to and year > year_to)):
        return False
    miles = int(row["odometer_miles"]) if str(row.get("odometer_miles", "")).isdigit() else None
    if miles is not None and max_miles and miles > max_miles:
        return False
    return True


def attach_results(rows: list[dict[str, str]], pages: list[Path], cache: PageCache, costs: dict) -> dict:
    """Итоги торгов: копит их в data/auction_results.csv, пишет «Итог торгов» в строки лотов
    и возвращает настройки с «рынком» площадок, у которых набралось достаточно своих продаж."""
    history = results.load_history()
    new = [rec for p in pages for rec in ((cache.kbb(p) or {}).get("results") or [])]
    changed = results.merge(history, new)
    by_vin, by_ymm = results.index(history)
    today = time.strftime("%Y-%m-%d")
    for row in rows:
        # Эта машина (VIN) уже была на торгах — по вашей базе: когда, где и чем кончилось.
        past = sorted((r for r in by_vin.get(row.get("vin", ""), []) if r.get("date") and r["date"] < today), key=lambda r: r["date"])
        row["seen_before"] = "; ".join(results.short(r) for r in past)
        rec = results.find(row, by_vin, by_ymm)
        if not rec:
            continue
        mmr = parse_money(row.get("mmr_adjusted_usd"))
        if mmr and not rec.get("mmr"):                     # в PDF итогов MMR нет — берём из списка до торгов
            rec["mmr"] = f"{mmr:.0f}"
            changed = True
        # Что было известно до торгов — в итог: потом по нему видно, почему машина ушла дешевле или дороже.
        kbb = parse_money(row.get("kbb_private_party_usd"))
        if kbb and not rec.get("kbb") and not row.get("kbb_from_window_field"):
            rec["kbb"] = f"{kbb:.0f}"
            changed = True
        remarks = row.get("auction_notes") or row.get("defects") or ""
        if remarks and not rec.get("remarks"):
            rec["remarks"] = remarks[:300]
            changed = True
        row["auction_result"] = results.describe(rec, mmr)
        row["auction_result_price"] = rec.get("price", "") if rec["outcome"] == "Sold" else ""
    if changed:
        results.save_history(history)
    own = {name: s for name, s in results.stats(history).items() if s["enough"]}
    # CarMax: своя доля «цена ÷ KBB» площадки (Chino, Oxnard …), когда своих продаж достаточно.
    for place, extra in analytics.market_overrides(analytics.joined_sales(history, analytics.load_kbb_log())).items():
        own[place] = {**own.get(place, {}), **extra}
    return {**costs, "market_by_location": own} if own else costs


def search_rows(pages: list[Path], cache: PageCache, costs: dict, query: str = "", year_from: int | None = None,
                year_to: int | None = None, max_miles: int | None = None, kbb: float | None = None,
                only_no_photos: bool = False, max_bid: float | None = None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    saved_kbb = load_kbb()
    bids = load_bids()
    kbb_cfg = costs.get("kbb_page") or {}
    condition = kbb_cfg.get("condition", "good")
    kbb_pages = [{**r, "_date": time.strftime("%Y-%m-%d", time.localtime(p.stat().st_mtime))}
                 for p, r in ((p, cache.kbb(p)) for p in pages) if r and r.get("miles")]   # без пробега — не подставляем
    for path in pages:                       # новые файлы первыми: дубликаты берутся из свежего
        for row in cache.rows(path):
            key = row.get("vin") or f"{row.get('auction')}:{row.get('lot_number')}:{row.get('location')}"
            if key in seen or not matches(row, query, year_from, year_to, max_miles):
                continue
            if only_no_photos and row.get("no_photos") != "да":
                continue
            seen.add(key)
            bid = bids.get(row.get("vin", ""))
            if bid:                           # ставка, вписанная в окне, — главнее «MP …» из заметки
                row["my_proxy_usd"] = str(bid["usd"])
                row["my_bid_from_window"] = "1"
            own = saved_kbb.get(row.get("vin", ""))
            if own:                          # вписан в окне по этому VIN — главнее всего
                row["kbb_private_party_usd"] = f"{float(own['usd']):.0f}"
                row["kbb_entered"] = " ".join(x for x in (own.get("source", "вручную"), own.get("note", ""), own.get("date", "")) if x)
                row["kbb_url"] = own.get("url", "")
                row["kbb_date"] = own.get("date", "")
            elif not row.get("kbb_private_party_usd"):
                page = next((r for r in kbb_pages if kbb_page.matches(r, row, int(kbb_cfg.get("max_miles_gap", 3000)))), None)
                if page and page["private_party"].get(condition):
                    row["kbb_private_party_usd"] = str(page["private_party"][condition])
                    row["kbb_entered"] = f"страница KBB: {kbb_page.describe(page)}"
                    row["kbb_date"] = page.get("_date", "")
                    if page["zip"] != str(kbb_cfg.get("zip", "92620")):
                        row["needs_review"] = "; ".join(x for x in (row.get("needs_review", ""), f"KBB для ZIP {page['zip']}, не {kbb_cfg.get('zip', '92620')}") if x)
            if kbb and not (row.get("kbb_private_party_usd") or row.get("retail_estimate_usd")):
                row["kbb_private_party_usd"] = f"{kbb:.0f}"
                row["kbb_from_window_field"] = "1"           # не настоящий KBB этой машины — в базу не пишем
                row["needs_review"] = "; ".join(x for x in (row.get("needs_review", ""), "KBB — из окна поиска, одинаковый для всех") if x)
            rows.append(row)
    kbb_log = analytics.load_kbb_log()
    if analytics.record_kbb(rows, kbb_log):            # каждый настоящий KBB — в свою базу
        analytics.save_kbb_log(kbb_log)
    costs = attach_results(rows, pages, cache, costs)
    # Машинам без KBB — предварительная цена продажи по вашей базе (KBB ÷ MMR той же модели), а не MMR × 1.3.
    apply_to_rows(rows, costs, estimator=analytics.OwnKbb(kbb_log))
    if max_bid:                              # дороже своего бюджета — не показываем
        rows = [r for r in rows if float(r.get("calc_max_bid_usd") or 0) <= max_bid]
    # Вкладки окна: популярные модели (Civic, Camry, RAV4 …), электромобили, пикапы, остальные; в каждой — сверху самые выгодные.
    for r in rows:
        r["place"] = place_of_row(r)
        r["popular"] = "да" if popular.is_popular(r, costs) else ""
        r["group"] = "popular" if r["popular"] else "truck" if popular.is_pickup(r) else "ev" if is_electric(r) else "other"
    rows.sort(key=lambda r: (GROUPS.index(r["group"]), verdict_rank(r.get("calc_verdict", "")), -expected_gain(r)))
    return rows


BIDS_PATH = DATA_DIR / "my_bids.json"


def load_bids() -> dict[str, dict]:
    try:
        return json.loads(BIDS_PATH.read_text(encoding="utf-8")) if BIDS_PATH.exists() else {}
    except (OSError, ValueError):
        return {}


def save_bid(vin: str, usd: float | None) -> None:
    """Ваша ставка по VIN (вписана в окне); пустое значение — стереть."""
    bids = load_bids()
    if usd:
        bids[vin] = {"usd": round(usd), "date": time.strftime("%Y-%m-%d")}
    else:
        bids.pop(vin, None)
    BIDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    BIDS_PATH.write_text(json.dumps(bids, ensure_ascii=False, indent=1), encoding="utf-8")


def load_kbb() -> dict[str, dict]:
    try:
        return json.loads(KBB_PATH.read_text(encoding="utf-8")) if KBB_PATH.exists() else {}
    except (OSError, ValueError):
        return {}


def save_kbb(vin: str, usd: float | None, miles: str = "", source: str = "вручную", note: str = "", url: str = "") -> None:
    """Запоминает KBB по VIN; пустое значение — стереть."""
    values = load_kbb()
    if usd:
        values[vin] = {"usd": round(usd), "miles": miles, "date": time.strftime("%Y-%m-%d"), "source": source, "note": note, "url": url}
    else:
        values.pop(vin, None)
    KBB_PATH.parent.mkdir(parents=True, exist_ok=True)
    KBB_PATH.write_text(json.dumps(values, ensure_ascii=False, indent=1), encoding="utf-8")


def place_of_row(row: dict) -> str:
    """Площадка лота одним названием: «CarMax Murrieta», «CarMax Oceanside», «Manheim California» …"""
    auction = str(row.get("auction", "")).strip()
    place = str(row.get("location", "")).strip()
    if " - " in place:                         # Manheim: «CA - Manheim California»
        place = place.split(" - ", 1)[1]
    place = place.split(",")[0].strip()        # CarMax: «Chino, CA»
    if auction and place.lower().startswith(auction.lower()):
        return place
    if auction == "ACV":                       # ACV — машины у дилеров по всему региону, площадки нет
        return "ACV"
    if auction == "Manheim" and place:         # машина стоит у продавца (Santa Ana, Anaheim …), не на площадке Manheim
        return "Manheim — у продавца (не на площадке)"
    return f"{auction} {place}".strip() or "—"


def picked(row: dict) -> bool:
    """Отобранная вами машина: есть настоящий KBB (вы его смотрели) или ваша ставка."""
    return bool((row.get("kbb_private_party_usd") and not row.get("kbb_from_window_field")) or row.get("my_bid_from_window"))


def by_place(rows: list[dict], q: dict) -> tuple[list[dict], list[list]]:
    """Отбор по площадке (q["place"]) и «только отобранные» (q["picked"]); плюс список площадок со счётчиками."""
    if q.get("picked") == "1":
        rows = [r for r in rows if picked(r)]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["place"]] = counts.get(r["place"], 0) + 1
    places = sorted(([name, n] for name, n in counts.items()), key=lambda x: (-x[1], x[0]))
    if q.get("place"):
        rows = [r for r in rows if r["place"] == q["place"]]
    return rows, places


def verdict_rank(verdict: str) -> float:
    """Место вердикта в сортировке. «РИСК: рама …; МОЖНО до …» — по тому, что после риска: машину с выгодой — наверх."""
    if verdict.startswith("РИСК") and "; " in verdict:
        verdict = verdict.split("; ", 1)[1]
    return RANK.get(verdict.split(" ")[0].rstrip(":"), 5)


def expected_gain(row: dict[str, str]) -> float:
    """Порядок в окне: сначала прибыль при покупке по рынку (сколько реально заработаете), иначе прибыль при потолке × шанс."""
    if row.get("calc_profit_market_usd"):
        return float(row["calc_profit_market_usd"]) * (0.5 if "грубо по MMR" in row.get("calc_verdict", "") else 1.0)
    profit = float(row.get("calc_profit_usd") or 0)
    chance = float(row["calc_win_chance_pct"]) / 100 if row.get("calc_win_chance_pct") else 0.3
    if "грубо по MMR" in row.get("calc_verdict", ""):
        chance *= 0.5                        # цена продажи — прикидка, доверия меньше
    return profit * chance


# ---------------------------------------------------------------- ссылки поиска

def search_links(query: str, year_from: int | None, year_to: int | None) -> list[dict[str, str]]:
    templates = json.loads(SEARCH_URLS_PATH.read_text(encoding="utf-8")) if SEARCH_URLS_PATH.exists() else {}
    own = json.loads(LINKS_PATH.read_text(encoding="utf-8")) if LINKS_PATH.exists() else {}
    words = query.split()
    make = words[0].title() if words else ""
    model = " ".join(words[1:]).title() if len(words) > 1 else ""
    values = {
        "make": quote(make), "model": quote(model), "query": quote(query), "query_plus": "+".join(quote(w) for w in words),
        "year_from": str(year_from or ""), "year_to": str(year_to or ""),
    }
    links = []
    for auction, spec in templates.items():
        if auction.startswith("_"):
            continue
        saved = own.get(query.lower().strip(), {}).get(auction)
        if saved:
            links.append({"auction": auction, "url": saved, "kind": "ваш сохранённый поиск"})
        elif spec.get("template") and make:
            url = spec["template"]
            for key, value in values.items():
                url = url.replace("{" + key + "}", value)
            url = url.replace("&year=-", "")          # годы не заданы
            links.append({"auction": auction, "url": url, "kind": "поиск по шаблону"})
        else:
            links.append({"auction": auction, "url": spec.get("home", ""), "kind": spec.get("home_title") or "страница поиска — впишите модель или сохраните свой поиск"})
    return links


def save_link(query: str, auction: str, url: str) -> None:
    own = json.loads(LINKS_PATH.read_text(encoding="utf-8")) if LINKS_PATH.exists() else {}
    own.setdefault(query.lower().strip(), {})[auction] = url
    LINKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LINKS_PATH.write_text(json.dumps(own, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- веб-сервер

COLUMNS = ("auction", "location", "lot_number", "year", "make", "model", "trim", "exterior_color", "odometer_miles", "current_bid_usd",
           "kbb_private_party_usd", "kbb_estimate_usd", "kbb_estimate_source", "market_estimate_usd", "calc_max_bid_usd", "calc_profit_usd", "calc_win_chance_pct", "calc_verdict",
           "sale_estimate_usd", "calc_profit_market_usd", "calc_profit_items", "auction_result", "auction_result_price", "condition_grade", "cr_url", "photo_main_url",
           "sale_date", "lot_url", "vin", "my_proxy_usd", "no_photos", "inspect", "calc_max_bid_if_defect_usd", "defects", "source_file", "calc_breakdown", "needs_review")


def make_handler(folders: list[Path], costs_path: Path, cache: PageCache):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:  # тихо
            pass

        def _send(self, body: bytes, kind: str = "application/json; charset=utf-8", status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            num = lambda key: int(q[key]) if q.get(key, "").isdigit() else None
            if url.path == "/":
                self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif url.path == "/api/rows":
                pages = find_pages(folders, float(q.get("hours") or 24))
                rows = search_rows(pages, cache, load_costs(costs_path), q.get("q", ""), num("y1"), num("y2"), num("miles"),
                                   float(q["kbb"]) if q.get("kbb", "").replace(".", "").isdigit() else None,
                                   only_no_photos=q.get("nophoto") == "1", max_bid=num("maxbid"))
                rows, places = by_place(rows, q)
                total = len(rows)
                all_rows = rows
                # По SHOW_ROWS из каждой части: одна не вытесняет другие.
                rows = [r for g in GROUPS for r in [x for x in rows if x.get("group") == g][:SHOW_ROWS]]
                kbb_cfg = load_costs(costs_path).get("kbb_page") or {}
                files = []
                for p in pages:
                    item = {"name": p.name, "time": time.strftime("%H:%M", time.localtime(p.stat().st_mtime)), "cars": len(cache.rows(p))}
                    version = bookmarklet_version(p)
                    if version is not None and version < BOOKMARKLET_VERSION and not p.name.startswith("KBB_"):
                        item["old"] = "старая закладка — машин может быть не все: переустановите её (кнопка «🔖 Закладка») и сохраните снова"
                    record = cache.kbb(p)
                    if record and "results" in record:
                        recs = record["results"]
                        sold = sum(1 for r in recs if r["outcome"] == "Sold")
                        where = ", ".join(sorted({f"{r['auction']} {r['date']}" for r in recs}))
                        item["kbb"] = f"Итоги торгов {where}: {len(recs)} машин, продано {sold} — сохранены в историю"
                    elif record and "report" in record:
                        item["kbb"] = record["report"].replace("\n", " · ")
                        if "НЕТ " in record["report"]:
                            item["kbb"] = "⚠ " + item["kbb"]
                    elif record:
                        pp = record["private_party"].get(kbb_cfg.get("condition", "good"))
                        warn = ("" if record["miles"] else " — ⚠ пробег не указан на KBB, не подставлено: введите пробег на kbb.com и сохраните снова")
                        if not pp:
                            warn += " — ⚠ нет Private Party (Good) — на KBB нажмите «Sell it yourself», состояние Good, и сохраните снова"
                        item["kbb"] = (f"KBB {record['year']} {record['make']} {record['model']} {kbb_page.describe(record)}: "
                                       + (f"PP Good ${pp:,}" if pp else "") + warn)
                    files.append(item)
                costs_now = load_costs(costs_path)
                payload = {"rows": [{**{k: r.get(k, "") for k in COLUMNS}, "kbb_entered": r.get("kbb_entered", ""), "kbb_url": r.get("kbb_url", ""), "popular": r.get("popular", ""), "group": r.get("group", "other"),
                                     "profit_at_my_bid_usd": r.get("profit_at_my_bid_usd", ""), "seen_before": r.get("seen_before", ""),
                                     "kbb_open": kbb_site.browser_url(costs_now, r.get("year", ""), r.get("make", ""), r.get("model", ""), r.get("odometer_miles", ""))}
                                    for r in rows], "files": files, "total": total, "places": places, "place": q.get("place", ""), "popular_total": sum(1 for r in all_rows if r.get("popular")),
                           "group_total": {g: sum(1 for r in all_rows if r.get("group") == g) for g in GROUPS},
                           "folders": [str(f) for f in folders],
                           "results_stats": {k: {"n": v["n"], "median": v["median"], "enough": v["enough"]}
                                             for k, v in results.stats(results.load_history()).items()}}
                self._send(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            elif url.path == "/inspection":
                pages = find_pages(folders, float(q.get("hours") or 24))
                rows = search_rows(pages, cache, load_costs(costs_path), q.get("q", ""), num("y1"), num("y2"), num("miles"),
                                   float(q["kbb"]) if q.get("kbb", "").replace(".", "").isdigit() else None, max_bid=num("maxbid"))
                rows, _ = by_place(rows, q)
                title = ("На осмотр" + (f" — {q['place']}" if q.get("place") else "") + (" — отобранные" if q.get("picked") == "1" else "")
                         + (f" — {q['q']}" if q.get("q") else "" if q.get("place") or q.get("picked") == "1" else " — все машины из файлов"))
                self._send(render_html(rows, title).encode("utf-8"), "text/html; charset=utf-8")
            elif url.path == "/api/files":         # дёшево: только список файлов — окно пересчитывает таблицу, если он изменился
                pages = find_pages(folders, float(q.get("hours") or 24))
                self._send(json.dumps([f"{p.name}:{p.stat().st_mtime:.0f}" for p in pages]).encode("utf-8"))
            elif url.path == "/analytics":           # своя база: KBB, итоги торгов, доли по площадкам и моделям
                pages = find_pages(folders, float(q.get("hours") or 168))
                search_rows(pages, cache, load_costs(costs_path), "")      # свежие файлы — в базу
                self._send(analytics.render(results.load_history(), analytics.load_kbb_log()).encode("utf-8"), "text/html; charset=utf-8")
            elif url.path == "/install":               # страница установки закладки и расширения
                page = Path(__file__).resolve().parent.parent / "tools" / "bookmarklet" / "install.html"
                if page.exists():
                    self._send(page.read_bytes(), "text/html; charset=utf-8")
                else:
                    self._send("Нет файла tools/bookmarklet/install.html — скачайте программу заново.".encode("utf-8"), "text/plain; charset=utf-8", 404)
            elif url.path == "/api/inbox":            # состояние отправки Claude (токен не показывается)
                self._send(json.dumps(inbox.state(), ensure_ascii=False).encode("utf-8"))
            elif url.path == "/api/notes":         # для расширения: тексты заметок (Notes) машинам с настоящим KBB
                pages = find_pages(folders, float(q.get("hours") or 168))
                rows = search_rows(pages, cache, load_costs(costs_path), "")
                self._send(json.dumps(notes.notes_for(rows), ensure_ascii=False).encode("utf-8"))
            elif url.path == "/api/links":
                self._send(json.dumps(search_links(q.get("q", ""), num("y1"), num("y2")), ensure_ascii=False).encode("utf-8"))
            else:
                self._send(b'{"error":"not found"}', status=404)

        def do_POST(self) -> None:
            if urlparse(self.path).path == "/api/inbox":
                length = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(length) or b"{}")
                repo = str(data.get("repo", "")).strip()
                if data.get("enabled") and not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
                    self._send('{"error":"репозиторий в виде владелец/имя"}'.encode(), status=400)
                    return
                inbox.save_settings(repo, data.get("token") or None, bool(data.get("enabled")))
                self._send(json.dumps(inbox.state(), ensure_ascii=False).encode("utf-8"))
                return
            if urlparse(self.path).path == "/api/bid":
                length = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(length) or b"{}")
                vin = str(data.get("vin", "")).strip().upper()
                value = str(data.get("usd", "")).replace("$", "").replace(",", "").strip()
                if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin) or (value and not value.replace(".", "", 1).isdigit()):
                    self._send(b'{"error":"need vin and number"}', status=400)
                    return
                save_bid(vin, float(value) if value else None)
                self._send(b'{"ok":true}')
                return
            if urlparse(self.path).path == "/api/kbb":
                length = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(length) or b"{}")
                vin = str(data.get("vin", "")).strip().upper()
                value = str(data.get("usd", "")).replace("$", "").replace(",", "").strip()
                if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin) or (value and not value.replace(".", "", 1).isdigit()):
                    self._send(b'{"error":"need vin and number"}', status=400)
                    return
                save_kbb(vin, float(value) if value else None, str(data.get("miles", "")))
                self._send(b'{"ok":true}')
                return
            if urlparse(self.path).path != "/api/links":
                self._send(b'{"error":"not found"}', status=404)
                return
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(length) or b"{}")
            if data.get("q") and data.get("auction") and str(data.get("url", "")).startswith("https://"):
                save_link(data["q"], data["auction"], data["url"])
                self._send(b'{"ok":true}')
            else:
                self._send(b'{"error":"need q, auction, https url"}', status=400)

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lot_analyzer.app", description="«Одно окно»: одна машина — все аукционы.")
    parser.add_argument("--watch", action="append", default=[], help="папка с сохранёнными страницами (по умолчанию «Загрузки» и samples/)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--costs", default=str(DEFAULT_COSTS_PATH))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    folders = [Path(p).expanduser() for p in args.watch] or [Path.home() / "Downloads", Path("samples")]
    # Данные — в постоянной папке (~/LotAnalyzer/data); прежние из старых папок программы копируются сюда один раз.
    from . import paths
    for line in paths.migrate():
        print("перенесено в", paths.DATA_DIR, ":", line)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(folders, Path(args.costs), PageCache()))
    # Отправка Claude (если включена в окне): новые файлы из «Загрузок» — в ваш закрытый репозиторий GitHub.
    inbox.start(lambda: [p for p in find_pages(folders, 72) if p.parent != Path("samples")])
    address = f"http://127.0.0.1:{args.port}"
    print(f"Одно окно: {address}   (папки: {', '.join(map(str, folders))}; остановить — Ctrl+C)")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(address)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


PAGE = r"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Одно окно — аукционы</title>
<style>
:root{--bg:#f6f6f3;--fg:#1c1c1a;--muted:#62625c;--card:#fff;--line:#e2e2dc;--ok:#1f6f43;--okbg:#e3f1e8;--bad:#9b2c2c;--badbg:#f8e3e3;--mid:#8a6d00;--midbg:#fbf1d0;--acc:#2f5597}
@media (prefers-color-scheme:dark){:root{--bg:#151514;--fg:#ececea;--muted:#a2a29c;--card:#1f1f1d;--line:#34342f;--ok:#5cc28a;--okbg:#17301f;--bad:#f08b8b;--badbg:#3a1d1d;--mid:#e0c060;--midbg:#352c10;--acc:#7da2e8}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1400px;margin:0 auto;padding:20px 16px 40px}h1{font-size:22px;margin:0 0 4px}.muted{color:var(--muted);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:12px 0}
form{display:flex;flex-wrap:wrap;gap:10px;align-items:end}label{display:flex;flex-direction:column;font-size:12px;color:var(--muted);gap:4px}
input{font:inherit;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg);min-width:90px}
input#q{min-width:240px}button,a.btn{font:inherit;padding:8px 14px;border-radius:8px;border:1px solid var(--acc);background:var(--acc);color:#fff;cursor:pointer;text-decoration:none;display:inline-block}
a.btn.sec,button.btn.sec{background:transparent;color:var(--acc)}button.btn.bad{border-color:var(--bad);color:var(--bad)}.links{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{position:sticky;top:0;background:var(--card);font-weight:600}td.num{text-align:right;white-space:nowrap}.wrap{overflow-x:auto}
.v-ok{background:var(--okbg);color:var(--ok)}.v-bad{background:var(--badbg);color:var(--bad)}.v-mid{background:var(--midbg);color:var(--mid)}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600}details summary{cursor:pointer;color:var(--muted)}
input.kbb{width:80px;min-width:0;padding:4px 6px;text-align:right}
td.kbbcell,th.kbbcell{width:96px;max-width:96px;white-space:normal;overflow-wrap:anywhere}td.kbbcell .muted{font-size:11px;line-height:1.25}
.tabs{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:8px}button.tab{background:transparent;color:var(--fg);border:1px solid var(--line);font-weight:600}
button.tab.on{background:var(--acc);border-color:var(--acc);color:#fff}button.tab .cnt{font-weight:400;opacity:.8;margin-left:4px}.tabnote{flex-basis:100%}#places button.tab{font-weight:500;padding:5px 10px;font-size:13px}label.pickl{flex-direction:row;align-items:center;gap:6px;margin-left:8px;font-size:13px;color:var(--fg)}label.pickl input{min-width:auto;width:18px;height:18px}a.jump{margin-left:10px;padding:4px 12px;font-size:13px}th.w110{min-width:112px}
div.seen{font-size:11px;line-height:1.3;color:var(--mid);text-align:left;white-space:normal;min-width:110px;margin-top:2px}
img.thumb{width:112px;height:84px;object-fit:cover;border-radius:6px;display:block}
div.result{margin-top:6px;min-width:150px}details.calc{text-align:left;font-weight:400;margin-top:4px}details.calc table{font-size:12px;min-width:300px;margin-top:4px}
details.calc td{padding:2px 4px;border-bottom:1px dotted var(--line);white-space:normal}details.calc td.num{white-space:nowrap}details.calc tr.total td{font-weight:700;border-bottom:none}.neg{color:var(--bad)}.pos{color:var(--ok)}
</style></head><body><main>
<h1 id="top">Одно окно <a class="btn sec jump" href="/analytics" target="_blank" title="Своя база: KBB, итоги торгов, за сколько уходят машины на каждой площадке">📊 Наша аналитика</a> <a class="btn sec jump" href="/install" target="_blank" title="Установить или обновить закладку «💾 Сохранить для анализа» и расширение">🔖 Закладка</a> <button type="button" class="btn sec jump" id="inboxbtn" onclick="$('inboxbox').hidden=!$('inboxbox').hidden">📤 Отправка Claude</button> <span id="extstate" class="pill v-mid" title="Расширение «Lot Analyzer KBB»: ссылки и KBB открываются в фоне, вы остаётесь здесь">расширение: проверяю…</span></h1><div id="inboxbox" class="card" hidden><b>Отправка Claude</b> — каждый файл «Сохранить для анализа» сам уходит в ваш <b>закрытый</b> репозиторий GitHub, Claude читает его оттуда.
<div class="muted">В открытый (Public) репозиторий программа не отправляет никогда. Токен хранится только на этом компьютере (~/LotAnalyzer/data/inbox.json).</div>
<form onsubmit="event.preventDefault();saveInbox()" style="margin-top:8px"><label>Репозиторий<input id="inrepo" placeholder="acctradeteam-hub/autodealer-inbox"></label>
<label>Токен GitHub<input id="intoken" type="password" placeholder="github_pat_… (пусто — оставить прежний)"></label>
<label>Включено<input id="inon" type="checkbox" style="min-width:auto;width:20px;height:20px"></label><button>Сохранить</button></form>
<div id="instate" class="muted" style="margin-top:6px">—</div></div>
<div class="muted">Одна машина — все аукционы. Файлы из закладки «💾 Сохранить для анализа» подхватываются сами.</div>
<div class="card"><form id="f" onsubmit="event.preventDefault();refresh();loadLinks()">
<label>Машина<input id="q" placeholder="Honda Civic" autofocus></label>
<label>Год от<input id="y1" inputmode="numeric" placeholder="2013"></label><label>до<input id="y2" inputmode="numeric" placeholder="2016"></label>
<label>Пробег до, миль<input id="miles" inputmode="numeric" placeholder="150000"></label>
<label>KBB PP 92620 Good, $ (если у лота нет)<input id="kbb" inputmode="numeric" placeholder="10000"></label>
<label>Потолок до, $<input id="maxbid" inputmode="numeric" value="15000" title="Машины с потолком выше — не показывать. Пусто — без ограничения"></label>
<label>Без фото<input id="nophoto" type="checkbox" style="min-width:auto;width:20px;height:20px"></label>
<label title="Раскрыть у всех машин, из каких сумм сложилась прибыль при покупке по средней цене">Расчёт прибыли<input id="showcalc" type="checkbox" style="min-width:auto;width:20px;height:20px" onchange="try{localStorage.setItem('showcalc',this.checked?'1':'')}catch(e){};refresh()"></label>
<label>Файлы за, часов<input id="hours" inputmode="numeric" value="24"></label>
<button>Показать</button>
<button type="button" onclick="kbbTop()" title="Откроет kbb.com и сам получит KBB Private Party для лучших машин без KBB во всех вкладках: 15 популярных, по 5 электромобилей, пикапов и остальных">KBB: 15 + 5 + 5 + 5 лучших</button>
<button type="button" onclick="window.open('/inspection?'+params(),'_blank')" title="Все лоты «Major … Defect» и без фото — одним списком для поездки на аукцион">Список на осмотр</button></form>
<div class="links" id="links"></div>
<details style="margin-top:8px"><summary>Свой сохранённый поиск для этой машины</summary>
<form onsubmit="event.preventDefault();saveLink()" style="margin-top:8px"><label>Аукцион<input id="la" placeholder="ACV"></label>
<label>Ссылка из адресной строки<input id="lu" style="min-width:420px" placeholder="https://app.acvauctions.com/marketplace?..."></label><button>Запомнить</button></form></details>
</div>
<div class="card"><div id="places" class="tabs"><input id="picked" type="checkbox" hidden></div><div id="tabs" class="tabs"></div><div id="stat" class="muted">—</div><div class="wrap"><table><thead><tr>
<th>Фото</th><th>Машина</th><th>Пробег</th><th>CR</th><th title="Ваша ставка (proxy bid): впишите — сохранится, попадёт в заметку на CarMax («MP …»), прибыль при ней — под полем. Ниже — текущая ставка на сайте">Наша ставка</th><th class="kbbcell">KBB</th><th class="w110" title="Средняя цена покупки на аукционе: за сколько такая машина обычно уходит на этом аукционе (медиана по итогам торгов)">Средняя цена покупки<br>на аукционе</th><th class="w110">Прибыль при покупке<br>по средней цене</th><th>Потолок</th><th>Вердикт</th><th>Торги / итог</th></tr></thead>
<tbody id="rows"></tbody></table></div></div>
<div class="card muted" id="files"></div>
</main><script>
const $=id=>document.getElementById(id);const money=v=>v?('$'+Number(v).toLocaleString('en-US')):'—';
const signed=v=>(v===''||v==null)?'—':(Number(v)<0?'−$':'$')+Math.abs(Number(v)).toLocaleString('en-US');
/* Из чего прибыль по рынку: каждая статья с суммой — продажа, ремонт, детейлинг, сборы, покупка по рынку… */
function resultsSummary(st){const k=Object.keys(st||{});if(!k.length)return '';
 return '<div><b>История итогов торгов</b> (~/LotAnalyzer/data/auction_results.csv): '+k.map(a=>`${esc(a)} — ${st[a].n} продаж с MMR, цена ÷ MMR ${st[a].median}`+(st[a].enough?' (рынок этой площадки считается по ней)':' (мало для своего рынка, нужно от 20)')).join('; ')+'</div>'}
function profitItems(x){if(!x.calc_profit_items)return '';let items=[];try{items=JSON.parse(x.calc_profit_items)}catch(e){return ''}
 const rows=items.map(([label,v])=>`<tr><td>${esc(label)}</td><td class="num ${v<0?'neg':''}">${signed(v)}</td></tr>`).join('');
 return `<details class="calc"${$('showcalc').checked?' open':''}><summary>из чего</summary><table>${rows}<tr class="total"><td>Прибыль при покупке по средней цене</td><td class="num">${signed(x.calc_profit_market_usd)}</td></tr></table></details>`}
/* Итог торгов: за сколько продана и как это соотносится с нашим потолком */
function resultCell(x){if(!x.auction_result)return '';const p=Number(x.auction_result_price),c=Number(x.calc_max_bid_usd);
 const vs=p&&c?(c>=p?`<div class="pos">потолок ${money(c)} — выиграли бы</div>`:`<div class="neg">потолок ${money(c)} — ниже на ${money(p-c)}</div>`):'';
 return `<div class="result"><b>${esc(x.auction_result.split(' · ')[0])}</b><div class="muted">${esc(x.auction_result.split(' · ').slice(1).join(' · '))}</div>${vs}</div>`}
/* CR grade Manheim (0–5): 4+ хорошо, 3–4 средне, ниже 3 — много вложений */
const crCls=g=>{const n=parseFloat(g);return isNaN(n)?'':n>=4?'v-ok':n>=3?'v-mid':'v-bad'};
let place='';try{place=localStorage.getItem('la-place')||''}catch(e){}
try{$('picked').checked=localStorage.getItem('la-picked')==='1'}catch(e){}
function setPlace(p){place=p;try{localStorage.setItem('la-place',p)}catch(e){}refresh()}
function setPicked(on){try{localStorage.setItem('la-picked',on?'1':'')}catch(e){}refresh()}
const params=()=>new URLSearchParams({place,picked:$('picked').checked?'1':'',q:$('q').value,y1:$('y1').value,y2:$('y2').value,miles:$('miles').value,kbb:$('kbb').value,hours:$('hours').value,maxbid:$('maxbid').value,nophoto:$('nophoto').checked?'1':''});
/* «РИСК: рама; МОЖНО до $X; …» — в плашке и риск, и что вышло по расчёту. */
const vParts=v=>String(v||'').split(';'),vN=v=>String(v||'').startsWith('РИСК')?2:1;
function vHead(v){return vParts(v).slice(0,vN(v)).map(x=>x.trim()).join(' · ')}
function vRest(v){return vParts(v).slice(vN(v)).join(';')}
function cls(v){if(v.startsWith('РИСК'))return 'v-mid';return v.startsWith('МОЖНО')?(v.includes('вряд ли')?'v-mid':'v-ok'):(v.startsWith('ПРОПУСТИТЬ')||v.startsWith('НЕВЫГОДНО')||v.startsWith('ДОРОЖЕ'))?'v-bad':'v-mid'}
function kbbShort(s){s=String(s||'').replace(/^страница KBB: /,'');return s.length>34?s.slice(0,32)+'…':s}
function esc(s){return String(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
/* Показываем только ответ на последний запрос: опоздавший старый (без нового фильтра) не затирает таблицу. */
let refreshNo=0;
/* Вкладки: в каждой — только свои машины, сверху самые выгодные. Выбранная вкладка запоминается. */
const TABS=[['popular','★ Популярные','Corolla, Civic, Camry, Accord, Mazda3, CR-V, RAV4, CX-5, Prius, Camry / CR-V / RAV4 Hybrid, Lexus CT 200h / RX / IS / ES, Model 3 2022 SR'],
 ['ev','⚡ Электромобили','без смог-теста'],['truck','🛻 Пикапы','F-150, Silverado, Sierra, Ram, Tacoma, Tundra, Colorado, Frontier, Ranger, Ridgeline, Gladiator, Maverick …'],['other','Остальные','все прочие марки и модели']];
let tab='popular',lastData=null;try{tab=localStorage.getItem('la-tab')||'popular'}catch(e){}
function setTab(g){tab=g;try{localStorage.setItem('la-tab',g)}catch(e){}if(lastData)render(lastData);window.scrollTo({top:$('tabs').getBoundingClientRect().top+window.scrollY-10})}
async function refresh(){const my=++refreshNo;try{const r=await fetch('/api/rows?'+params());const d=await r.json();if(my!==refreshNo)return;lastData=d;render(d)}catch(e){$('stat').textContent='Нет связи с программой: '+e}}
function render(d){shown=d.rows;const gt=d.group_total||{};
if(place&&!(d.places||[]).some(([p])=>p===place)&&(d.places||[]).length){place='';try{localStorage.setItem('la-place','')}catch(e){};refresh();return}
$('places').innerHTML=`<span class="muted">Площадка:</span> <button type="button" class="tab${place?'':' on'}" onclick="setPlace('')">Все <span class="cnt">${(d.places||[]).reduce((a,[,n])=>a+n,0)}</span></button>`+
 (d.places||[]).map(([p,n])=>`<button type="button" class="tab${p===place?' on':''}" onclick="setPlace(this.dataset.p)" data-p="${esc(p)}">${esc(p)} <span class="cnt">${n}</span></button>`).join('')+
 `<label class="pickl" title="Только машины, которые вы отобрали: с KBB (вы его смотрели) или с вашей ставкой"><input id="picked" type="checkbox" onchange="setPicked(this.checked)"${$('picked')&&$('picked').checked?' checked':''}> только отобранные (KBB или ставка)</label>`;
if(!(gt[tab]>0)){const any=TABS.find(([g])=>gt[g]>0);if(any)tab=any[0]}
$('tabs').innerHTML=TABS.map(([g,t])=>`<button type="button" class="tab${g===tab?' on':''}" onclick="setTab('${g}')">${t} <span class="cnt">${gt[g]||0}</span></button>`).join('')+
 `<div class="muted tabnote">${esc((TABS.find(([g])=>g===tab)||[])[2]||'')} · сверху самые выгодные</div>`;
$('rows').innerHTML=d.rows.map((x,i)=>x.group!==tab?'':`<tr><td>${x.photo_main_url?`<a href="${esc(x.lot_url||x.photo_main_url)}" target="_blank" rel="noopener" title="Открыть лот: все фото, CR, ставка"><img class="thumb" src="${esc(x.photo_main_url)}" loading="lazy" alt=""></a>`:(x.no_photos?'<span class="pill v-mid">нет фото</span>':'')}</td>
<td>${esc([x.year,x.make,x.model,x.trim].join(' '))}${x.exterior_color?` <span class="muted">· ${esc(x.exterior_color)}</span>`:''}${x.inspect?` <span class="pill v-mid">осмотр: ${esc(x.inspect)}</span>`:''}
<div class="muted">${esc(x.vin)} · ${esc(x.auction)} ${esc(x.location)}${x.lot_number?' · лот '+esc(x.lot_number):''}</div>${x.lot_url?`<div><a href="${esc(x.lot_url)}" target="_blank" rel="noopener" title="Открыть эту машину на аукционе: все фото, Condition Report, ставка">Открыть лот ↗</a></div>`:''}</td>
<td class="num">${x.odometer_miles?Number(x.odometer_miles).toLocaleString('en-US'):'—'}</td>
<td class="num">${x.condition_grade?`<span class="pill ${crCls(x.condition_grade)}">${esc(x.condition_grade.split(' ')[0])}</span>`:(x.seen_before?'':'—')}${x.seen_before?`<div class="seen" title="Эта машина (VIN) уже была на торгах — по вашей базе итогов">${esc(x.seen_before).replace(/; /g,'<br>')}</div>`:''}${x.cr_url?`<div><a class="muted" href="${esc(x.cr_url)}" target="_blank" rel="noopener" title="Condition Report на Manheim: повреждения, фото дефектов, шины">CR ↗</a></div>`:''}</td>
<td class="num kbbcell">${x.vin?`<input class="kbb bid" data-vin="${esc(x.vin)}" value="${esc(x.my_proxy_usd)}" placeholder="ставка" inputmode="numeric" title="Ваша ставка (proxy bid). Enter — сохранить и пересчитать" onchange="saveBid(this)">`:money(x.my_proxy_usd)}${x.profit_at_my_bid_usd?`<div class="muted">прибыль <b class="${Number(x.profit_at_my_bid_usd)<0?'neg':'pos'}">${signed(x.profit_at_my_bid_usd)}</b></div>`:''}${x.current_bid_usd?`<div class="muted">на сайте ${money(x.current_bid_usd)}</div>`:''}${x.auction==='CarMax'&&x.my_proxy_usd&&/vehicledetail\/\d+/.test(x.lot_url||'')?`<div><a class="fg" href="${esc(x.lot_url)}#la-bid=${esc(String(x.my_proxy_usd).replace(/\D/g,''))}" target="_blank" rel="noopener" title="Откроет лот на CarMax и впишет ставку в «Set early bid» (вниз до шага $50). Способ оплаты и «Place bid» — вы сами">поставить на CarMax ↗</a></div>`:''}</td><td class="num kbbcell">${x.vin?`<input class="kbb" data-vin="${esc(x.vin)}" data-miles="${esc(x.odometer_miles)}" data-year="${esc(x.year)}" data-make="${esc(x.make)}" data-model="${esc(x.model)}" data-trim="${esc(x.trim)}" value="${esc(x.kbb_private_party_usd)}" placeholder="KBB PP" inputmode="numeric" title="KBB Private Party, 92620, Good — из приложения. Enter — пересчитать" onchange="saveKbb(this)">`:money(x.kbb_private_party_usd)}
<div class="muted" title="${esc(x.kbb_entered)}">${x.kbb_url?`<a class="muted" href="${esc(x.kbb_url)}" target="_blank" rel="noopener" title="Открыть страницу KBB, откуда взята цена: ${esc(x.kbb_entered)}">${esc(kbbShort(x.kbb_entered))} ↗</a>`:esc(kbbShort(x.kbb_entered))}</div>${(x.vin||x.lot_number)&&x.year&&x.make&&x.odometer_miles?`<div><a class="muted" href="${esc(laUrl([x]))}" onclick="openKbb([shown[${i}]]);return false" title="Откроется kbb.com и сам получит KBB (расширение «Lot Analyzer KBB»; без него — нажмите там закладку «💾 Сохранить для анализа»): комплектация, пробег ${esc(x.odometer_miles)}, 92620, Private Party, Good">${x.kbb_private_party_usd?'обновить ↗':'получить KBB ↗'}</a></div>`:''}</td>

<td class="num" title="Средняя цена покупки на аукционе: за сколько такая машина обычно уходит (Manheim — от MMR, CarMax — от KBB; медиана по итогам торгов)">${money(x.market_estimate_usd)}</td>
<td class="num" title="Прибыль, если купить по средней цене покупки на аукционе: продажа − расходы − (средняя цена покупки + сборы аукциона)"><b class="${Number(x.calc_profit_market_usd)<0?'neg':'pos'}">${signed(x.calc_profit_market_usd)}</b>${profitItems(x)}</td>
<td class="num" title="Максимальная ставка, при которой остаётся ваша цель прибыли"><b>${money(x.calc_max_bid_usd)}</b>${x.calc_profit_usd?`<div class="muted">прибыль ${money(x.calc_profit_usd)}</div>`:''}</td>
<td><span class="pill ${cls(x.calc_verdict||'')}">${esc(vHead(x.calc_verdict))}</span><div class="muted">${esc(vRest(x.calc_verdict))}</div>
<details><summary>расчёт</summary><div class="muted">${esc(x.calc_breakdown)}<br>${esc(x.needs_review)}</div></details></td><td class="muted">${esc(x.sale_date)}${resultCell(x)}</td></tr>`).join('')||`<tr><td colspan="11" class="muted">${d.rows.length?'В этой вкладке машин нет.':'Пока пусто: откройте поиск на аукционах и нажмите закладку на каждой вкладке.'}</td></tr>`;
const n=d.total,ok=d.rows.filter(x=>(x.calc_verdict||'').startsWith('МОЖНО')).length;
const inTab=d.rows.filter(x=>x.group===tab).length;
$('stat').textContent=`Всего машин: ${n} · в этой вкладке: ${gt[tab]||0}`+((gt[tab]||0)>inTab?` (показаны лучшие ${inTab})`:'')+` · «МОЖНО» во всех: ${ok} · сверху — больше прибыль при покупке по средней цене · обновлено ${new Date().toLocaleTimeString()}`;
$('files').innerHTML=resultsSummary(d.results_stats)+'Файлы ('+esc(d.folders.join(', '))+'): '+(d.files.map(f=>esc(f.time+' '+f.name)+(f.kbb?'':` <b class="${f.cars?'':'neg'}">(машин: ${f.cars||0})</b>`)+(f.old?` <span class="neg">⚠ ${esc(f.old)}</span>`:'')+(f.kbb?`<div class="${f.kbb.includes('⚠')?'v-bad':''}">${esc(f.kbb)}</div>`:'')).join(' · ')||'нет');}
async function saveBid(el){const v=el.value.replace(/[$,\s]/g,'');el.disabled=true;
try{await fetch('/api/bid',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({vin:el.dataset.vin,usd:v})});await refresh()}catch(e){alert('Не сохранилось: '+e)}}
async function saveKbb(el){const v=el.value.replace(/[$,\s]/g,'');el.disabled=true;
try{await fetch('/api/kbb',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({vin:el.dataset.vin,usd:v,miles:el.dataset.miles})});await refresh()}catch(e){alert('Не сохранилось: '+e)}}
let shown=[];
/* Адрес kbb.com для закладки-автопилота: страница модели первой машины + машины лотов в #la=… */
function laUrl(list){const cars=list.map(x=>({v:x.vin,y:x.year,mk:x.make,md:x.model,t:x.trim,mi:String(x.odometer_miles).replace(/\D/g,'')}));
 return list[0].kbb_open.split('#')[0]+'#la='+encodeURIComponent(JSON.stringify(cars))}
function kbbTop(){const need=x=>(x.vin||x.lot_number)&&x.year&&x.make&&x.odometer_miles&&!x.kbb_private_party_usd&&!(x.calc_verdict||'').startsWith('ПРОПУСТИТЬ');
/* Лучшие без KBB: 15 популярных, по 5 электромобилей, пикапов и остальных. */
const list=[['popular',15],['ev',5],['truck',5],['other',5]].flatMap(([g,n])=>shown.filter(x=>x.group===g&&need(x)).slice(0,n));
if(!list.length){alert('У машин на экране KBB уже есть');return}
openKbb(list);
$('stat').textContent=`KBB для ${list.length} машин: на вкладке kbb.com всё идёт само (с расширением «Lot Analyzer KBB»; без него — нажмите там закладку «💾 Сохранить для анализа»). Около 5 секунд на машину, цены появятся здесь сами. Если Chrome спросит «Разрешить скачивание нескольких файлов» — разрешите.`}
/* Машины передаются и в адресе (#la=…), и в имени вкладки — на случай, если kbb.com при переадресации потеряет хвост адреса. */
/* С расширением «Lot Analyzer KBB» ссылки открываются соседней вкладкой в фоне — вы остаётесь в окне программы. */
const hasExt=()=>document.documentElement.dataset.laExt==='1';
function extState(){const e=$('extstate');if(!e)return;if(hasExt()){e.className='pill v-ok';e.textContent='расширение подключено — ссылки и KBB в фоне'}
 else{e.className='pill v-bad';e.textContent='расширение не подключено — вкладки откроются поверх: обновите его (⟳ на chrome://extensions) и эту страницу'}}
window.addEventListener('la-ext',extState);setTimeout(extState,800);
function showInbox(st){$('inboxbtn').textContent='📤 Отправка Claude: '+(st.enabled&&st.status==='включена'?'вкл ✓ ('+st.sent+')':st.enabled?st.status:'выкл');
 $('inboxbtn').className='btn sec jump'+(st.error?' bad':'');if(!$('inrepo').value)$('inrepo').value=st.repo||'';$('inon').checked=!!st.enabled;
 $('instate').textContent=(st.enabled?'Включено':'Выключено')+(st.repo?' · '+st.repo:'')+(st.has_token?' · токен сохранён':' · токена нет')+' · отправлено файлов: '+(st.sent||0)+(st.error?' · ⚠ '+st.error:'')}
async function loadInbox(){try{showInbox(await (await fetch('/api/inbox')).json())}catch(e){}}
async function saveInbox(){const r=await fetch('/api/inbox',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({repo:$('inrepo').value,token:$('intoken').value,enabled:$('inon').checked})});
 const d=await r.json();if(d.error){alert(d.error);return}$('intoken').value='';showInbox(d)}
loadInbox();setInterval(loadInbox,15000);
function openBg(url,cars){if(!hasExt())return false;window.postMessage({source:'lot-analyzer',type:'open-bg',url,cars:cars||''},'*');return true}
/* «поставить на CarMax» (a.fg) открывается обычно — там вы сами нажимаете «Place bid». */
document.addEventListener('click',e=>{const a=e.target.closest&&e.target.closest('a[target="_blank"]:not(.fg)');
 if(!a||e.metaKey||e.ctrlKey||e.shiftKey||e.button!==0||!/^https:/.test(a.href)||!hasExt())return;e.preventDefault();openBg(a.href)},true);
function openKbb(list){const url=laUrl(list),cars=url.split('#la=')[1];if(openBg(url,cars))return;window.open(url,'la='+cars)}
async function loadLinks(){const r=await fetch('/api/links?'+params());const d=await r.json();
$('links').innerHTML=d.map(x=>`<a class="btn ${x.kind.startsWith('страница')?'sec':''}" href="${esc(x.url)}" target="_blank" rel="noopener" title="${esc(x.kind)}">${esc(x.auction)} ↗</a>`).join('')+
'<span class="muted" style="align-self:center">затем на каждой вкладке — закладка «💾 Сохранить для анализа»</span>'}
async function saveLink(){await fetch('/api/links',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({q:$('q').value,auction:$('la').value,url:$('lu').value})});loadLinks()}
let seenFiles='';
async function watchFiles(){try{const r=await fetch('/api/files?hours='+encodeURIComponent($('hours').value));const t=await r.text();if(t!==seenFiles){seenFiles=t;refresh()}}catch(e){}}
try{$('showcalc').checked=localStorage.getItem('showcalc')==='1'}catch(e){}
loadLinks();watchFiles();setInterval(watchFiles,4000);
</script></body></html>"""


if __name__ == "__main__":
    raise SystemExit(main())
