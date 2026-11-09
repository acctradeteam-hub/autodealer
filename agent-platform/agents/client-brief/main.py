from agent_sdk import agent, llm


@agent
def run(input, ctx):
    found = ctx.get("clients", name=input["client"])
    if not found:
        return {"client": input["client"], "brief": "Клиент не найден в базе знаний.", "cases": []}
    client = found[0]
    cases = ctx.search(client["meta"].get("industry", ""), section="cases", limit=2)
    facts = client["body"] + "\n\n" + "\n\n".join(c["body"] for c in cases)
    brief = llm(
        f"Составь справку о клиенте перед звонком: 5–7 пунктов, по-русски.\n\n{facts}",
        system="Ты помощник отдела продаж. Пиши только то, что есть в материалах.",
        mock=f"**{client['title']}** — стадия: {client['meta'].get('stage')}.\n\n{client['body']}",
    )
    return {"client": client["title"], "brief": brief, "cases": [c["title"] for c in cases]}
