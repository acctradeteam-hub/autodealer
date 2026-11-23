"""Агенты сами — без копирования руками.

Muse (Facebook Marketplace): расширение Chrome раз в минуту спрашивает программу (/api/agent_next); есть задание —
открывает muse.ai во вкладке в фоне, вставляет текст, отправляет, ждёт ответ (таблицу CSV) и отдаёт его программе
(/api/agent_report); вкладка закрывается. Задание бывает:
  — по кнопке «🤖 Muse сам» в окне (общее — группы без свежих цен; или по машинам, отмеченным «🛒 Рынок»);
  — само раз в день (вкл. в окне: «Авто»), после часа hour, если есть группы без свежих цен; лимит агента бережём —
    те же правила, что у ручного задания (refresh_days, asked_wait_days, max_groups).
CarGurus (Claude в Chrome): чужое расширение программа нажимать не может, поэтому наоборот — ярлык (shortcut) Claude
в Chrome по расписанию открывает страницу программы /agent/cg: там задание и поле для ответа; Claude делает задание
на CarGurus, вставляет CSV в поле и нажимает «Load» — ответ сразу в программе.
Состояние — data/agent_auto.json.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Callable

from .paths import DATA_DIR

PATH = DATA_DIR / "agent_auto.json"
SOURCES = ("fb", "cg")
STALE_S = 45 * 60          # задание взято, а ответа нет 45 минут — считаем, что не вышло (вкладку закрыли, Mac уснул …)
DEFAULT_HOUR = 19          # «Авто»: раз в день после 19:00


def load() -> dict:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}
    except (OSError, ValueError):
        data = {}
    for s in SOURCES:
        st = data.setdefault(s, {})
        st.setdefault("auto", False)
        st.setdefault("hour", DEFAULT_HOUR)
        st.setdefault("last_auto_day", "")
        st.setdefault("job", None)
        st.setdefault("last", None)
    return data


def _save(data: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_name(PATH.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PATH)


def _src(s: str) -> str:
    if s not in SOURCES:
        raise ValueError(f"unknown source {s!r}")
    return s


def set_auto(s: str, on: bool, hour: int | None = None) -> dict:
    data = load()
    st = data[_src(s)]
    st["auto"] = bool(on)
    if hour is not None and 0 <= int(hour) <= 23:
        st["hour"] = int(hour)
    _save(data)
    return st


def _busy(job: dict | None, now: float) -> bool:
    return bool(job) and job.get("state") in ("taken", "sent") and now - float(job.get("taken") or 0) < STALE_S


def _expire(st: dict, now: float) -> None:
    """Взятое задание без ответа дольше STALE_S — в «последний итог» с ошибкой; можно давать следующее."""
    job = st.get("job")
    if job and job.get("state") in ("taken", "sent") and not _busy(job, now):
        st["last"] = {"at": now, "state": "error", "kind": job.get("kind", ""),
                      "note": "ответа нет 45 минут (вкладку закрыли, Mac уснул или агент не закончил) — попробуйте ещё раз"}
        st["job"] = None


def _job(task: dict, kind: str, vins: list[str] | None, now: float, state: str) -> dict:
    return {"id": uuid.uuid4().hex[:12], "kind": kind, "text": task.get("text", ""), "ids": task.get("ids") or {},
            "vins": list(vins or []), "groups": len(task.get("groups") or []), "made": now,
            "taken": now if state in ("taken", "sent") else 0, "state": state, "note": ""}


def queue(s: str, task: dict, kind: str = "button", vins: list[str] | None = None, now: float | None = None) -> dict:
    """Кнопка в окне: задание в очередь (расширение возьмёт в течение минуты). Агент уже работает — не мешаем."""
    now = time.time() if now is None else now
    data = load()
    st = data[_src(s)]
    _expire(st, now)
    if _busy(st.get("job"), now) and st["job"].get("kind") != "claude":   # общее задание страницы /agent — можно заменить
        return {"ok": False, "note": "агент уже работает над заданием — дождитесь ответа", "job": public(st["job"])}
    if not task.get("text"):
        return {"ok": False, "note": "спрашивать нечего: по этим машинам цены свежие или машин нет в списке"}
    st["job"] = _job(task, kind, vins, now, "queued")
    _save(data)
    return {"ok": True, "job": public(st["job"])}


def next_job(s: str, build: Callable[[], dict], now: float | None = None, local_now: time.struct_time | None = None) -> dict | None:
    """Расширение спрашивает раз в минуту: задание из очереди (кнопка) или раз в день само («Авто»).
    build() — собрать общее задание (дорого: считает список машин) — зовём только когда пора. Выданное — «взято»."""
    now = time.time() if now is None else now
    local_now = time.localtime(now) if local_now is None else local_now
    data = load()
    st = data[_src(s)]
    _expire(st, now)
    job = st.get("job")
    if _busy(job, now):
        _save(data)
        return None
    if job and job.get("state") == "queued":
        job.update(state="taken", taken=now)
        _save(data)
        return job
    today = time.strftime("%Y-%m-%d", local_now)
    if st["auto"] and local_now.tm_hour >= int(st["hour"]) and st["last_auto_day"] != today:
        st["last_auto_day"] = today                  # раз в день, даже если спрашивать нечего
        task = build()
        if task.get("text"):
            st["job"] = _job(task, "auto", None, now, "taken")
            _save(data)
            return st["job"]
        st["last"] = {"at": now, "state": "done", "kind": "auto", "note": "все группы свежие — задание не понадобилось"}
    _save(data)
    return None


def report(s: str, job_id: str, state: str, note: str = "", now: float | None = None) -> dict | None:
    """Ход задания от расширения: sent (отправлено агенту), done / error — итог (задание закрыто)."""
    now = time.time() if now is None else now
    data = load()
    st = data[_src(s)]
    job = st.get("job")
    if not job or job.get("id") != job_id:
        return None
    if state in ("done", "error"):
        st["last"] = {"at": now, "state": state, "kind": job.get("kind", ""), "note": str(note)[:500]}
        st["job"] = None
    else:
        job.update(state=state, note=str(note)[:300])
    _save(data)
    return job


def answered(s: str, note: str, now: float | None = None) -> None:
    """Ответ пришёл со страницы /agent/… (Claude в Chrome) — задание закрыто."""
    now = time.time() if now is None else now
    data = load()
    st = data[_src(s)]
    job = st.get("job") or {}
    st["last"] = {"at": now, "state": "done", "kind": job.get("kind", "claude"), "note": str(note)[:500]}
    st["job"] = None
    _save(data)


def page_task(s: str, build: Callable[[], dict], now: float | None = None) -> dict:
    """Страница /agent/cg для Claude в Chrome: задание по кнопке (отмеченные машины) или общее — группы без свежих цен."""
    now = time.time() if now is None else now
    data = load()
    st = data[_src(s)]
    job = st.get("job")
    if job and job.get("state") in ("queued", "taken", "sent") and now - float(job.get("made") or 0) < 24 * 3600:
        job.update(state="sent", taken=job.get("taken") or now)
        _save(data)
        return job
    task = build()
    if not task.get("text"):
        return {"id": "", "kind": "claude", "text": "", "ids": {}, "groups": 0}
    st["job"] = _job(task, "claude", None, now, "sent")     # обновили страницу — то же задание, пока нет ответа
    _save(data)
    return st["job"]


def public(job: dict | None) -> dict | None:
    """Для окна — без текста задания."""
    if not job:
        return None
    return {k: job.get(k) for k in ("id", "kind", "state", "made", "taken", "groups", "note")} | {"cars": len(job.get("vins") or [])}


def summary(now: float | None = None) -> dict:
    now = time.time() if now is None else now
    data = load()
    out = {}
    for s in SOURCES:
        st = data[s]
        job = st.get("job")
        if job and job.get("state") in ("taken", "sent") and not _busy(job, now):
            job = None
        out[s] = {"auto": st["auto"], "hour": st["hour"], "job": public(job), "last": st.get("last")}
    return out


def page(s: str, job: dict) -> str:
    """Страница для Claude в Chrome (ярлык по расписанию): задание и поле ответа. По-английски — это читает агент."""
    import html
    site = "CarGurus.com" if s == "cg" else "Facebook Marketplace"
    head = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>Lot Analyzer task</title><style>body{{font:15px/1.45 -apple-system,'Segoe UI',Arial,sans-serif;margin:20px auto;max-width:980px;"
            "padding:0 16px;color:#1d2b45;background:#fff}pre{white-space:pre-wrap;background:#f3f6fb;border:1px solid #c9d6ea;border-radius:8px;"
            "padding:12px;font-size:13px}textarea{width:100%;box-sizing:border-box;min-height:220px;font:13px ui-monospace,Menlo,monospace}"
            "button{font-size:16px;padding:8px 18px;border-radius:8px;border:1px solid #2f5597;background:#2f5597;color:#fff;cursor:pointer}"
            "#st{margin-left:10px;font-weight:600}.ru{color:#667;font-size:13px}</style></head><body>"
            "<p class=\"ru\">Lot Analyzer: страница для Claude в Chrome — задание и поле ответа (ярлык по расписанию).</p>")
    if not job.get("text"):
        return (head + f"<h1 id=\"done\">Nothing to do now</h1><p>All cars already have fresh {site} prices. You are done — "
                "do not open other sites. Close this tab.</p></body></html>")
    return (head + f"<h1>Task: {site} prices for the user's car list</h1>"
            "<p>This page is the user's own program (Lot Analyzer) running on this computer. Steps:</p><ol>"
            f"<li>Do the task below on {site} in another tab — only look at listings: do not contact sellers or dealers, do not fill "
            "forms there, do not buy or sell anything, do not share any personal info.</li>"
            "<li>Come back to this tab, paste your CSV (header line + rows) into the box at the bottom and press <b>Load answer</b>.</li>"
            "<li>Wait until it says <b>Loaded</b>. Then you are done.</li></ol>"
            f"<pre id=\"task\">{html.escape(job['text'])}</pre>"
            "<label for=\"answer\"><b>Answer (CSV):</b></label><textarea id=\"answer\" placeholder=\"group,year,make,model,…\"></textarea>"
            "<p><button id=\"load\" type=\"button\">Load answer</button><span id=\"st\"></span></p>"
            "<script>document.getElementById('load').onclick=async function(){const st=document.getElementById('st');"
            "const text=document.getElementById('answer').value;if(!text.trim()){st.textContent='Paste the CSV first.';return}"
            "st.textContent='Loading…';try{const r=await fetch('/api/agent_answer',{method:'POST',headers:{'Content-Type':'application/json','X-LA':'1'},"
            f"body:JSON.stringify({{src:{json.dumps(s)},id:{json.dumps(job.get('id', ''))},text}})}});const d=await r.json();"
            "st.textContent=d.ok?`Loaded: ${d.found} listings (${d.added} new). Done — you can close this tab.`:"
            "'Not loaded: '+(d.error||'unknown error')+' — fix the CSV and press Load answer again.'}"
            "catch(e){st.textContent='The program is not answering: '+e}}</script></body></html>")
