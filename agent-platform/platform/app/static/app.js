// Интерфейс платформы: без сборки и фреймворков. Всё, что он показывает,
// берётся из того же API, которым пользуются программы сотрудников.

const app = document.getElementById("app");
const personSelect = document.getElementById("person");
let people = [];
let me = null;

// --- утилиты ---------------------------------------------------------------
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

async function api(path, options = {}) {
  const resp = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${me.token}`, ...(options.headers || {}) },
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok && !(data && data.status)) throw new Error(data.detail || data.error || resp.statusText);
  return data;
}

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const usd = (v) => (v ? `$${Number(v).toFixed(4)}` : "$0");
const when = (iso) => (iso ? new Date(iso).toLocaleString("ru-RU") : "");
const roleTitle = (r, roles) => (roles && roles[r] ? roles[r].title : r);

const STATUS = {
  ok: ["ok", "готово"], running: ["", "выполняется"], denied: ["bad", "отказ: роль"],
  invalid_input: ["warn", "вход не по схеме"], invalid_output: ["bad", "выход не по схеме"], error: ["bad", "ошибка"],
};
const statusBadge = (s) => h("span", { class: `badge ${(STATUS[s] || ["plain"])[0]}` }, (STATUS[s] || [s, s])[1]);
const REVIEW = { draft: "черновик", approved: "одобрено", sent: "отправлено" };

// Простой markdown: заголовки, списки, таблицы, жирный, курсив, код.
function markdown(src) {
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/(^|[^*])\*([^*]+)\*/g, "$1<i>$2</i>");
  const out = [];
  const lines = String(src || "").split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\|.*\|\s*$/.test(line)) {
      const rows = [];
      while (i < lines.length && /^\|.*\|\s*$/.test(lines[i])) rows.push(lines[i++]);
      i--;
      const cells = (r) => r.trim().slice(1, -1).split("|").map((c) => c.trim());
      const body = rows.filter((r) => !/^\|[\s|:-]+\|\s*$/.test(r));
      out.push("<table>" + body.map((r, n) => "<tr>" + cells(r).map((c) =>
        n === 0 ? `<th>${inline(c)}</th>` : `<td>${inline(c)}</td>`).join("") + "</tr>").join("") + "</table>");
    } else if (/^#{1,6}\s/.test(line)) {
      const level = line.match(/^#+/)[0].length;
      out.push(`<h${level}>${inline(line.replace(/^#+\s/, ""))}</h${level}>`);
    } else if (/^\s*([-*]|\d+\.)\s/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s/.test(lines[i])) items.push(lines[i++].replace(/^\s*([-*]|\d+\.)\s/, ""));
      i--;
      out.push("<ul>" + items.map((t) => `<li>${inline(t)}</li>`).join("") + "</ul>");
    } else if (/^---+\s*$/.test(line)) out.push("<hr>");
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  return h("div", { class: "md", html: out.join("") });
}

// --- навигация -------------------------------------------------------------
const routes = [
  [/^#?\/?$/, overview],
  [/^#\/agent\/([\w-]+)$/, agentPage],
  [/^#\/run\/([\w-]+)$/, runPage],
  [/^#\/runs$/, journal],
  [/^#\/knowledge\/([\w-]+)$/, knowledgePage],
];

async function render() {
  for (const [re, fn] of routes) {
    const m = location.hash.match(re);
    if (m) {
      try { await fn(...m.slice(1).map(decodeURIComponent)); }
      catch (e) { app.replaceChildren(h("div", { class: "error" }, e.message)); }
      return;
    }
  }
  app.replaceChildren(h("p", {}, "Страница не найдена. ", h("a", { href: "#/" }, "На главную")));
}

// --- главная: три блока ------------------------------------------------------
async function overview() {
  const data = await api("/api/overview");
  document.getElementById("llm-mode").replaceWith(llmBadge(data.llm));
  const role = data.person.role;

  const context = h("section", { class: "panel" },
    h("div", { class: "panel-head" }, h("h2", {}, "Контекст"),
      h("button", { class: "secondary small", onclick: syncKnowledge }, "Синхронизировать")),
    h("p", { class: "muted small" }, "Знания компании: файлы в knowledge/<раздел>/. Каждая запись видна только ролям из её поля access."),
    h("table", {},
      h("tr", {}, h("th", {}, "Раздел"), h("th", {}, "Видно"), h("th", {}, "Владельцы"), h("th", {}, "Обновлено")),
      data.sections.map((s) => h("tr", {},
        h("td", {}, s.visible ? h("a", { href: `#/knowledge/${s.section}` }, s.section) : h("span", { class: "muted" }, s.section)),
        h("td", {}, `${s.visible} из ${s.total}`, s.total > s.visible ? h("div", {}, h("span", { class: "badge warn" }, `скрыто: ${s.total - s.visible}`)) : null),
        h("td", { class: "small" }, s.owners.join(", ")),
        h("td", { class: "small" }, s.updated)))));

  const roles = Object.keys(data.roles);
  const rolesBlock = h("section", { class: "panel" },
    h("h2", {}, "Роли"),
    h("p", { class: "muted small" }, "Сколько записей каждого раздела видит роль. Ваша роль подсвечена."),
    h("table", { class: "matrix" },
      h("tr", {}, h("th", {}, ""), roles.map((r) => h("th", { class: r === role ? "me" : "", title: roleTitle(r, data.roles) }, r))),
      Object.entries(data.access_matrix).map(([section, cell]) => h("tr", {},
        h("td", {}, section),
        roles.map((r) => h("td", { class: (r === role ? "me " : "") + (cell[r] ? "" : "cell-none") },
          cell[r] ? `${cell[r]}/${cell._total}` : "—"))))),
    h("table", { class: "small", style: "margin-top:12px" },
      roles.map((r) => h("tr", { class: r === role ? "me" : "" },
        h("td", {}, h("b", {}, roleTitle(r, data.roles))), h("td", { class: "muted" }, data.roles[r].description),
        h("td", {}, people.filter((p) => p.role === r).map((p) => p.name).join(", "))))));

  const agents = h("section", { class: "panel" },
    h("h2", {}, "Агенты"),
    h("p", { class: "muted small" }, "Каждый агент — отдельная программа в agents/<имя>/ с контрактом «вход → выход»."),
    data.agents.map((a) => agentCard(a, data.roles)));

  app.replaceChildren(...[
    h("h1", {}, `Добрый день, ${data.person.name}`),
    h("p", { class: "lead" }, `Вы смотрите как ${data.person.position.toLowerCase()} (роль «${roleTitle(role, data.roles)}»). `,
      "Платформа держит границы: кто что видит и кто кого запускает. Агенты держат логику."),
    data.errors.length ? h("div", { class: "error" }, "Ошибки конфигурации: ", data.errors.join("; ")) : null,
    h("div", { class: "grid3" }, context, rolesBlock, agents)].filter(Boolean));
}

function llmBadge(llm) {
  return h("span", { id: "llm-mode", class: `badge ${llm.mock ? "warn" : "ok"}`,
    title: llm.mock ? "ANTHROPIC_API_KEY не задан: агенты получают демо-ответы, стоимость — оценка" : "" },
    llm.mock ? "LLM: демо-режим" : `LLM: ${llm.default_model}`);
}

function fieldsOf(schema) {
  const req = new Set(schema.required || []);
  return Object.entries(schema.properties || {}).map(([k, p]) => (req.has(k) ? k : `${k}?`)).join(", ");
}

function agentCard(a, roles) {
  return h("div", { class: "agent" },
    h("div", { class: "panel-head" }, h("h3", {}, a.name), h("span", { class: "num" }, `v${a.version} · ${a.owner}`)),
    h("div", { class: "small" }, a.description),
    h("dl", {},
      h("dt", {}, "Вход"), h("dd", {}, h("code", {}, fieldsOf(a.input))),
      h("dt", {}, "Выход"), h("dd", {}, h("code", {}, fieldsOf(a.output))),
      h("dt", {}, "Контекст"), h("dd", {}, h("div", { class: "chips" }, a.context.map((c) => h("span", { class: "badge plain" }, c)))),
      h("dt", {}, "Запускают"), h("dd", {}, h("div", { class: "chips" }, a.roles.map((r) => h("span", { class: "badge" }, roleTitle(r, roles)))))),
    h("div", { class: "foot" },
      h("span", { class: "small muted" }, a.stats ? `${a.stats.runs} запусков · ${usd(a.stats.cost_usd)}` : "ещё не запускался"),
      a.can_run ? h("button", { onclick: () => (location.hash = `#/agent/${a.name}`) }, "Запустить")
        : h("span", { class: "badge bad" }, "недоступно вашей роли")));
}

async function syncKnowledge() {
  const r = await api("/api/knowledge/sync", { method: "POST" });
  alert(`Загружено записей: ${r.loaded}, удалено: ${r.removed}` + (r.errors.length ? `\nОшибки:\n${r.errors.join("\n")}` : ""));
  render();
}

// --- раздел знаний -----------------------------------------------------------
async function knowledgePage(section) {
  const data = await api(`/api/knowledge/${section}`);
  app.replaceChildren(
    h("p", {}, h("a", { href: "#/" }, "← Обзор")),
    h("h1", {}, `Раздел ${section}`),
    h("p", { class: "lead" }, `Видно вашей роли: ${data.records.length}. Скрыто ролью: ${data.hidden_by_role}.`),
    data.records.map((r) => h("div", { class: "panel" },
      h("div", { class: "panel-head" }, h("h3", {}, r.title), h("span", { class: "num" }, `${r.id}.md · ${r.owner} · ${r.updated}`)),
      h("div", { class: "chips" }, r.access.map((a) => h("span", { class: "badge plain" }, a))),
      markdown(r.body))));
}

// --- запуск агента: форма из input-схемы -------------------------------------
async function agentPage(name) {
  const data = await api("/api/overview");
  const agent = data.agents.find((a) => a.name === name);
  if (!agent) throw new Error(`Агент ${name} не найден`);
  const form = h("form", { class: "form" });
  const schema = agent.input;
  const required = new Set(schema.required || []);
  const inputs = {};
  for (const [key, prop] of Object.entries(schema.properties || {})) {
    let control;
    if (prop.enum) control = h("select", {}, prop.enum.map((v) => h("option", { value: v, selected: v === prop.default }, v)));
    else if (prop["x-ui"] === "textarea") control = h("textarea", { minlength: prop.minLength });
    else if (prop.type === "number" || prop.type === "integer") control = h("input", { type: "number", min: prop.minimum, step: prop.type === "integer" ? 1 : "any" });
    else if (prop.type === "boolean") control = h("input", { type: "checkbox" });
    else control = h("input", { type: prop.format === "date" ? "date" : "text" });
    if (required.has(key) && prop.type !== "boolean") control.required = true;
    inputs[key] = { control, prop };
    form.append(h("label", {}, `${prop.title || key}${required.has(key) ? "" : " (необязательно)"}`,
      prop.description ? h("span", { class: "hint" }, prop.description) : null, control));
  }
  const result = h("div");
  const button = h("button", { type: "submit" }, "Запустить");
  form.append(h("div", { class: "row" }, button, h("span", { class: "small muted" }, "Платформа проверит роль и вход до вызова агента")));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = {};
    for (const [key, { control, prop }] of Object.entries(inputs)) {
      if (prop.type === "boolean") input[key] = control.checked;
      else if (control.value === "") continue;
      else input[key] = prop.type === "number" || prop.type === "integer" ? Number(control.value) : control.value;
    }
    button.disabled = true;
    result.replaceChildren(h("p", { class: "muted" }, "Агент работает…"));
    try {
      const run = await api(`/api/agents/${name}/run`, { method: "POST", body: JSON.stringify({ input }) });
      if (run.id) location.hash = `#/run/${run.id}`;
      else result.replaceChildren(h("div", { class: "error" }, run.error || "Ошибка"));
    } catch (err) {
      result.replaceChildren(h("div", { class: "error" }, err.message));
    } finally { button.disabled = false; }
  });
  app.replaceChildren(
    h("p", {}, h("a", { href: "#/" }, "← Обзор")),
    h("h1", {}, agent.name), h("p", { class: "lead" }, agent.description),
    h("div", { class: "panel" }, h("h2", {}, "Вход"), form, result));
}

// --- трассировка запуска -----------------------------------------------------
async function runPage(id) {
  const run = await api(`/api/runs/${id}`);
  const contextEvents = run.events.filter((e) => e.kind === "context" || e.kind === "denied");
  const llmEvents = run.events.filter((e) => e.kind === "llm");
  const hidden = contextEvents.reduce((s, e) => s + (e.data.hidden_by_role || 0), 0);
  const given = new Set(contextEvents.flatMap((e) => (e.data.records || []).map((r) => r.id)));
  const mock = llmEvents.some((e) => e.data.mock);

  const stageInput = h("div", { class: "stage" },
    h("div", { class: "stage-head" }, h("h3", {}, "1. Вход"),
      h("span", { class: "small muted" }, `${run.person} · роль ${run.role} · ${when(run.started_at)}`)),
    h("pre", {}, JSON.stringify(run.input, null, 2)));

  const stageContext = h("div", { class: "stage" },
    h("div", { class: "stage-head" }, h("h3", {}, "2. Контекст"),
      h("span", { class: "badge" }, `выдано записей: ${given.size}`),
      h("span", { class: `badge ${hidden ? "warn" : "plain"}` }, `скрыто ролью: ${hidden}`)),
    contextEvents.length ? contextEvents.map(contextEvent)
      : h("p", { class: "muted small" }, run.status === "denied" || run.status === "invalid_input"
        ? "Агент не вызывался: платформа отказала до запуска." : "Агент не запрашивал знания."));

  const steps = run.steps || [];
  const stageSteps = h("div", { class: "stage" },
    h("div", { class: "stage-head" }, h("h3", {}, "3. Шаги агента"),
      h("span", { class: "small muted" }, `LLM-вызовов: ${llmEvents.length}${mock ? " (демо-режим)" : ""}`)),
    steps.length ? h("table", { class: "small" }, steps.map((s, i) => h("tr", {},
      h("td", {}, i + 1), h("td", {}, h("b", {}, s.title)),
      h("td", {}, h("code", {}, JSON.stringify(s.details))), h("td", { class: "muted" }, s.ms !== undefined ? `${s.ms} мс` : ""))))
      : h("p", { class: "muted small" }, "Шагов нет."));

  const stageOutput = h("div", { class: "stage" },
    h("div", { class: "stage-head" }, h("h3", {}, "4. Выход"), statusBadge(run.status),
      run.status === "ok" ? h("span", { class: "badge plain" }, REVIEW[run.review_status]) : null),
    run.error ? h("div", { class: "error" }, run.error) : null,
    run.status === "ok" ? reviewBlock(run) : run.output ? h("pre", {}, JSON.stringify(run.output, null, 2)) : null);

  app.replaceChildren(
    h("p", {}, h("a", { href: "#/runs" }, "← Журнал")),
    h("h1", {}, `Запуск ${run.agent}`),
    h("div", { class: "summary" },
      h("span", {}, "Статус ", statusBadge(run.status)),
      h("span", {}, "Время ", h("b", {}, run.duration_ms !== null ? `${(run.duration_ms / 1000).toFixed(2)} с` : "—")),
      h("span", {}, "Токены ", h("b", {}, `${run.input_tokens} → ${run.output_tokens}`)),
      h("span", {}, "Стоимость ", h("b", {}, usd(run.cost_usd)), mock ? " (оценка, демо-режим)" : ""),
      h("span", {}, "Версия ", h("b", {}, run.agent_version))),
    h("div", { class: "chain" }, stageInput, stageContext, stageSteps, stageOutput));
}

function contextEvent(e) {
  const d = e.data;
  if (e.kind === "denied") return h("div", { class: "ev denied" }, h("b", {}, "Отказ: "), d.reason);
  const what = d.call === "search" ? `поиск «${d.query}» в ${d.sections.join(", ")}`
    : `раздел ${d.section}${Object.keys(d.filters || {}).length ? " " + JSON.stringify(d.filters) : ""}`;
  return h("div", { class: "ev" },
    h("div", {}, h("b", {}, what), " → выдано ", d.records.length,
      d.hidden_by_role ? h("span", { class: "badge warn", style: "margin-left:6px" }, `скрыто ролью: ${d.hidden_by_role}`) : null),
    d.records.length ? h("ul", { class: "small" }, d.records.map((r) => h("li", {}, r.title, " ", h("span", { class: "muted" }, r.id)))) : null);
}

// Результат: просмотр, правка, статус. Правка хранится рядом с оригиналом.
function reviewBlock(run) {
  const current = run.edited_output || run.output;
  const hasMd = typeof current.markdown === "string" || typeof current.brief === "string";
  const textKey = typeof current.markdown === "string" ? "markdown" : "brief";
  const view = h("div");
  const tabs = h("div", { class: "tabs" });
  const show = (mode) => {
    [...tabs.children].forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
    if (mode === "result") view.replaceChildren(hasMd ? markdown(current[textKey]) : h("pre", {}, JSON.stringify(current, null, 2)));
    if (mode === "json") view.replaceChildren(h("pre", {}, JSON.stringify(current, null, 2)));
    if (mode === "original") view.replaceChildren(hasMd ? markdown(run.output[textKey]) : h("pre", {}, JSON.stringify(run.output, null, 2)));
    if (mode === "edit") view.replaceChildren(editor());
  };
  const tab = (mode, label) => h("button", { type: "button", "data-mode": mode, onclick: () => show(mode) }, label);
  tabs.append(tab("result", run.edited_output ? "Результат (с правками)" : "Результат"), tab("json", "JSON"));
  if (run.edited_output) tabs.append(tab("original", "Оригинал агента"));
  tabs.append(tab("edit", "Править"));

  function editor() {
    const area = h("textarea", { style: "min-height:360px" }, hasMd ? current[textKey] : JSON.stringify(current, null, 2));
    const msg = h("div");
    return h("div", {}, area, h("div", { class: "row", style: "margin-top:8px" },
      h("button", { onclick: async () => {
        try {
          const edited = hasMd ? { ...current, [textKey]: area.value } : JSON.parse(area.value);
          await api(`/api/runs/${run.id}`, { method: "PATCH", body: JSON.stringify({ edited_output: edited }) });
          runPage(run.id);
        } catch (e) { msg.replaceChildren(h("div", { class: "error" }, e.message)); }
      } }, "Сохранить правку"), h("span", { class: "small muted" }, "Оригинал агента не меняется")), msg);
  }

  const statusSelect = h("select", {}, Object.entries(REVIEW).map(([k, v]) => h("option", { value: k, selected: k === run.review_status }, v)));
  statusSelect.addEventListener("change", async () => {
    await api(`/api/runs/${run.id}`, { method: "PATCH", body: JSON.stringify({ review_status: statusSelect.value }) });
    runPage(run.id);
  });
  const reviewed = run.edited_by ? h("span", { class: "small muted" }, `изменено: ${run.edited_by}, ${when(run.edited_at)}`) : null;
  setTimeout(() => show("result"));
  return h("div", {}, h("div", { class: "row", style: "justify-content:space-between" }, tabs,
    h("label", { class: "row small" }, "Статус", statusSelect, reviewed)), view);
}

// --- журнал ------------------------------------------------------------------
async function journal() {
  const list = await api("/api/runs");
  app.replaceChildren(
    h("h1", {}, "Журнал запусков"),
    h("p", { class: "lead" }, me.role === "ceo" ? "Все запуски компании." : "Ваши запуски."),
    h("div", { class: "panel" }, list.length ? h("table", {},
      h("tr", {}, ["Когда", "Агент", "Кто", "Статус", "Ревью", "Время", "Стоимость"].map((t) => h("th", {}, t))),
      list.map((r) => h("tr", {},
        h("td", {}, h("a", { href: `#/run/${r.id}` }, when(r.started_at))),
        h("td", {}, r.agent), h("td", {}, `${r.person} (${r.role})`), h("td", {}, statusBadge(r.status)),
        h("td", {}, r.status === "ok" ? REVIEW[r.review_status] : ""),
        h("td", {}, r.duration_ms !== null ? `${(r.duration_ms / 1000).toFixed(2)} с` : ""),
        h("td", {}, usd(r.cost_usd)))))
      : h("p", { class: "muted" }, "Запусков пока нет.")));
}

// --- старт -------------------------------------------------------------------
async function start() {
  people = await (await fetch("/api/people")).json();
  let saved = null;
  try { saved = localStorage.getItem("viewAs"); } catch (e) { /* без localStorage тоже работает */ }
  me = people.find((p) => p.id === saved) || people[0];
  personSelect.replaceChildren(...people.map((p) => h("option", { value: p.id, selected: p.id === me.id }, `${p.name} — ${p.role}`)));
  personSelect.addEventListener("change", () => {
    me = people.find((p) => p.id === personSelect.value);
    try { localStorage.setItem("viewAs", me.id); } catch (e) { /* без localStorage тоже работает */ }
    if (/^#\/run\//.test(location.hash)) location.hash = "#/runs";
    render();
  });
  window.addEventListener("hashchange", render);
  render();
}
start();
