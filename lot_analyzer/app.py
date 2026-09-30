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

from .bid import DEFAULT_COSTS_PATH, apply_to_rows, load_costs
from .inspection import render_html
from . import kbb_page, manheim_csv
from .pages import read_page
from .parsers import parse_page

SEARCH_URLS_PATH = Path("config/search_urls.json")
LINKS_PATH = Path("data/search_links.json")
KBB_PATH = Path("data/kbb_values.json")            # KBB PP из приложения, вписанный в окне: {VIN: {"usd": …, "miles": …, "date": …}}
SAVED_BY_BOOKMARKLET = re.compile(r"^(CarMax|ACV|Manheim|ADESA|KBB|auction)_.+\.html?$", re.I)
SAVED_KBB = re.compile(r"kelley[\s_-]*blue[\s_-]*book.*\.(html?|mhtml?)$", re.I)     # страница KBB, сохранённая через Cmd+S
SHOW_ROWS = 300                              # в окне — лучшие 300, иначе браузер тормозит на тысячах машин
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
            if path.suffix.lower() == ".csv":
                rows = manheim_csv.read_export(path)
            else:
                html = read_page(path)
                if kbb_page.is_kbb(html):
                    record, rows = kbb_page.parse(html), []
                    if record:
                        record["file"] = path.name
                else:
                    rows = parse_page(html, source_name=path.name)
        except Exception as error:  # битый файл не должен ронять окно
            record = None
            rows = [{"auction": "?", "source_file": path.name, "needs_review": f"не разобрано: {error}"}]
        with self._lock:
            self._cache[path] = (mtime, rows)
            self._kbb[path] = record
        return [dict(r) for r in rows]


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
            if SAVED_BY_BOOKMARKLET.match(path.name) or SAVED_KBB.search(path.name) or manheim_csv.is_export(path):
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


def search_rows(pages: list[Path], cache: PageCache, costs: dict, query: str = "", year_from: int | None = None,
                year_to: int | None = None, max_miles: int | None = None, kbb: float | None = None,
                only_no_photos: bool = False, max_bid: float | None = None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    saved_kbb = load_kbb()
    kbb_cfg = costs.get("kbb_page") or {}
    condition = kbb_cfg.get("condition", "good")
    kbb_pages = [r for r in (cache.kbb(p) for p in pages) if r and r["miles"]]   # без пробега — не подставляем
    for path in pages:                       # новые файлы первыми: дубликаты берутся из свежего
        for row in cache.rows(path):
            key = row.get("vin") or f"{row.get('auction')}:{row.get('lot_number')}"
            if key in seen or not matches(row, query, year_from, year_to, max_miles):
                continue
            if only_no_photos and row.get("no_photos") != "да":
                continue
            seen.add(key)
            own = saved_kbb.get(row.get("vin", ""))
            if own:                          # вписан в окне по этому VIN — главнее всего
                row["kbb_private_party_usd"] = f"{float(own['usd']):.0f}"
                row["kbb_entered"] = "вручную " + own.get("date", "")
            elif not row.get("kbb_private_party_usd"):
                page = next((r for r in kbb_pages if kbb_page.matches(r, row, int(kbb_cfg.get("max_miles_gap", 3000)))), None)
                if page and page["private_party"].get(condition):
                    row["kbb_private_party_usd"] = str(page["private_party"][condition])
                    row["kbb_entered"] = f"страница KBB: {kbb_page.describe(page)}"
                    if page["zip"] != str(kbb_cfg.get("zip", "92620")):
                        row["needs_review"] = "; ".join(x for x in (row.get("needs_review", ""), f"KBB для ZIP {page['zip']}, не {kbb_cfg.get('zip', '92620')}") if x)
            if kbb and not (row.get("kbb_private_party_usd") or row.get("retail_estimate_usd")):
                row["kbb_private_party_usd"] = f"{kbb:.0f}"
                row["needs_review"] = "; ".join(x for x in (row.get("needs_review", ""), "KBB — из окна поиска, одинаковый для всех") if x)
            rows.append(row)
    apply_to_rows(rows, costs)
    if max_bid:                              # дороже своего бюджета — не показываем
        rows = [r for r in rows if float(r.get("calc_max_bid_usd") or 0) <= max_bid]
    rows.sort(key=lambda r: (RANK.get(r.get("calc_verdict", "").split(" ")[0].rstrip(":"), 5), -expected_gain(r)))
    return rows


def load_kbb() -> dict[str, dict]:
    try:
        return json.loads(KBB_PATH.read_text(encoding="utf-8")) if KBB_PATH.exists() else {}
    except (OSError, ValueError):
        return {}


def save_kbb(vin: str, usd: float | None, miles: str = "") -> None:
    """Запоминает KBB по VIN; пустое значение — стереть."""
    values = load_kbb()
    if usd:
        values[vin] = {"usd": round(usd), "miles": miles, "date": time.strftime("%Y-%m-%d")}
    else:
        values.pop(vin, None)
    KBB_PATH.parent.mkdir(parents=True, exist_ok=True)
    KBB_PATH.write_text(json.dumps(values, ensure_ascii=False, indent=1), encoding="utf-8")


def kbb_link(row: dict[str, str]) -> str:
    """Страница модели на kbb.com: дальше выбрать комплектацию, пробег, 92620, Good, Private Party."""
    slug = lambda s: re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    if row.get("make") and row.get("model") and row.get("year"):
        return f"https://www.kbb.com/{slug(row['make'])}/{slug(row['model'])}/{row['year']}/"
    return "https://www.kbb.com/whats-my-car-worth/"


def expected_gain(row: dict[str, str]) -> float:
    """Чего ждать от лота: прибыль при потолке × шанс выиграть (без шанса — осторожно, 30%)."""
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
            links.append({"auction": auction, "url": spec.get("home", ""), "kind": "страница поиска — впишите модель или сохраните свой поиск"})
    return links


def save_link(query: str, auction: str, url: str) -> None:
    own = json.loads(LINKS_PATH.read_text(encoding="utf-8")) if LINKS_PATH.exists() else {}
    own.setdefault(query.lower().strip(), {})[auction] = url
    LINKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LINKS_PATH.write_text(json.dumps(own, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- веб-сервер

COLUMNS = ("auction", "location", "lot_number", "year", "make", "model", "trim", "odometer_miles", "current_bid_usd",
           "kbb_private_party_usd", "kbb_estimate_usd", "kbb_estimate_source", "market_estimate_usd", "calc_max_bid_usd", "calc_profit_usd", "calc_win_chance_pct", "calc_verdict",
           "sale_date", "lot_url", "vin", "no_photos", "inspect", "calc_max_bid_if_defect_usd", "defects", "source_file", "calc_breakdown", "needs_review")


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
                total = len(rows)
                rows = rows[:SHOW_ROWS]
                files = []
                for p in pages:
                    item = {"name": p.name, "time": time.strftime("%H:%M", time.localtime(p.stat().st_mtime))}
                    record = cache.kbb(p)
                    if record:
                        item["kbb"] = (f"KBB {record['year']} {record['make']} {record['model']} {kbb_page.describe(record)}: PP Good ${record['private_party'].get('good', 0):,}"
                                       + ("" if record["miles"] else " — ⚠ пробег не указан на KBB, не подставлено: введите пробег на kbb.com и сохраните снова"))
                    files.append(item)
                payload = {"rows": [{**{k: r.get(k, "") for k in COLUMNS}, "kbb_link": kbb_link(r), "kbb_entered": r.get("kbb_entered", "")} for r in rows], "files": files, "total": total,
                           "folders": [str(f) for f in folders]}
                self._send(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            elif url.path == "/inspection":
                pages = find_pages(folders, float(q.get("hours") or 24))
                rows = search_rows(pages, cache, load_costs(costs_path), q.get("q", ""), num("y1"), num("y2"), num("miles"),
                                   float(q["kbb"]) if q.get("kbb", "").replace(".", "").isdigit() else None, max_bid=num("maxbid"))
                title = "На осмотр" + (f" — {q['q']}" if q.get("q") else " — все машины из файлов")
                self._send(render_html(rows, title).encode("utf-8"), "text/html; charset=utf-8")
            elif url.path == "/api/files":         # дёшево: только список файлов — окно пересчитывает таблицу, если он изменился
                pages = find_pages(folders, float(q.get("hours") or 24))
                self._send(json.dumps([f"{p.name}:{p.stat().st_mtime:.0f}" for p in pages]).encode("utf-8"))
            elif url.path == "/api/links":
                self._send(json.dumps(search_links(q.get("q", ""), num("y1"), num("y2")), ensure_ascii=False).encode("utf-8"))
            else:
                self._send(b'{"error":"not found"}', status=404)

        def do_POST(self) -> None:
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
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(folders, Path(args.costs), PageCache()))
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
a.btn.sec{background:transparent;color:var(--acc)}.links{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{position:sticky;top:0;background:var(--card);font-weight:600}td.num{text-align:right;white-space:nowrap}.wrap{overflow-x:auto}
.v-ok{background:var(--okbg);color:var(--ok)}.v-bad{background:var(--badbg);color:var(--bad)}.v-mid{background:var(--midbg);color:var(--mid)}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600}details summary{cursor:pointer;color:var(--muted)}
input.kbb{width:90px;min-width:0;padding:4px 6px;text-align:right}
</style></head><body><main>
<h1>Одно окно</h1><div class="muted">Одна машина — все аукционы. Файлы из закладки «💾 Сохранить для анализа» подхватываются сами.</div>
<div class="card"><form id="f" onsubmit="event.preventDefault();refresh();loadLinks()">
<label>Машина<input id="q" placeholder="Honda Civic" autofocus></label>
<label>Год от<input id="y1" inputmode="numeric" placeholder="2013"></label><label>до<input id="y2" inputmode="numeric" placeholder="2016"></label>
<label>Пробег до, миль<input id="miles" inputmode="numeric" placeholder="150000"></label>
<label>KBB PP 92620 Good, $ (если у лота нет)<input id="kbb" inputmode="numeric" placeholder="10000"></label>
<label>Потолок до, $<input id="maxbid" inputmode="numeric" value="15000" title="Машины с потолком выше — не показывать. Пусто — без ограничения"></label>
<label>Без фото<input id="nophoto" type="checkbox" style="min-width:auto;width:20px;height:20px"></label>
<label>Файлы за, часов<input id="hours" inputmode="numeric" value="24"></label>
<button>Показать</button>
<button type="button" onclick="window.open('/inspection?'+params(),'_blank')" title="Все лоты «Major … Defect» и без фото — одним списком для поездки на аукцион">Список на осмотр</button></form>
<div class="links" id="links"></div>
<details style="margin-top:8px"><summary>Свой сохранённый поиск для этой машины</summary>
<form onsubmit="event.preventDefault();saveLink()" style="margin-top:8px"><label>Аукцион<input id="la" placeholder="ACV"></label>
<label>Ссылка из адресной строки<input id="lu" style="min-width:420px" placeholder="https://app.acvauctions.com/marketplace?..."></label><button>Запомнить</button></form></details>
</div>
<div class="card"><div id="stat" class="muted">—</div><div class="wrap"><table><thead><tr>
<th>Аукцион</th><th>Лот</th><th>Машина</th><th>Пробег</th><th>Ставка</th><th>KBB</th><th>Рынок</th><th>Потолок</th><th>Прибыль</th><th>Вердикт</th><th>Торги</th></tr></thead>
<tbody id="rows"></tbody></table></div></div>
<div class="card muted" id="files"></div>
</main><script>
const $=id=>document.getElementById(id);const money=v=>v?('$'+Number(v).toLocaleString('en-US')):'—';
const params=()=>new URLSearchParams({q:$('q').value,y1:$('y1').value,y2:$('y2').value,miles:$('miles').value,kbb:$('kbb').value,hours:$('hours').value,maxbid:$('maxbid').value,nophoto:$('nophoto').checked?'1':''});
function cls(v){return v.startsWith('МОЖНО')?(v.includes('вряд ли')?'v-mid':'v-ok'):(v.startsWith('ПРОПУСТИТЬ')||v.startsWith('НЕВЫГОДНО')||v.startsWith('ДОРОЖЕ'))?'v-bad':'v-mid'}
function esc(s){return String(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
async function refresh(){try{const r=await fetch('/api/rows?'+params());const d=await r.json();
$('rows').innerHTML=d.rows.map(x=>`<tr><td>${esc(x.auction)}<div class="muted">${esc(x.location)}</div></td><td>${x.lot_url?`<a href="${esc(x.lot_url)}" target="_blank" rel="noopener">${esc(x.lot_number||'лот')}</a>`:esc(x.lot_number)}</td>
<td>${esc([x.year,x.make,x.model,x.trim].join(' '))}${x.inspect?` <span class="pill v-mid">осмотр: ${esc(x.inspect)}</span>`:''}<div class="muted">${esc(x.vin)}</div></td><td class="num">${x.odometer_miles?Number(x.odometer_miles).toLocaleString('en-US'):'—'}</td>
<td class="num">${money(x.current_bid_usd)}</td><td class="num">${x.vin?`<input class="kbb" data-vin="${esc(x.vin)}" data-miles="${esc(x.odometer_miles)}" value="${esc(x.kbb_private_party_usd)}" placeholder="KBB PP" inputmode="numeric" title="KBB Private Party, 92620, Good — из приложения. Enter — пересчитать" onchange="saveKbb(this)">`:money(x.kbb_private_party_usd)}
<div class="muted">${esc(x.kbb_entered)}</div><div><a class="muted" href="${esc(x.kbb_link)}" target="_blank" rel="noopener" title="Открыть kbb.com: выбрать комплектацию, пробег ${esc(x.odometer_miles)}, ZIP 92620, Good, Private Party">kbb.com ↗</a></div></td><td class="num">${money(x.market_estimate_usd)}</td>
<td class="num"><b>${money(x.calc_max_bid_usd)}</b></td><td class="num">${money(x.calc_profit_usd)}</td>
<td><span class="pill ${cls(x.calc_verdict||'')}">${esc((x.calc_verdict||'').split(';')[0])}</span><div class="muted">${esc((x.calc_verdict||'').split(';').slice(1).join(';'))}</div>
<details><summary>расчёт</summary><div class="muted">${esc(x.calc_breakdown)}<br>${esc(x.needs_review)}</div></details></td><td class="muted">${esc(x.sale_date)}</td></tr>`).join('')||'<tr><td colspan="11" class="muted">Пока пусто: откройте поиск на аукционах и нажмите закладку на каждой вкладке.</td></tr>';
const n=d.total,ok=d.rows.filter(x=>(x.calc_verdict||'').startsWith('МОЖНО')).length;
$('stat').textContent=`Машин: ${n}`+(n>d.rows.length?` (показаны лучшие ${d.rows.length})`:'')+` · «МОЖНО»: ${ok} · сверху — больше всего ожидаемой прибыли (прибыль × шанс) · обновлено ${new Date().toLocaleTimeString()}`;
$('files').innerHTML='Файлы ('+esc(d.folders.join(', '))+'): '+(d.files.map(f=>esc(f.time+' '+f.name)+(f.kbb?`<div class="${f.kbb.includes('⚠')?'v-bad':''}">${esc(f.kbb)}</div>`:'')).join(' · ')||'нет');}catch(e){$('stat').textContent='Нет связи с программой: '+e}}
async function saveKbb(el){const v=el.value.replace(/[$,\s]/g,'');el.disabled=true;
try{await fetch('/api/kbb',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({vin:el.dataset.vin,usd:v,miles:el.dataset.miles})});await refresh()}catch(e){alert('Не сохранилось: '+e)}}
async function loadLinks(){const r=await fetch('/api/links?'+params());const d=await r.json();
$('links').innerHTML=d.map(x=>`<a class="btn ${x.kind.startsWith('страница')?'sec':''}" href="${esc(x.url)}" target="_blank" rel="noopener" title="${esc(x.kind)}">${esc(x.auction)} ↗</a>`).join('')+
'<span class="muted" style="align-self:center">затем на каждой вкладке — закладка «💾 Сохранить для анализа»</span>'}
async function saveLink(){await fetch('/api/links',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({q:$('q').value,auction:$('la').value,url:$('lu').value})});loadLinks()}
let seenFiles='';
async function watchFiles(){try{const r=await fetch('/api/files?hours='+encodeURIComponent($('hours').value));const t=await r.text();if(t!==seenFiles){seenFiles=t;refresh()}}catch(e){}}
loadLinks();watchFiles();setInterval(watchFiles,4000);
</script></body></html>"""


if __name__ == "__main__":
    raise SystemExit(main())
