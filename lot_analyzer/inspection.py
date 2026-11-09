"""Список машин на личный осмотр перед торгами (печатная страница).

CarMax: в список попадает ваше избранное на сайте (звёздочка; машину с заметкой в
Notes CarMax кладёт в избранное сам). Сняли звёздочку — машина уходит из списка.
Если по сохранённым страницам не видно, в избранном ли машина, и на других
аукционах — лоты с пометкой «Личный осмотр»: объявлен «Major Engine /
Transmission Defect» (часто не подтверждается) или нет фото, — кроме тех, что
отсеяны стоп-факторами или невыгодны. Группировка: аукцион → площадка → дата,
внутри — по дорожке и номеру, как вы будете идти по стоянке.

Перед печатью список можно поправить прямо на странице: любую ячейку — щёлкнуть и
исправить, лишнюю машину — убрать (✕). Правки и удалённые запоминаются в этом
браузере для этого списка; «Вернуть всё» отменяет их.
"""

from __future__ import annotations

import html
import re
from collections import OrderedDict

SKIP_VERDICTS = ("ПРОПУСТИТЬ", "НЕВЫГОДНО")


_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()


def _day(sale_date: str) -> str:
    """День торгов без времени: «Oct 12, 9:00am PT», «Oct 12», «10/12/2026», «2026-10-12» → «10-12»."""
    text = str(sale_date or "").strip().lower()
    found = re.search(r"\b(" + "|".join(_MONTHS) + r")[a-z]*\.?\s+(\d{1,2})\b", text)
    if found:
        return f"{_MONTHS.index(found.group(1)) + 1:02d}-{int(found.group(2)):02d}"
    found = re.search(r"\b20\d\d-(\d{1,2})-(\d{1,2})\b", text) or re.search(r"\b(\d{1,2})/(\d{1,2})(?:/\d{2,4})?\b", text)
    return f"{int(found.group(1)):02d}-{int(found.group(2)):02d}" if found else ""


def _place(row: dict[str, str]) -> str:
    if row.get("place"):
        return row["place"]
    from .app import place_of_row                   # «CarMax Oceanside» и из «Oceanside, CA», и из «CarMax Oceanside»
    return place_of_row(row)


def _groups(rows: list[dict[str, str]]) -> dict[int, tuple[str, str]]:
    """Группа каждой строки: (площадка, день). Одна площадка и один день — одна группа, как бы ни была записана дата:
    PDF-список даёт «Oct 12» или без даты, сайт — «Oct 12, 9:00am PT». Без даты — к единственному дню этой площадки."""
    days: dict[str, set[str]] = {}
    for r in rows:
        if _day(r.get("sale_date", "")):
            days.setdefault(_place(r), set()).add(_day(r.get("sale_date", "")))
    out = {}
    for r in rows:
        place, day = _place(r), _day(r.get("sale_date", ""))
        if not day and len(days.get(place, ())) == 1:
            day = next(iter(days[place]))
        out[id(r)] = (place, day)
    return out


def wanted(row: dict[str, str]) -> bool:
    return bool(why(row))


def why(row: dict[str, str]) -> str:
    """Почему машина в списке на осмотр (графа «Добавлено»); пусто — её в списке нет."""
    if row.get("my_proxy_usd"):
        return "ставка"                                # ставка поставлена — в списке и на телефоне всегда, что бы ни было
    if row.get("inspect_manual") and not row.get("rejected"):
        return "вручную из программы"                  # «На осмотр» в окне — сами решили осмотреть
    return _why_auto(row)


def _why_auto(row: dict[str, str]) -> str:
    if not _wanted_auto(row):
        return ""
    if row.get("kbb_private_party_usd") and not row.get("kbb_from_window_field"):
        return "просчитана (KBB)"
    if row.get("no_photos") == "да" and row.get("popular"):
        return "без фото, популярная"
    if row.get("auction") == "CarMax" and row.get("favorite_known"):
        return "избранное CarMax"
    return "отобрана программой"


def _wanted_auto(row: dict[str, str]) -> bool:
    if row.get("favorite_removed"):
        return False                                   # сняли звёздочку — сами решили не смотреть
    if row.get("rejected"):
        return False                                   # «🚫 Отбой» в окне — посмотрели лот, не покупать
    if row.get("kbb_private_party_usd") and not row.get("kbb_from_window_field"):
        return True                                    # просчитали (настоящий KBB) — и не «Отбой»: смотрим
    if row.get("no_photos") == "да" and row.get("popular") and not row.get("calc_verdict", "").startswith(SKIP_VERDICTS):
        return True                                    # NO PICTURES у популярной с прибылью — смотреть только на месте
    if row.get("auction") == "CarMax" and row.get("favorite_known"):
        return bool(row.get("favorite"))               # CarMax: ваше избранное — ваш финальный список
    return bool(row.get("inspect")) and not row.get("calc_verdict", "").startswith(SKIP_VERDICTS)


# Площадка под номером лота — коротко, чтобы графа «Лот» оставалась узкой: Oceanside → Ocean (Murrieta — целиком).
SHORT_PLACE = {"Oceanside": "Ocean"}

# Порядок площадок в один день — как вы их объезжаете: сначала Oceanside, потом Murrieta, потом Chino.
PLACE_ORDER = ("Oceanside", "Murrieta", "Chino")
# Куда вы можете доехать на осмотр. Машины остальных площадок в список на осмотр и на телефон не попадают
# (и со ставкой тоже — их не осмотреть); исключение — «На осмотр» в окне: вы сами выбрали машину.
VISIT_PLACES = ("Oceanside", "Murrieta", "Chino")


def can_visit(row: dict[str, str]) -> bool:
    if not VISIT_PLACES:                              # пусто — все площадки
        return True
    low = _place(row).lower()
    return any(name.lower() in low for name in VISIT_PLACES)


_WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
_MONTHS_RU = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")


def day_title(day: str, sale_date: str = "") -> str:
    """«11-09» (+ «Nov 9, 9:00am PT») → «понедельник, 9 ноября · 9:00am PT»."""
    import datetime as dt
    try:
        month, num = (int(x) for x in day.split("-"))
        today = dt.date.today()
        year = today.year + (1 if month < today.month - 6 else 0)
        date = dt.date(year, month, num)
    except (ValueError, TypeError):
        return sale_date or "дата торгов не указана"
    time = re.search(r"\d{1,2}:\d{2}\s*[ap]m(\s*[A-Z]{2})?", sale_date or "", re.I)
    return f"{_WEEKDAYS[date.weekday()]}, {date.day} {_MONTHS_RU[date.month - 1]}" + (f" · {time.group(0)}" if time else "")


def _place_rank(place: str) -> tuple:
    low = place.lower()
    return next((i for i, name in enumerate(PLACE_ORDER) if name.lower() in low), len(PLACE_ORDER)), place


def inspection_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    picked = [r for r in rows if wanted(r) and (can_visit(r) or why(r) == "вручную из программы")]
    groups = _groups(picked)

    def lane_run(row: dict[str, str]) -> tuple:
        match = re.match(r"^([A-Z])\s*/\s*(\d+)", row.get("lot_number", ""))
        return (match.group(1), int(match.group(2))) if match else ("~", 0)

    return sorted(picked, key=lambda r: (groups[id(r)][1] or "99", _place_rank(groups[id(r)][0]), lane_run(r)))


def _notes_html(row: dict[str, str]) -> str:
    """Под VIN: заметки программы (KBB с датой, MP, «Был: …», замечания CarMax — то, что пишется в Notes на CarMax)
    и ваши собственные заметки из Notes на сайте CarMax."""
    from .notes import lot_note

    lines = [x for x in lot_note(row).split("\n") if x.strip()]
    if not any(x.startswith("CarMax: ") for x in lines) and (row.get("auction_notes") or row.get("defects")):
        notes = row.get("auction_notes") or row.get("defects")
        if notes != "no announcements":
            lines.append(f"CarMax: {notes}")
    # Ваши строки из Notes на CarMax — без строк программы (KBB с датой, MP): свежие уже стоят выше.
    own = [x.strip() for x in str(row.get("site_notes") or "").split("\n")
           if x.strip() and not re.match(r"^\s*(KBB|MP)\s*(PP\s*)?\$?\s*[\d,]+\s*\$?(\s+\d{1,2}/\d{1,2}/\d{2,4})?\s*$", x, re.I)]
    out = "".join(f"<div class='note'>{html.escape(x)}</div>" for x in lines)
    if own:
        out += "<div class='note own'>Мои заметки: " + html.escape(" · ".join(own)) + "</div>"
    return out


# «Major Engine Defect», «Major Transmission Defect» в объявлении аукциона — в графе «Машина», крупно и жирно.
_MAJOR = re.compile(r"major\s+(engine|transmission)\s+defect", re.I)


def major_defects(row: dict[str, str]) -> list[str]:
    """«engine», «transmission» — объявленные CarMax тяжёлые дефекты (сначала двигатель)."""
    text = " ".join(str(row.get(k) or "") for k in ("auction_notes", "defects", "condition_report"))
    return sorted({m.group(1).lower() for m in _MAJOR.finditer(text)}, key=lambda x: x != "engine")


def _major_html(row: dict[str, str]) -> str:
    return "".join(f"<div class='major'>⚠ MAJOR {kind.upper()} DEFECT</div>" for kind in major_defects(row))


def _money(value: str) -> str:
    try:
        return f"${float(value):,.0f}" if value else "—"
    except ValueError:
        return value


def render_html(rows: list[dict[str, str]], title: str = "Список на осмотр") -> str:
    items = inspection_rows(rows)
    keys = _groups(items)
    by_key: "OrderedDict[tuple[str, str], list[dict[str, str]]]" = OrderedDict()
    for row in items:
        by_key.setdefault(keys[id(row)], []).append(row)
    groups: "OrderedDict[str, list[dict[str, str]]]" = OrderedDict()
    for (place, day), group_rows in by_key.items():
        # Сверху страницы — площадка и дата: «Oceanside — понедельник, 9 ноября · 9:00am PT».
        when = max((r.get("sale_date", "") for r in group_rows), key=len)
        groups[re.sub(r"^CarMax\s+", "", place or "") + " — " + day_title(day, when)] = group_rows
    # На экране сверху — все площадки и дни списка («Oceanside, Murrieta — понедельник, 9 ноября»); на печать не идёт.
    by_day: "OrderedDict[str, list[str]]" = OrderedDict()
    for (place, day), _rows in by_key.items():
        by_day.setdefault(day_title(day), []).append(re.sub(r"^CarMax\s+", "", place or ""))
    heading = "; ".join(f"{', '.join(dict.fromkeys(p))} — {d}" for d, p in by_day.items()) or title
    esc = lambda s: html.escape(str(s or ""))
    parts = [f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title><style>
:root{{--fg:#1c1c1a;--muted:#62625c;--line:#d8d8d2;--bg:#fff}}
@media (prefers-color-scheme:dark){{:root{{--fg:#ececea;--muted:#a2a29c;--line:#3a3a35;--bg:#151514}}}}
@media print{{:root{{--fg:#000;--muted:#444;--line:#999;--bg:#fff}} .noprint{{display:none}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:13px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1200px;margin:0 auto;padding:16px}}h1{{font-size:20px;margin:0 0 4px}}h2{{font-size:15px;margin:18px 0 6px}}
.muted{{color:var(--muted)}}table{{width:100%;border-collapse:collapse}}th,td{{border:1px solid var(--line);padding:5px 6px;vertical-align:top;text-align:left}}
td.num{{text-align:right}}td.box{{width:70px}}button{{font:inherit;padding:6px 12px}}
table{{table-layout:fixed}}td,th{{overflow-wrap:break-word}}th{{hyphens:auto}}td.lot .place{{white-space:nowrap}}td.car .major{{margin-top:4px;padding:2px 5px;font-weight:800;font-size:14px;line-height:1.25;color:#b91c1c;border:2px solid #b91c1c;border-radius:4px;background:rgba(185,28,28,.08)}}td.car .manual{{margin-top:4px;font-weight:700;padding:1px 4px;border-left:3px solid #d97706;background:rgba(217,119,6,.12)}}td.num{{white-space:nowrap}}
td.car .vin{{font-family:ui-monospace,Menlo,monospace;font-size:12px;margin-top:2px;overflow-wrap:anywhere}}td.car .miles{{margin-top:0}}
tr{{break-inside:avoid;page-break-inside:avoid}}thead{{display:table-header-group}}
@media print{{h2.newpage{{break-before:page;page-break-before:always}}h2{{margin-top:0}}}}
@page{{size:letter;margin:8mm}}
@media print{{body{{font-size:11px}}main{{padding:0}}th,td{{padding:3px 4px}}td.info .note,td.car .vin,td.lot .place{{font-size:10px}}th{{font-size:10px}}td.car .manual{{background:none}}td.car .major{{font-size:12px;color:#000;border-color:#000;background:none}}}}
td.info .note{{font-size:12px;color:var(--muted)}}td.info .note.own{{color:var(--fg)}}td.lot .place{{font-size:12px;color:var(--muted)}}
.nopics{{font-weight:700;letter-spacing:.5px;margin-top:2px}}td.manual{{font-weight:700}}
td[contenteditable]:hover{{outline:1px dashed var(--muted)}}td[contenteditable]:focus{{outline:2px solid #2f6fd6;background:rgba(47,111,214,.06)}}
td.edited{{background:rgba(230,180,40,.14)}}td.del{{width:28px;text-align:center}}td.del button{{padding:1px 7px;cursor:pointer}}
@media print{{td.edited{{background:none}}}}
</style></head><body><main>
<h1 class="noprint">{esc(heading)}</h1><div class="muted noprint">Лотов: <span id="count">{len(items)}</span>. Только площадки, куда вы ездите: {', '.join(VISIT_PLACES)}. NO PICTURES — фото нет: осмотреть обязательно. «Major … Defect» у CarMax часто не подтверждается — на месте: заводится ли, как едет, как переключается,
нет ли стуков и ошибок.
CarMax — машины из вашего избранного на сайте: сняли звёздочку — машина уходит из этого списка.</div>
<p class="noprint"><button onclick="print()">Печать</button>
<button id="restore" onclick="restoreAll()" hidden>Вернуть всё</button>
<button id="phone" onclick="toPhone()" title="На телефон — ровно этот список: без убранных ✕ машин и с тем, что вы написали в графе «Заметки»">📱 На телефон</button> <span id="phonestate" class="muted"></span>
<span class="muted">Ячейки можно исправить — щёлкните и пишите; ✕ убирает машину из списка. Правки запоминаются в этом браузере.</span></p>"""]
    if not items:
        parts.append('<p class="muted">Нет лотов, требующих личного осмотра.</p>')
    for n, (group, group_rows) in enumerate(groups.items()):
        # Графы: лот и площадка · машина (год, марка, модель, VIN, через строку — пробег; «добавлено вручную») · замечания
        # и история · KBB · средняя цена покупки · ваши заметки · ставка · итог. Ширины — под печать на одну страницу.
        # Каждый аукцион — с новой страницы.
        parts.append(f"<h2{' class=newpage' if n else ''}>{esc(group)}</h2><table><colgroup><col style='width:7%'><col style='width:18%'>"
                     "<col style='width:22%'><col style='width:8%'><col style='width:9%'><col style='width:17%'><col style='width:8%'>"
                     "<col style='width:11%'><col class='noprint' style='width:30px'></colgroup>"
                     "<thead><tr><th>Лот</th><th>Машина</th><th>Замечания и история</th>"
                     "<th>KBB</th><th>Средняя покупка на аукционе</th><th class='notes'>Заметки</th>"
                     "<th>Моя ставка</th><th>Результат торгов</th><th class='noprint'></th></tr></thead><tbody>")
        for r in group_rows:
            kbb = _money(r.get("kbb_private_party_usd")) if r.get("kbb_private_party_usd") else (
                "≈" + _money(r.get("kbb_estimate_usd")) if r.get("kbb_estimate_usd") else "—")
            car = " ".join(x for x in (r.get("year"), r.get("make"), r.get("model"), r.get("trim")) if x)
            no_pics = "<div class='nopics'>NO PICTURES</div>" if r.get("no_photos") == "да" else ""
            lot = f'<a href="{esc(r["lot_url"])}">{esc(r.get("lot_number") or "лот")}</a>' if r.get("lot_url") else esc(r.get("lot_number"))
            place = re.sub(r"^CarMax\s+", "", keys[id(r)][0] or "")      # площадка — под номером лота
            place = SHORT_PLACE.get(place, place)
            try:
                miles = f"{int(float(str(r.get('odometer_miles')).replace(',', ''))):,} mi"
            except (TypeError, ValueError):
                miles = str(r.get("odometer_miles") or "")
            result = esc((r.get("auction_result") or "").split(" · ")[0])
            # «На осмотр» в окне — вы сами выбрали машину: смотреть в первую очередь и внимательнее.
            manual = "<div class='manual'>✋ Добавлено вручную — смотреть внимательно</div>" if why(r) == "вручную из программы" else ""
            parts.append(
                f"<tr data-id='{esc(r.get('vin') or r.get('lot_url') or r.get('lot_number'))}'><td class='lot'>{lot}<div class='place'>{esc(place)}</div></td>"
                f"<td class='car'><b>{esc(car)}</b>{no_pics}<div class='vin'>{esc(r.get('vin'))}</div><div class='miles'>{esc(miles)}</div>{_major_html(r)}{manual}</td>"
                f"<td class='info'>{_notes_html(r)}</td>"
                f"<td class='num'>{kbb}</td><td class='num'>{_money(r.get('market_estimate_usd'))}</td>"
                f"<td class='notes'></td><td class='num'>{_money(r.get('my_proxy_usd')) if r.get('my_proxy_usd') else ''}</td><td>{result}</td>"
                "<td class='del noprint'><button type='button' title='Убрать из списка'>✕</button></td></tr>")
        parts.append("</tbody></table>")
    parts.append("<script>" + _SCRIPT + "</script></main></body></html>")
    return "".join(parts)


# Правки списка в браузере: ключ — заголовок списка; хранится {removed: [id], cells: {id: {номер ячейки: html}}, title}.
_SCRIPT = r"""
const KEY='la-inspection5:'+document.title;   // 5 — без графы «Добавлено»: старые правки не переносим
let state={removed:[],cells:{},title:''};
try{state=Object.assign(state,JSON.parse(localStorage.getItem(KEY)||'{}'))}catch(e){}
const save=()=>{try{localStorage.setItem(KEY,JSON.stringify(state))}catch(e){}};
const rows=()=>[...document.querySelectorAll('tr[data-id]')];
function refresh(){
  rows().forEach(tr=>{tr.hidden=state.removed.includes(tr.dataset.id)});
  document.querySelectorAll('table').forEach(t=>{const any=[...t.querySelectorAll('tr[data-id]')].some(tr=>!tr.hidden);t.hidden=!any;const h=t.previousElementSibling;if(h&&h.tagName==='H2')h.hidden=!any});
  document.getElementById('count').textContent=rows().filter(tr=>!tr.hidden).length;
  const changed=state.removed.length||Object.keys(state.cells).length||state.title;
  const b=document.getElementById('restore');b.hidden=!changed;
  b.textContent='Вернуть всё'+(state.removed.length?` (убрано: ${state.removed.length})`:'');
}
rows().forEach(tr=>{
  const id=tr.dataset.id;
  [...tr.cells].forEach((td,i)=>{
    if(td.classList.contains('del'))return;
    const saved=(state.cells[id]||{})[i];
    if(saved!==undefined){td.innerHTML=saved;td.classList.add('edited')}
    td.contentEditable='true';
    td.addEventListener('input',()=>{(state.cells[id]=state.cells[id]||{})[i]=td.innerHTML;td.classList.add('edited');save();refresh()});
  });
  tr.querySelector('td.del button').addEventListener('click',()=>{if(!state.removed.includes(id))state.removed.push(id);save();refresh()});
});
const h1=document.querySelector('h1');
document.addEventListener('click',e=>{const a=e.target.closest('td[contenteditable] a');if(a&&!(e.metaKey||e.ctrlKey)){e.preventDefault();window.open(a.href,'_blank')}});
/* «📱 На телефон»: что видно на странице (без убранных ✕) и ваш текст из графы «Заметки» — в приложение для осмотра. */
async function toPhone(){
  const st=document.getElementById('phonestate'),kept=rows().filter(tr=>!tr.hidden),notes={};
  kept.forEach(tr=>{const td=tr.querySelector('td.notes');const t=td?td.innerText.trim():'';if(t)notes[tr.dataset.id]=t});
  st.textContent='Отправляю…';
  try{const r=await fetch('/api/voice_push',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({params:location.search.slice(1),keep:kept.map(tr=>tr.dataset.id),notes})});
      const d=await r.json();
      st.textContent=d.ok?`На телефоне: ${d.cars} машин. В приложении нажмите «Обновить».`:('Не отправлено: '+(d.error||'ошибка'))}
  catch(e){st.textContent='Не отправлено: окно программы не запущено'}}
function restoreAll(){if(!confirm('Вернуть убранные машины и отменить все правки?'))return;try{localStorage.removeItem(KEY)}catch(e){}location.reload()}
refresh();
"""
