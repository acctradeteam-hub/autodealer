"""KBB Private Party прямо с kbb.com — ПРОБНЫЙ режим до официального API (IDWS).

Как работает (по рецепту browse.sh «KBB Get Vehicle Value»):
1. Страница модели https://www.kbb.com/{make}/{model}/{year}/ — список комплектаций
   (ссылки /{make}/{model}/{year}/{trim-style-slug}/), выбирается похожая на трим лота.
2. Страница комплектации ?intent=trade-in-sell&mileage=…&zipcode=… — в её встроенном
   JSON (__NEXT_DATA__) все значения: Private Party и Trade-In по состояниям (kbb_page.parse).

Неофициально: правила kbb.com запрещают массовый автоматический сбор. Поэтому только по
кнопке в окне, пауза между запросами, лимит в день, VIN запоминается на несколько дней,
при первой блокировке — стоп до завтра. Настройки — config/costs.json → "kbb_site".
Когда будет ключ официального API KBB, меняется только этот модуль.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import kbb_page
from .paths import DATA_DIR

BASE = "https://www.kbb.com"
CACHE_PATH = DATA_DIR / "kbb_site_cache.json"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
# Кузов по умолчанию, если у комплектации их несколько (седан для легковых, SUV, пикап).
STYLES = ("sedan", "sport-utility", "suv", "pickup", "crew-cab", "hatchback", "wagon", "minivan", "van", "coupe", "convertible")
STYLE_WORDS = {"sedan", "sport", "utility", "suv", "pickup", "truck", "hatchback", "coupe", "wagon", "van", "minivan",
               "convertible", "cab", "crew", "extended", "regular", "double", "quad", "super", "supercrew", "supercab"}
SKIP_SLUGS = {"cost-to-own", "specs", "consumer-reviews", "colors", "options", "styles", "cars-for-sale", "photos",
              "videos", "trade-in-value", "expert-review", "reviews", "safety", "cargo-space", "mpg"}

_lock = threading.Lock()
_last_request = [0.0]


def _ssl_context() -> ssl.SSLContext:
    """Python с python.org на Mac не видит системные сертификаты — берём их из certifi."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


class KbbSiteError(Exception):
    """Понятная причина, почему KBB не получен."""


slug = kbb_page.slug


def model_slugs(model: str) -> list[str]:
    """Как модель может называться в адресе kbb.com: «F-150» → f-150, f150; «Silverado 1500» → silverado-1500, silverado."""
    base = slug(model)
    options = [base, base.replace("-", "")]
    words = base.split("-")
    if len(words) > 1:
        options.append(words[0])
    return list(dict.fromkeys(o for o in options if o))


def settings(costs: dict) -> dict:
    cfg = {"enabled": False, "zip": "92620", "condition": "good", "min_interval_sec": 4,
           "daily_limit": 60, "cache_days": 7}
    cfg.update(costs.get("kbb_site") or {})
    return cfg


# ---------------------------------------------------------------- запоминание и лимиты

def _load() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
    except (OSError, ValueError):
        return {}


def _store(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def _http_get(url: str, cfg: dict, cache: dict) -> str:
    """Один запрос к kbb.com: пауза между запросами, дневной лимит, стоп при блокировке."""
    today = dt.date.today().isoformat()
    usage = cache.setdefault("_usage", {})
    if usage.get("blocked") == today:
        raise KbbSiteError("kbb.com сегодня уже ответил блокировкой — запросы остановлены до завтра, впишите KBB вручную")
    if usage.get(today, 0) >= int(cfg["daily_limit"]):
        raise KbbSiteError(f"дневной лимит {cfg['daily_limit']} запросов к kbb.com исчерпан (config/costs.json → kbb_site.daily_limit)")
    with _lock:
        wait = float(cfg["min_interval_sec"]) - (time.time() - _last_request[0])
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.time()
    usage[today] = usage.get(today, 0) + 1
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9",
                                                   "Accept": "text/html,application/xhtml+xml"})
    try:
        with urllib.request.urlopen(request, timeout=25, context=_ssl_context()) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise KbbSiteError("404") from error
        if error.code in (403, 429):
            usage["blocked"] = today
            raise KbbSiteError(f"kbb.com ответил {error.code} (блокировка) — запросы остановлены до завтра") from error
        raise KbbSiteError(f"kbb.com: ошибка {error.code}") from error
    except urllib.error.URLError as error:
        raise KbbSiteError(f"нет связи с kbb.com: {error.reason}") from error
    finally:
        _store(cache)
    if "__NEXT_DATA__" not in body:
        usage["blocked"] = today
        _store(cache)
        raise KbbSiteError("kbb.com показал проверку «вы не робот» — запросы остановлены до завтра")
    return body


# ---------------------------------------------------------------- комплектация

def trim_links(html: str, make_s: str, model_s: str, year: str) -> list[str]:
    """Комплектации со страницы модели: href="/honda/civic/2014/lx-sedan-4d/" → lx-sedan-4d."""
    pattern = (rf'(?:https://www\.kbb\.com)?/{re.escape(make_s)}/{re.escape(model_s)}/{re.escape(str(year))}/'
               rf'([a-z0-9]+(?:-[a-z0-9]+)*)/?(?=["?#\\])')
    return list(dict.fromkeys(s for s in re.findall(pattern, html) if s not in SKIP_SLUGS))


def _trim_part(s: str) -> str:
    """lx-sedan-4d → lx; outer-banks-sport-utility-4d → outer-banks."""
    parts = s.split("-")
    while parts and (parts[-1] in STYLE_WORDS or re.fullmatch(r"\d+d|\d+wd|awd|fwd|rwd", parts[-1])):
        parts.pop()
    return "-".join(parts)


def pick_trim(slugs: list[str], trim: str) -> tuple[str | None, list[str]]:
    """Комплектация KBB под трим лота. Если однозначно не выбрать — (None, варианты для человека)."""
    if not slugs:
        return None, []
    wanted = [w for w in slug(trim).replace("2-5i", "25i").replace("1-5t", "15t").replace("2-0t", "20t").split("-")
              if w and w not in ("base", "no", "trim", "w")]
    if not wanted and len({_trim_part(s) for s in slugs}) == 1:
        wanted = _trim_part(slugs[0]).split("-")

    def score(s: str) -> tuple[int, int, int]:
        trim_words = _trim_part(s).split("-")
        hits = sum(1 for w in wanted if w in trim_words)
        extra = sum(1 for w in trim_words if w and w not in wanted)
        style = next((i for i, st in enumerate(STYLES) if st in s), len(STYLES))
        return hits, -extra, -style

    ranked = sorted(slugs, key=score, reverse=True)
    best = score(ranked[0])
    if best[0] == 0:
        return None, ranked[:15]
    same = {_trim_part(s) for s in ranked if score(s)[:2] == best[:2]}
    if len(same) > 1:
        return None, ranked[:15]
    return ranked[0], ranked[:15]


def available(costs: dict) -> bool:
    """Можно ли сейчас спрашивать kbb.com программой (включено и сегодня не было блокировки)."""
    return bool(settings(costs)["enabled"]) and _load().get("_usage", {}).get("blocked") != dt.date.today().isoformat()


def browser_url(costs: dict, year: str, make: str, model: str, miles: str) -> str:
    """Страница модели на kbb.com для ВАШЕГО браузера — с пробегом лота и ZIP, в режиме «продажа».

    Выбираете комплектацию, нажимаете закладку «Сохранить для анализа» — окно само
    прочитает Private Party из сохранённой страницы (lot_analyzer/kbb_page.py).
    """
    cfg = settings(costs)
    miles_n = re.sub(r"\D", "", str(miles or ""))
    query = f"?intent=trade-in-sell&mileage={miles_n}&zipcode={cfg['zip']}" if miles_n else f"?intent=trade-in-sell&zipcode={cfg['zip']}"
    if year and make and model:
        return f"{BASE}/{slug(make)}/{model_slugs(model)[0]}/{year}/{query}"
    return f"{BASE}/whats-my-car-worth/"


# ---------------------------------------------------------------- главное

def lookup(costs: dict, vin: str, year: str, make: str, model: str, trim: str, miles: str,
           chosen: str = "", fetch=None) -> dict:
    """KBB для машины лота.

    Ответ: {"usd", "trim_slug", "miles", "zip", "url", "private_party", …}
    или {"candidates": ["model/trim-slug", …], "question": …}, если комплектацию надо выбрать.
    chosen — выбранный человеком вариант «model/trim-slug».
    """
    cfg = settings(costs)
    if not cfg["enabled"]:
        raise KbbSiteError("получение KBB с kbb.com выключено (config/costs.json → kbb_site.enabled)")
    if not (year and make and model):
        raise KbbSiteError("у лота нет года, марки или модели")
    miles_n = re.sub(r"\D", "", str(miles or ""))
    if not miles_n or miles_n == "0":
        raise KbbSiteError("у лота нет пробега — KBB без пробега неточный")
    cache = _load()
    key = f"{vin or slug(f'{year}-{make}-{model}-{trim}')}|{miles_n}"
    fresh = (dt.date.today() - dt.timedelta(days=int(cfg["cache_days"]))).isoformat()
    if not chosen and cache.get(key, {}).get("date", "") >= fresh:
        return {**cache[key], "cached": True}
    get = fetch or (lambda url: _http_get(url, cfg, cache))

    make_s = slug(make)
    if chosen:
        model_s, trim_slug = chosen.split("/", 1)
    else:
        model_s = trim_slug = ""
        candidates: list[str] = []
        for option in model_slugs(model):
            try:
                page = get(f"{BASE}/{make_s}/{option}/{year}/")
            except KbbSiteError as error:
                if str(error) == "404":
                    continue
                raise
            found = trim_links(page, make_s, option, year)
            if found:
                model_s = option
                picked, candidates = pick_trim(found, trim)
                trim_slug = picked or ""
                break
        if not model_s:
            raise KbbSiteError(f"на kbb.com не нашлась страница {year} {make} {model} — откройте «kbb.com ↗» и впишите KBB вручную")
        if not trim_slug:
            return {"candidates": [f"{model_s}/{c}" for c in candidates],
                    "question": f"Какая комплектация на KBB соответствует лоту «{year} {make} {model} {trim}»?"}

    url = f"{BASE}/{make_s}/{model_s}/{year}/{trim_slug}/?intent=trade-in-sell&mileage={miles_n}&zipcode={cfg['zip']}"
    record = kbb_page.parse(get(url))
    condition = cfg["condition"]
    if not record or not record["private_party"].get(condition):
        raise KbbSiteError("на странице KBB нет Private Party — KBB мог поменять сайт; впишите KBB вручную")
    if record["miles"] and abs(record["miles"] - int(miles_n)) > 100:
        raise KbbSiteError(f"KBB посчитал для {record['miles']} миль вместо {miles_n} — впишите KBB вручную")
    if not record["miles"]:
        raise KbbSiteError("KBB не учёл пробег (показал «типичный пробег») — впишите KBB вручную")
    result = {"usd": record["private_party"][condition], "condition": condition, "zip": record["zip"] or cfg["zip"],
              "miles": miles_n, "trim_slug": f"{model_s}/{trim_slug}", "url": url, "private_party": record["private_party"],
              "trade_in": record["trade_in"], "date": dt.date.today().isoformat()}
    cache[key] = result
    _store(cache)
    return result
