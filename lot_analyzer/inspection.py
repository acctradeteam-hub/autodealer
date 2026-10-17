"""Список машин на личный осмотр перед торгами (печатная страница).

В список попадают лоты с пометкой «Личный осмотр» — объявлен «Major Engine /
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


def inspection_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    picked = [r for r in rows if r.get("inspect") and not r.get("calc_verdict", "").startswith(SKIP_VERDICTS)]
    groups = _groups(picked)

    def lane_run(row: dict[str, str]) -> tuple:
        match = re.match(r"^([A-Z])\s*/\s*(\d+)", row.get("lot_number", ""))
        return (match.group(1), int(match.group(2))) if match else ("~", 0)

    return sorted(picked, key=lambda r: (groups[id(r)][1] or "99", groups[id(r)][0], lane_run(r)))


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
        # Подпись дня — самая полная запись даты в группе («Oct 12, 9:00am PT» лучше, чем «Oct 12»).
        when = max((r.get("sale_date", "") for r in group_rows), key=len) or "дата торгов не указана"
        groups[" · ".join(x for x in (place, when) if x)] = group_rows
    esc = lambda s: html.escape(str(s or ""))
    parts = [f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title><style>
:root{{--fg:#1c1c1a;--muted:#62625c;--line:#d8d8d2;--bg:#fff}}
@media (prefers-color-scheme:dark){{:root{{--fg:#ececea;--muted:#a2a29c;--line:#3a3a35;--bg:#151514}}}}
@media print{{:root{{--fg:#000;--muted:#444;--line:#999;--bg:#fff}} .noprint{{display:none}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:13px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1200px;margin:0 auto;padding:16px}}h1{{font-size:20px;margin:0 0 4px}}h2{{font-size:15px;margin:18px 0 6px}}
.muted{{color:var(--muted)}}table{{width:100%;border-collapse:collapse}}th,td{{border:1px solid var(--line);padding:5px 6px;vertical-align:top;text-align:left}}
td.num{{text-align:right;white-space:nowrap}}td.box{{width:70px}}button{{font:inherit;padding:6px 12px}}
td[contenteditable]:hover{{outline:1px dashed var(--muted)}}td[contenteditable]:focus{{outline:2px solid #2f6fd6;background:rgba(47,111,214,.06)}}
td.edited{{background:rgba(230,180,40,.14)}}td.del{{width:28px;text-align:center}}td.del button{{padding:1px 7px;cursor:pointer}}
@media print{{td.edited{{background:none}}}}
</style></head><body><main>
<h1 contenteditable="true">{esc(title)}</h1><div class="muted">Лотов: <span id="count">{len(items)}</span>. «Major … Defect» у CarMax часто не подтверждается — на месте: заводится ли, как едет, как переключается,
нет ли стуков и ошибок. Ставка: до «без дефекта», если дефекта нет; до «с дефектом» — если подтвердился.</div>
<p class="noprint"><button onclick="print()">Печать</button>
<button id="restore" onclick="restoreAll()" hidden>Вернуть всё</button>
<span class="muted">Ячейки можно исправить — щёлкните и пишите; ✕ убирает машину из списка. Правки запоминаются в этом браузере.</span></p>"""]
    if not items:
        parts.append('<p class="muted">Нет лотов, требующих личного осмотра.</p>')
    for group, group_rows in groups.items():
        parts.append(f"<h2>{esc(group)}</h2><table><thead><tr><th>Лот</th><th>Машина</th><th>Пробег</th><th>VIN</th><th>Почему осмотр</th>"
                     "<th>KBB</th><th>Потолок без дефекта</th><th>Потолок с дефектом</th><th>Ставка сейчас</th>"
                     "<th>Дефект есть / нет</th><th>Моя ставка</th><th class='noprint'></th></tr></thead><tbody>")
        for r in group_rows:
            kbb = _money(r.get("kbb_private_party_usd")) if r.get("kbb_private_party_usd") else (
                "≈" + _money(r.get("kbb_estimate_usd")) if r.get("kbb_estimate_usd") else "—")
            car = " ".join(x for x in (r.get("year"), r.get("make"), r.get("model"), r.get("trim")) if x)
            lot = f'<a href="{esc(r["lot_url"])}">{esc(r.get("lot_number") or "лот")}</a>' if r.get("lot_url") else esc(r.get("lot_number"))
            parts.append(
                f"<tr data-id='{esc(r.get('vin') or r.get('lot_url') or r.get('lot_number'))}'><td>{lot}</td><td>{esc(car)}<div class='muted'>{esc(r.get('defects', '')[:120])}</div></td>"
                f"<td class='num'>{esc(r.get('odometer_miles'))}</td><td>{esc(r.get('vin'))}</td><td>{esc(r.get('inspect'))}</td>"
                f"<td class='num'>{kbb}</td><td class='num'><b>{_money(r.get('calc_max_bid_usd'))}</b></td>"
                f"<td class='num'>{_money(r.get('calc_max_bid_if_defect_usd')) if r.get('calc_max_bid_if_defect_usd') else ('не брать' if 'Defect' in r.get('inspect', '') else '—')}</td>"
                f"<td class='num'>{_money(r.get('current_bid_usd'))}</td><td class='box'></td><td class='box'></td>"
                "<td class='del noprint'><button type='button' title='Убрать из списка'>✕</button></td></tr>")
        parts.append("</tbody></table>")
    parts.append("<script>" + _SCRIPT + "</script></main></body></html>")
    return "".join(parts)


# Правки списка в браузере: ключ — заголовок списка; хранится {removed: [id], cells: {id: {номер ячейки: html}}, title}.
_SCRIPT = r"""
const KEY='la-inspection:'+document.title;
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
if(state.title)h1.textContent=state.title;
h1.addEventListener('input',()=>{state.title=h1.textContent;save();refresh()});
document.addEventListener('click',e=>{const a=e.target.closest('td[contenteditable] a');if(a&&!(e.metaKey||e.ctrlKey)){e.preventDefault();window.open(a.href,'_blank')}});
function restoreAll(){if(!confirm('Вернуть убранные машины и отменить все правки?'))return;try{localStorage.removeItem(KEY)}catch(e){}location.reload()}
refresh();
"""
