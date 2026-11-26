/* «✍ Заметки фоном» — фон расширения. Заметки (KBB с датой, ставка MP, голос, замечания аукциона) вписываются в Notes
   на CarMax без вашей открытой страницы: вкладка в фоне по очереди открывает карточки лотов
   (carmaxauctions.com/vehicledetail/<номер>), carmax_notes.js на карточке вписывает заметку и жмёт Save, отвечает сюда,
   результат уходит в программу (/api/notes_done) — второй раз ту же заметку не пишем. В конце вкладка закрывается.
   — Какие машины: программа (/api/notes_queue) — CarMax, торги впереди, заметка поменялась с последней записи.
   — Сама: каждые 30 минут с 7 до 22 (costs.json → notes_bg), если есть что вписать; кнопка «✍ записать сейчас» в окне
     и с телефона.
   — Три карточки подряд с самого начала не открылись (CarMax просит войти) — стоп, в окне: «войдите на CarMax».
   Только поле Notes и его кнопка Save: ставок расширение не ставит, «Place bid» не нажимает. */
var NOTES_APP = 'http://127.0.0.1:8765';
var NOTES_WAIT_MS = 60000;          // на одну карточку: загрузка + ожидание заметки + Save (вкладка в фоне — таймеры медленнее)
var NOTES_LAST = 'notes-bg-last';    // storage.local: когда был последний проход (для «каждые 30 минут»)
var notesJob = null;
var notesStarting = 0;

function notesSave() {
  try { chrome.storage.session.set({ 'notes-job': notesJob ? JSON.stringify(notesJob) : '' }); } catch (e) { /* нет session */ }
}

function notesReport(state, extra) {
  var j = notesJob || {};
  var body = { state: state, total: j.total || 0, done: (j.written || 0) + (j.same || 0) + (j.failed || 0), written: j.written || 0, same: j.same || 0,
               failed: j.failed || 0, why: j.why || '', current: j.current ? (j.current.car || j.current.vin) : '' };
  Object.keys(extra || {}).forEach(function (k) { body[k] = extra[k]; });
  fetch(NOTES_APP + '/api/notes_bg_status', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LA': '1' }, body: JSON.stringify(body) })
    .catch(function () {});
}

function notesFinish(state, extra) {
  var j = notesJob;
  if (!j) { return; }
  clearTimeout(j.timer);
  clearInterval(j.awake);
  j.current = null;
  notesReport(state, extra);
  notesJob = null;
  notesSave();
  chrome.tabs.remove(j.tab, function () { void chrome.runtime.lastError; });
}

function notesNext() {
  var j = notesJob;
  if (!j) { return; }
  if (!j.queue.length) { notesFinish('done'); return; }
  var car = j.queue.shift();
  j.current = car;
  j.n += 1;
  notesSave();
  notesReport('running');
  chrome.tabs.update(j.tab, { url: 'https://www.carmaxauctions.com/vehicledetail/' + car.stock + '#la-notes' }, function () {
    if (chrome.runtime.lastError) { notesFinish('closed', { note: 'вкладку заметок закрыли' }); }
  });
  clearTimeout(j.timer);
  j.timer = setTimeout(function () { notesResult({ vin: car.vin, ok: false, why: 'timeout' }); }, NOTES_WAIT_MS);
}

function notesResult(res) {
  var j = notesJob;
  if (!j || !j.current || String(res.vin || '').toUpperCase() !== String(j.current.vin).toUpperCase()) { return; }
  clearTimeout(j.timer);
  var car = j.current;
  j.current = null;
  if (res.ok) {
    j.misses = 0;
    if (res.same) { j.same += 1; } else { j.written += 1; }
  } else {
    j.failed += 1;
    j.misses += 1;
  }
  /* «Не вышло» из-за входа на CarMax — не попытка этой машины: программу не трогаем, просто стоп ниже. */
  var login = !res.ok && (res.why === 'redirect' || res.why === 'timeout') && j.written + j.same === 0;
  if (!login) {
    fetch(NOTES_APP + '/api/notes_done', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LA': '1' },
      body: JSON.stringify({ cars: [{ vin: car.vin, note: car.note, ok: !!res.ok, why: res.why || '' }] }) }).catch(function () {});
  }
  notesSave();
  if (j.misses >= 3 && j.written + j.same === 0) {          // с самого начала ни одна карточка не открылась — вход на CarMax
    notesFinish('blocked', { url: res.url || '', title: res.title || '', why: res.why || '' });
    return;
  }
  setTimeout(notesNext, Math.max(500, (j.gap || 2) * 1000));
}

/* Старт: why — «кнопка», «сам», «с телефона». Вкладка — новая, в фоне. */
function notesBgStart(why) {
  if ((notesJob && Date.now() - notesJob.started < 60 * 60000) || Date.now() - notesStarting < 30000) { return 'busy'; }
  notesStarting = Date.now();
  fetch(NOTES_APP + '/api/notes_queue', { cache: 'no-store' })
    .then(function (r) { return r.json(); })
    .then(function (a) {
      var cars = (a && a.cars) || [];
      var cfg = (a && a.cfg) || {};
      chrome.storage.local.set({ 'notes-bg-last': Date.now() });
      if (!cars.length || cfg.enabled === false) {
        notesStarting = 0;
        notesReport('done', { total: 0, why: why || '' });
        return;
      }
      chrome.tabs.create({ url: 'about:blank', active: false }, function (tab) {
        notesStarting = 0;
        if (chrome.runtime.lastError || !tab) { notesReport('error', { note: 'не удалось открыть вкладку' }); return; }
        notesJob = { tab: tab.id, queue: cars.slice(), total: cars.length, n: 0, written: 0, same: 0, failed: 0, misses: 0,
                     gap: Number(cfg.gap_s) || 2, why: why || '', started: Date.now() };
        /* Фон расширения засыпает через 30 с без событий — пока идёт запись, не даём. */
        notesJob.awake = setInterval(function () { chrome.runtime.getPlatformInfo(function () {}); }, 20000);
        notesNext();
      });
    })
    .catch(function () { notesStarting = 0; notesReport('error', { note: 'программа не отвечает' }); });
  return 'started';
}

/* Фон проснулся заново посреди записи (Chrome его усыплял) — продолжаем с той же машины. */
try {
  chrome.storage.session.get('notes-job', function (got) {
    var saved = (got || {})['notes-job'];
    if (!saved || notesJob) { return; }
    try { notesJob = JSON.parse(saved); } catch (e) { return; }
    if (!notesJob || Date.now() - notesJob.started > 60 * 60000) { notesJob = null; notesSave(); return; }
    notesJob.awake = setInterval(function () { chrome.runtime.getPlatformInfo(function () {}); }, 20000);
    if (notesJob.current) { notesJob.queue.unshift(notesJob.current); notesJob.current = null; }
    notesNext();
  });
} catch (e) { /* нет session */ }

chrome.runtime.onMessage.addListener(function (msg, sender, reply) {
  if (!msg) { return false; }
  /* Окно программы (кнопка «✍ записать сейчас»). */
  if (msg.type === 'notes-bg') {
    reply({ started: notesBgStart(msg.why || 'кнопка') });
    return false;
  }
  /* Карточка лота спрашивает: это вкладка заметок и какую машину ждём? */
  if (msg.type === 'notes-job') {
    var j = notesJob;
    var mine = j && sender.tab && sender.tab.id === j.tab && j.current;
    reply(mine ? { vin: j.current.vin, note: j.current.note, known: j.current.known || '', stock: j.current.stock, n: j.n, total: j.total } : null);
    return false;
  }
  if (msg.type === 'notes-job-done' && notesJob && sender.tab && sender.tab.id === notesJob.tab) {
    notesResult(msg);
    return false;
  }
  /* Заметки, вписанные на открытой вами странице CarMax, — в учёт программы: фоном их второй раз не пишем. */
  if (msg.type === 'notes-done' && sender.tab && /^https:\/\/www\.carmaxauctions\.com\//.test(sender.tab.url || '')) {
    fetch(NOTES_APP + '/api/notes_done', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LA': '1' },
      body: JSON.stringify({ cars: (msg.cars || []).slice(0, 500) }) }).catch(function () {});
    return false;
  }
  return false;
});

/* Сама: раз в 10 минут проверяем — пора ли (каждые every_min минут, с from_hour до to_hour). Настройки — от программы. */
chrome.alarms.get('la-notes-bg', function (a) { if (!a) { chrome.alarms.create('la-notes-bg', { delayInMinutes: 5, periodInMinutes: 10 }); } });
chrome.alarms.onAlarm.addListener(function (a) {
  if (a.name !== 'la-notes-bg' || notesJob) { return; }
  fetch(NOTES_APP + '/api/notes_bg', { cache: 'no-store' })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (!d || d.enabled === false || !d.pending) { return; }
      chrome.storage.local.get(NOTES_LAST, function (got) {
        var cfg = d.cfg || { every_min: 30, from_hour: 7, to_hour: 22 };
        var h = new Date().getHours();
        var last = Number((got || {})[NOTES_LAST] || 0);
        if (h < (cfg.from_hour || 7) || h >= (cfg.to_hour || 22) || Date.now() - last < (cfg.every_min || 30) * 60000) { return; }
        notesBgStart('сам');
      });
    })
    .catch(function () {});
});
