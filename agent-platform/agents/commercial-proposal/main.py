"""Агент «Коммерческое предложение».

Шаги:
1. LLM разбирает задачу и подбирает услуги из прайса.
2. Код считает стоимость и сроки по прайсу (pricing.py, без LLM).
3. Поиск похожих кейсов в базе знаний.
4. LLM пишет текст КП по шаблону; код проверяет, что итоговая сумма не изменилась.
"""

import json
import re

from agent_sdk import agent, llm
from pricing import fmt_rub, plan_timeline, price_services, services_table


@agent
def run(input, ctx):
    found = ctx.get("clients", name=input["client"])
    client = found[0] if found else None
    # Роль запустившего решает, что придёт из раздела pricing: продажи получат
    # только цены, руководитель — ещё и себестоимость. КП строится только по ценам.
    catalog = {r["meta"]["service"]: r for r in ctx.get("pricing") if r["meta"].get("kind") == "price"}

    # 1. Подбор услуг — LLM
    selection = select_services(input, client, catalog)
    assumptions = list(selection["assumptions"])
    unknown = [s["id"] for s in selection["services"] if s["id"] not in catalog]
    if unknown:
        assumptions.append(f"Модель предложила услуги не из прайса, они исключены: {', '.join(unknown)}")
    chosen = [s for s in selection["services"] if s["id"] in catalog] or [{"id": "ai-audit", "qty": 1}]

    # 2. Стоимость и сроки — код
    lines = price_services(chosen, catalog)
    total = sum(line["price"] for line in lines)
    timeline = plan_timeline(lines, input.get("deadline"))
    budget = input.get("budget")
    if budget and total > budget:
        assumptions.append(f"Стоимость выше бюджета клиента на {fmt_rub(total - budget)}; "
                           "можно начать с первого этапа и перенести остальное.")
    if timeline["fits_deadline"] is False:
        assumptions.append(f"Полный объём завершается {timeline['end']}, позже желаемого срока "
                           f"{timeline['deadline']}; к сроку можно запустить первый этап.")
    ctx.step("Расчёт по прайсу (код, без LLM)", total=total, weeks=timeline["weeks"],
             services=[line["id"] for line in lines])

    # 3. Похожие кейсы — поиск
    industry = client["meta"].get("industry", "") if client else ""
    tags = " ".join(" ".join(catalog[line["id"]]["meta"].get("tags", [])) for line in lines)
    cases = ctx.search(f"{industry} {input['task']} {tags}", section="cases", limit=3)

    # 4. Текст КП — LLM по шаблону
    template = ctx.get("templates", kind="commercial-proposal")[0]["body"]
    draft = fill_template(template, input, client, lines, total, timeline, cases, assumptions)
    markdown = write_proposal(draft, input, total)
    if fmt_rub(total) not in markdown:
        ctx.step("Проверка итоговой суммы", ok=False, note="LLM изменил сумму, взят черновик")
        markdown = draft

    return {
        "markdown": markdown,
        "services": lines,
        "total_price": total,
        "currency": "RUB",
        "timeline": timeline,
        "cases": [{"id": c["id"], "title": c["title"], "industry": c["meta"].get("industry", "")}
                  for c in cases],
        "assumptions": assumptions,
    }


def select_services(input, client, catalog):
    services = "\n".join(f"- {sid}: {r['title']} ({r['meta'].get('unit')}). {r['body'].split(chr(10))[-1]}"
                         for sid, r in catalog.items())
    prompt = (
        f"Задача клиента: {input['task']}\n"
        f"Клиент: {client['body'] if client else input['client']}\n"
        f"Бюджет: {input.get('budget') or 'не указан'}. Срок: {input.get('deadline')}.\n\n"
        f"Услуги студии:\n{services}\n\n"
        'Подбери минимальный набор услуг. Ответь только JSON: '
        '{"services": [{"id": "...", "qty": 1, "reason": "..."}], "assumptions": ["..."]}'
    )
    text = llm(prompt, system="Ты пресейл-консультант студии внедрения AI-агентов.",
               max_tokens=2000, mock=json.dumps(keyword_selection(input["task"], catalog)))
    try:
        data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        return {"services": list(data.get("services", [])), "assumptions": list(data.get("assumptions", []))}
    except (AttributeError, ValueError):
        return {"services": [], "assumptions": ["Ответ модели не разобран, предложен AI-аудит."]}


def keyword_selection(task, catalog):
    """Демо-подбор без модели: услуги, у которых теги встречаются в тексте задачи."""
    words = {w[:5] for w in re.findall(r"\w+", task.lower()) if len(w) > 3}
    picked = []
    for sid, rec in catalog.items():
        tags = {t.lower()[:5] for tag in rec["meta"].get("tags", []) for t in tag.split()}
        if words & tags:
            qty = 3 if rec["meta"].get("unit") == "мес." else 1
            picked.append({"id": sid, "qty": qty, "reason": "совпадение с задачей: " + ", ".join(sorted(words & tags))})
    return {"services": picked, "assumptions": ["Подбор выполнен по ключевым словам (демо-режим без модели)."]}


def fill_template(template, input, client, lines, total, timeline, cases, assumptions):
    deadline_note = ""
    if timeline["fits_deadline"] is not None:
        deadline_note = " — укладываемся в желаемый срок." if timeline["fits_deadline"] else " — позже желаемого срока."
    values = {
        "client": client["title"] if client else input["client"],
        "task_summary": input["task"],
        "services_table": services_table(lines),
        "total": fmt_rub(total),
        "timeline": f"Старт {timeline['start']}, запуск {timeline['end']} ({timeline['weeks']} нед.){deadline_note}",
        "cases": "\n".join(f"- **{c['title']}**. {c['body'].split('**Результат.**')[-1].strip()}" for c in cases)
                 or "- Подберём по запросу.",
        "assumptions": "\n".join(f"- {a}" for a in assumptions) or "- Нет.",
    }
    return template.format(**values)


def write_proposal(draft, input, total):
    lang = "английском" if input.get("language") == "en" else "русском"
    return llm(
        f"Вот черновик коммерческого предложения:\n\n{draft}\n\n"
        f"Перепиши его на {lang} языке: сформулируй раздел «Задача» понятно для клиента, "
        f"остальное отредактируй стилистически. Таблицу, суммы и даты оставь без изменений, "
        f"итог «{fmt_rub(total)}» должен остаться в тексте дословно. Верни только markdown.",
        system="Ты пишешь коммерческие предложения студии внедрения AI-агентов: коротко, по делу, без обещаний сверх сметы.",
        max_tokens=4000,
        mock=draft,
    )
