"""Список машин на личный осмотр перед торгами (печатная страница).

В список попадают лоты с пометкой «Личный осмотр» — объявлен «Major Engine /
Transmission Defect» (часто не подтверждается) или нет фото, — кроме тех, что
отсеяны стоп-факторами или невыгодны. Группировка: аукцион → площадка → дата,
внутри — по дорожке и номеру, как вы будете идти по стоянке.
"""

from __future__ import annotations

import html
import re
from collections import OrderedDict

SKIP_VERDICTS = ("ПРОПУСТИТЬ", "НЕВЫГОДНО")


def inspection_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    picked = [r for r in rows if r.get("inspect") and not r.get("calc_verdict", "").startswith(SKIP_VERDICTS)]

    def lane_run(row: dict[str, str]) -> tuple:
        match = re.match(r"^([A-Z])\s*/\s*(\d+)", row.get("lot_number", ""))
        return (match.group(1), int(match.group(2))) if match else ("~", 0)

    return sorted(picked, key=lambda r: (r.get("auction", ""), r.get("location", ""), r.get("sale_date", ""), lane_run(r)))


def _money(value: str) -> str:
    try:
        return f"${float(value):,.0f}" if value else "—"
    except ValueError:
        return value


def render_html(rows: list[dict[str, str]], title: str = "Список на осмотр") -> str:
    items = inspection_rows(rows)
    groups: "OrderedDict[str, list[dict[str, str]]]" = OrderedDict()
    for row in items:
        key = " · ".join(x for x in (row.get("auction", ""), row.get("location", ""), row.get("sale_date", "")) if x)
        groups.setdefault(key, []).append(row)
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
</style></head><body><main>
<h1>{esc(title)}</h1><div class="muted">Лотов: {len(items)}. «Major … Defect» у CarMax часто не подтверждается — на месте: заводится ли, как едет, как переключается,
нет ли стуков и ошибок. Ставка: до «без дефекта», если дефекта нет; до «с дефектом» — если подтвердился.</div>
<p class="noprint"><button onclick="print()">Печать</button></p>"""]
    if not items:
        parts.append('<p class="muted">Нет лотов, требующих личного осмотра.</p>')
    for group, group_rows in groups.items():
        parts.append(f"<h2>{esc(group)}</h2><table><thead><tr><th>Лот</th><th>Машина</th><th>Пробег</th><th>VIN</th><th>Почему осмотр</th>"
                     "<th>KBB</th><th>Потолок без дефекта</th><th>Потолок с дефектом</th><th>Ставка сейчас</th>"
                     "<th>Дефект есть / нет</th><th>Моя ставка</th></tr></thead><tbody>")
        for r in group_rows:
            kbb = _money(r.get("kbb_private_party_usd")) if r.get("kbb_private_party_usd") else (
                "≈" + _money(r.get("kbb_estimate_usd")) if r.get("kbb_estimate_usd") else "—")
            car = " ".join(x for x in (r.get("year"), r.get("make"), r.get("model"), r.get("trim")) if x)
            lot = f'<a href="{esc(r["lot_url"])}">{esc(r.get("lot_number") or "лот")}</a>' if r.get("lot_url") else esc(r.get("lot_number"))
            parts.append(
                f"<tr><td>{lot}</td><td>{esc(car)}<div class='muted'>{esc(r.get('defects', '')[:120])}</div></td>"
                f"<td class='num'>{esc(r.get('odometer_miles'))}</td><td>{esc(r.get('vin'))}</td><td>{esc(r.get('inspect'))}</td>"
                f"<td class='num'>{kbb}</td><td class='num'><b>{_money(r.get('calc_max_bid_usd'))}</b></td>"
                f"<td class='num'>{_money(r.get('calc_max_bid_if_defect_usd')) if r.get('calc_max_bid_if_defect_usd') else ('не брать' if 'Defect' in r.get('inspect', '') else '—')}</td>"
                f"<td class='num'>{_money(r.get('current_bid_usd'))}</td><td class='box'></td><td class='box'></td></tr>")
        parts.append("</tbody></table>")
    parts.append("</main></body></html>")
    return "".join(parts)
