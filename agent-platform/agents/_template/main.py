# Код агента. SDK (platform/sdk/agent_sdk.py) поднимает HTTP-сервис с POST /run
# и вызывает функцию с декоратором @agent для каждого запуска.
from agent_sdk import agent, llm


@agent
def run(input, ctx):
    # input уже проверен платформой по схеме из agent.yaml.

    # ctx.get(раздел, поле=значение) — записи раздела, которые видит роль запустившего.
    # Каждая запись: {"id", "section", "title", "meta" (поля frontmatter), "body"}.
    found = ctx.get("clients", name=input["client"])
    if not found:
        return {"client": input["client"], "brief": "Клиент не найден в базе знаний.", "cases": []}
    client = found[0]

    # ctx.search(запрос, section=...) — полнотекстовый поиск по разделам агента.
    cases = ctx.search(client["meta"].get("industry", ""), section="cases", limit=2)

    # Свой шаг в трассировку (get/search/llm записываются сами).
    ctx.step("Собраны материалы", client=client["id"], cases=len(cases))

    facts = client["body"] + "\n\n" + "\n\n".join(c["body"] for c in cases)
    # llm() идёт через LLM Gateway платформы: ключа у агента нет, токены и стоимость
    # считает платформа. mock — ответ для демо-режима, когда ключ API не задан.
    brief = llm(
        f"Составь справку о клиенте перед звонком: 5–7 пунктов, по-русски.\n\n{facts}",
        system="Ты помощник отдела продаж. Пиши только то, что есть в материалах.",
        mock=f"**{client['title']}** — стадия: {client['meta'].get('stage')}.\n\n{client['body']}",
    )

    # Вернуть словарь по схеме output из agent.yaml.
    return {"client": client["title"], "brief": brief, "cases": [c["title"] for c in cases]}
