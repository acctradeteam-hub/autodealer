/* «Аукцион: заметки»: список машин на осмотр (voice/list.json) и заметки к ним (voice/notes.json)
   в вашем закрытом репозитории GitHub — тот же обмен, что у программы Lot Analyzer на компьютере. */
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const money = v => v ? "$" + Number(v).toLocaleString("en-US") : "—";
const TAGS = [["Дефекта нет", "good"], ["Дефект есть", "badc"], ["Беру", "good"], ["Не беру", "badc"], ["Кузов / краска", "badc"], ["Шины", "badc"], ["Салон", "badc"]];
const BRANCH = "main", LIST = "voice/list.json", NOTES = "voice/notes.json";
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
};
let cars = store.get("vn-cars", []), made = store.get("vn-made", ""), place = store.get("vn-place", "");
let notes = store.get("vn-notes", {}), dirty = store.get("vn-dirty", false), sendTimer = null, sending = false;
const hhmm = t => new Date(t).toLocaleTimeString("ru-RU", {hour: "2-digit", minute: "2-digit"});

$("repo").value = store.get("vn-repo", $("repo").value);
$("token").value = store.get("vn-token", "");
$("savecfg").addEventListener("click", () => {
  store.set("vn-repo", $("repo").value.trim()); store.set("vn-token", $("token").value.trim());
  $("cfgstate").textContent = "Сохранено."; $("cfg").open = false; loadList();
});
if (!store.get("vn-token", "")) $("cfg").open = true;

/* GitHub REST: файл репозитория (текст + sha) и запись файла. */
function b64ToText(b64) { const bin = atob(b64.replace(/\s/g, "")); return new TextDecoder().decode(Uint8Array.from(bin, c => c.charCodeAt(0))); }
function textToB64(text) { const bytes = new TextEncoder().encode(text); let bin = ""; for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000)); return btoa(bin); }
async function gh(method, path, body) {
  const repo = $("repo").value.trim(), token = store.get("vn-token", "");
  if (!token) throw {code: "no_token"};
  const r = await fetch(`https://api.github.com/repos/${repo}/contents/${path}` + (method === "GET" ? `?ref=${BRANCH}&t=${Date.now()}` : ""), {
    method, headers: {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
    body: body ? JSON.stringify(body) : undefined, cache: "no-store"});
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw {code: r.status, message: data.message || ""};
  return data;
}
async function readFile(path) {
  try { const d = await gh("GET", path); return {sha: d.sha, text: b64ToText(d.content || "")}; }
  catch (e) { if (e.code === 404) return {sha: "", text: ""}; throw e; }
}
function why(e) {
  if (e && e.code === "no_token") return "Вставьте токен GitHub в «Настройки GitHub».";
  if (e && (e.code === 401 || e.code === 403)) return "GitHub не принял токен: проверьте его и доступ к репозиторию.";
  if (e && e.code === 404) return "Репозиторий не найден: проверьте имя в настройках.";
  if (e instanceof TypeError || (e && e.name === "TypeError")) return "Нет интернета — заметки сохранены в телефоне, отправятся позже.";
  return "GitHub ответил ошибкой" + (e && e.code ? " " + e.code : "") + ".";
}

async function loadList() {
  $("liststate").textContent = "Загружаю список машин…";
  try {
    const f = await readFile(LIST);
    if (!f.text) { $("emptywhy").textContent = "Списка ещё нет: в программе нажмите «📱 На телефон»."; }
    else {
      const data = JSON.parse(f.text);
      cars = Array.isArray(data.cars) ? data.cars : []; made = data.made || "";
      store.set("vn-cars", cars); store.set("vn-made", made); $("emptywhy").textContent = "";
    }
    await pullNotes();
  } catch (e) { $("emptywhy").textContent = why(e); }
  render();
  if (dirty) send(false);
}

/* Заметки, отправленные раньше (в т.ч. с другого устройства), — к своим: новее побеждает. */
async function pullNotes() {
  const f = await readFile(NOTES);
  if (!f.text) return f.sha;
  const remote = (JSON.parse(f.text).notes) || {};
  for (const [vin, n] of Object.entries(remote)) if (!notes[vin] || (n.at || 0) > (notes[vin].at || 0)) notes[vin] = n;
  store.set("vn-notes", notes);
  return f.sha;
}

const hasNote = vin => { const n = notes[vin]; return !!(n && ((n.text || "").trim() || (n.tags || []).length)); };
function stateLine() {
  const done = cars.filter(c => hasNote(c.vin)).length;
  $("liststate").innerHTML = cars.length ? `<b>${cars.length}</b> машин · с заметкой <b>${done}</b>` + (made ? ` · список от ${esc(made)}` : "") : "Список пуст.";
}
function render() {
  const places = [...new Set(cars.map(c => c.place).filter(Boolean))];
  if (place && !places.includes(place)) place = "";
  $("places").innerHTML = (places.length > 1 ? [["", "Все"]].concat(places.map(p => [p, p.replace(/^CarMax /, "")])) : [])
    .map(([p, t]) => `<button type="button" class="chip${p === place ? " on" : ""}" data-place="${esc(p)}">${esc(t)}</button>`).join("");
  const q = $("q").value.trim().toLowerCase();
  const shown = cars.filter(c => (!place || c.place === place) && (!q || [c.lot, c.car, (c.vin || "").slice(-6)].join(" ").toLowerCase().includes(q)));
  $("empty").hidden = cars.length > 0;
  stateLine();
  $("list").innerHTML = shown.map(c => {
    const n = notes[c.vin] || {}, tags = n.tags || [];
    const mine = c.mine ? money(c.mine) + (c.mine_defect ? " / " + money(c.mine_defect) : "") : "—";
    return `<article class="car${hasNote(c.vin) ? " done" : ""}" data-vin="${esc(c.vin)}">
      <div class="head"><div class="lot">${esc(c.lot || "—")}</div>
        <div class="name">${esc(c.car)}<small>${c.miles ? Number(c.miles).toLocaleString("en-US") + " миль · " : ""}…${esc((c.vin || "").slice(-6))}${c.place ? " · " + esc(c.place.replace(/^CarMax /, "")) : ""}</small>
        ${c.no_pics ? '<span class="nopics">NO PICTURES</span>' : ""}</div></div>
      <div class="nums">
        <div class="num"><span>KBB</span><b>${c.kbb ? money(c.kbb) : c.kbb_est ? "≈" + money(c.kbb_est) : "—"}</b></div>
        <div class="num"><span>Моя оценка</span><b>${mine}</b></div>
        <div class="num"><span>Средняя</span><b>${money(c.market)}</b></div>
        <div class="num"><span>Ставка</span><b>${money(c.mp)}</b></div>
      </div>
      ${(c.notes || []).length || (c.own || []).length ? `<div class="prog">${(c.notes || []).map(x => `<div>${esc(x)}</div>`).join("")}${(c.own || []).map(x => `<div>Мои: ${esc(x)}</div>`).join("")}</div>` : ""}
      <div class="tags">${TAGS.map(([t, k]) => `<button type="button" class="tag ${k}${tags.includes(t) ? " on" : ""}" data-tag="${esc(t)}" aria-pressed="${tags.includes(t)}">${esc(t)}</button>`).join("")}</div>
      <div class="say"><button type="button" class="mic" data-mic="${esc(c.vin)}" aria-label="Надиктовать заметку">🎤</button>
        <textarea id="n-${esc(c.vin)}" data-vin="${esc(c.vin)}" placeholder="Нажмите 🎤 и говорите, или пишите здесь">${esc(n.text || "")}</textarea></div>
      <div class="saved" id="s-${esc(c.vin)}">${n.at ? "сохранено " + hhmm(n.at) : ""}</div>
    </article>`;
  }).join("");
}

function carOf(vin) { return cars.find(c => c.vin === vin) || {}; }
function saveNote(vin, patch) {
  const c = carOf(vin);
  notes[vin] = {...(notes[vin] || {}), ...patch, at: Date.now(), lot: c.lot || "", car: c.car || "", place: c.place || ""};
  store.set("vn-notes", notes); dirty = true; store.set("vn-dirty", true);
  const s = $("s-" + vin); if (s) s.textContent = "сохранено " + hhmm(Date.now());
  const card = document.querySelector(`article[data-vin="${CSS.escape(vin)}"]`); if (card) card.classList.toggle("done", hasNote(vin));
  stateLine();
  clearTimeout(sendTimer); sendTimer = setTimeout(() => send(false), 6000);   // в программу — после паузы
}

async function send(byHand) {
  if (sending || (!dirty && !byHand)) return;
  sending = true; $("sync").textContent = "Отправляю в программу…";
  try {
    const sha = await pullNotes();                     // свежие с GitHub — не затираем заметки с другого устройства
    const out = {};
    for (const [vin, n] of Object.entries(notes)) if (hasNote(vin)) out[vin] = {text: n.text || "", tags: n.tags || [], lot: n.lot || "", car: n.car || "", at: n.at || 0};
    const body = {message: `voice: заметки с аукциона (${Object.keys(out).length})`, branch: BRANCH,
                  content: textToB64(JSON.stringify({updated: new Date().toISOString(), notes: out}, null, 1))};
    if (sha) body.sha = sha;
    await gh("PUT", NOTES, body);
    dirty = false; store.set("vn-dirty", false);
    $("sync").textContent = `Отправлено ${Object.keys(out).length} в ${hhmm(Date.now())} — в программе через 1–2 мин.`;
  } catch (e) {
    $("sync").textContent = "Не отправлено: " + why(e);
  } finally { sending = false; }
}

/* Голос: Android распознаёт речь и возвращает текст сюда — дописываем к заметке машины. */
window.onVoice = (vin, text) => {
  const el = $("n-" + vin), old = el ? el.value.trim() : ((notes[vin] || {}).text || "");
  const value = old ? old + (/[.!?]$/.test(old) ? " " : ". ") + text : text;
  if (el) el.value = value;
  saveNote(vin, {text: value});
};
window.onVoiceError = msg => { $("sync").textContent = msg; };

$("list").addEventListener("input", e => { const t = e.target; if (t.tagName === "TEXTAREA") saveNote(t.dataset.vin, {text: t.value}); });
$("list").addEventListener("click", e => {
  const mic = e.target.closest("button[data-mic]");
  if (mic) { if (window.Native) window.Native.voice(mic.dataset.mic); else $("sync").textContent = "Голос работает в приложении на телефоне."; return; }
  const b = e.target.closest("button[data-tag]"); if (!b) return;
  const vin = b.closest("article").dataset.vin, tag = b.dataset.tag;
  const tags = new Set((notes[vin] || {}).tags || []);
  tags.has(tag) ? tags.delete(tag) : tags.add(tag);
  for (const [a, z] of [["Дефекта нет", "Дефект есть"], ["Беру", "Не беру"]]) { if (tag === a) tags.delete(z); if (tag === z) tags.delete(a); }
  saveNote(vin, {tags: [...tags]});
  b.closest("article").querySelectorAll("button[data-tag]").forEach(x => { const on = tags.has(x.dataset.tag); x.classList.toggle("on", on); x.setAttribute("aria-pressed", on); });
});
$("places").addEventListener("click", e => { const b = e.target.closest("button[data-place]"); if (!b) return; place = b.dataset.place; store.set("vn-place", place); render(); });
$("q").addEventListener("input", render);
$("reload").addEventListener("click", loadList);
$("send").addEventListener("click", () => send(true));
window.addEventListener("online", () => { if (dirty) send(false); });

render();
loadList();
