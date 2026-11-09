/* Запись торгов CarMax сама — фон расширения.
   По понедельникам в 7:55 (Chrome запущен, вход на carmaxauctions.com есть): отдельное окно Chrome → главная CarMax →
   «Join Auction» → Velocicast → «CHOOSE LANES» — все линии → логгер торгов (velocicast_logger.js — тот же скрипт, что
   в навыке «velocicast-auction-logger») → каждые 15 минут итоги в программу: auction_all_<дата>_<время>.csv в
   «Загрузки/Lot Analyzer», программа берёт их как обычные итоги торгов. Торги закончились (20 минут без событий
   после продаж или 17:00) — последний файл, запись закончена; окно Velocicast остаётся открытым.
   Chrome включили позже 7:55 — запуск сразу, если ещё нет 12:00. Не вышло (нет входа, не нашлась кнопка) — в окне
   программы видно, на каком шаге, а страница сохраняется в данные программы для разбора.
   Ставок программа не делает: нажимаются только «Join Auction», выбор линий и кнопка подтверждения выбора. */
var JOIN_DAYS = [1];                 // дни недели: 1 — понедельник
var JOIN_HOUR = 7, JOIN_MIN = 55;
var JOIN_LATEST_HOUR = 12;           // Chrome включили позже — запускаем, пока нет 12:00
var JOIN_EXPORT_MIN = 15;            // итоги в программу — каждые 15 минут
var JOIN_IDLE_MIN = 20;              // столько минут без событий после продаж — торги закончились
var JOIN_STOP_HOUR = 17;             // и не позже 17:00
var JOIN_STEP_MS = 4 * 60000;        // шаг (Join, линии) не должен тянуться дольше 4 минут
var JOIN_APP = 'http://127.0.0.1:8765';
var JOIN_HOME = 'https://www.carmaxauctions.com/#la-join';
var JOIN_VC = /^https:\/\/[\w.-]*velocicast\.io\//;

function joinIso(d) { return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2); }
function joinHHMM(d) { return ('0' + d.getHours()).slice(-2) + ('0' + d.getMinutes()).slice(-2); }
/* Имя файла итогов: auction_all_<дата>_<время>.csv. Время — не раньше 08:00: по имени программа узнаёт день торгов
   (файл «до 8:00» она считает прошлыми торгами), а запись начинается в 7:55. */
function joinFile(d) {
  var t = joinHHMM(d);
  if ([1, 2].indexOf(d.getDay()) >= 0 && t < '0800') { t = '0800'; }
  return 'auction_all_' + joinIso(d) + '_' + t + '.csv';
}

/* Ближайший запуск: понедельник 7:55 (сегодня, если ещё не наступило). */
function joinNext(now) {
  for (var i = 0; i < 8; i++) {
    var d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + i, JOIN_HOUR, JOIN_MIN);
    if (JOIN_DAYS.indexOf(d.getDay()) >= 0 && d > now) { return d; }
  }
  return null;
}

/* Пора ли запускать сейчас: день торгов, с 7:55 до 12:00, сегодня ещё не запускали. */
function joinDue(now, state) {
  if (state.enabled === false || JOIN_DAYS.indexOf(now.getDay()) < 0) { return false; }
  var start = new Date(now.getFullYear(), now.getMonth(), now.getDate(), JOIN_HOUR, JOIN_MIN);
  return now >= start && now.getHours() < JOIN_LATEST_HOUR && state.date !== joinIso(now);
}

function joinGet(cb) { chrome.storage.local.get('join-state', function (got) { cb((got || {})['join-state'] || { enabled: true }); }); }
function joinPut(state, report) {
  state.at = Date.now();
  var next = joinNext(new Date());
  state.next = next ? next.getTime() : 0;
  chrome.storage.local.set({ 'join-state': state });
  if (report !== false) {
    var body = {};
    ['enabled', 'date', 'step', 'note', 'file', 'started', 'exported', 'next', 'at', 'manual'].forEach(function (k) { body[k] = state[k]; });
    body.stats = state.stats || null;
    fetch(JOIN_APP + '/api/logger_status', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LA': '1' }, body: JSON.stringify(body) })
      .catch(function () {});
  }
}
function joinSchedule() {
  var next = joinNext(new Date());
  if (next) { chrome.alarms.create('la-join', { when: next.getTime() }); }
}

/* Страница, на которой не вышло, — программе (data/logger_page_*.html), чтобы разобрать и поправить. */
function joinSnapshot(step, html) {
  if (!html) { return; }
  fetch(JOIN_APP + '/api/logger_page', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LA': '1' },
    body: JSON.stringify({ step: step, html: String(html).slice(0, 3000000) }) }).catch(function () {});
}

function joinFail(state, step, note, html) {
  state.step = 'error'; state.failed = step; state.note = note;
  joinPut(state);
  joinSnapshot(step, html);
}

/* Старт: отдельное окно на переднем плане (фоновые вкладки Chrome притормаживает), всплывающие окна CarMax
   разрешены — «Join Auction» открывает Velocicast новым окном. */
function joinRun(manual) {
  joinGet(function (state) {
    var now = new Date();
    if (!manual && !joinDue(now, state)) { return; }
    if (['open', 'join', 'lanes', 'logging'].indexOf(state.step) >= 0 && state.date === joinIso(now) && Date.now() - (state.at || 0) < 10 * 60000) { return; }
    try { chrome.contentSettings.popups.set({ primaryPattern: 'https://www.carmaxauctions.com/*', setting: 'allow' }); } catch (e) { /* нет разрешения */ }
    state = { enabled: state.enabled !== false, date: joinIso(now), step: 'open', note: '', manual: !!manual, started: Date.now(),
              file: joinFile(now), stepAt: Date.now() };
    chrome.windows.create({ url: JOIN_HOME, focused: true, type: 'normal', state: 'maximized' }, function (win) {
      if (chrome.runtime.lastError || !win || !win.tabs || !win.tabs.length) { joinFail(state, 'open', 'не открылось окно Chrome'); return; }
      state.win = win.id; state.tab = win.tabs[0].id;
      chrome.tabs.update(state.tab, { autoDiscardable: false }, function () { void chrome.runtime.lastError; });
      joinPut(state);
    });
  });
}

/* Velocicast открылся (новым окном / вкладкой или в той же) после «Join Auction» — это наша вкладка. */
function joinAdopt(tab) {
  joinGet(function (state) {
    if (['join', 'open'].indexOf(state.step) < 0 || state.date !== joinIso(new Date()) || !JOIN_VC.test(tab.url || tab.pendingUrl || '')) { return; }
    if (state.vtab === tab.id) { return; }
    state.vtab = tab.id; state.step = 'lanes'; state.stepAt = Date.now(); state.note = '';
    chrome.tabs.update(tab.id, { autoDiscardable: false }, function () { void chrome.runtime.lastError; });
    joinPut(state);
  });
}
chrome.tabs.onCreated.addListener(function (tab) { if (JOIN_VC.test(tab.pendingUrl || tab.url || '')) { joinAdopt(tab); } });
chrome.tabs.onUpdated.addListener(function (id, info, tab) { if (info.url && JOIN_VC.test(info.url)) { joinAdopt(tab); } });

/* Логгер — в мир страницы Velocicast (он подменяет обработку WebSocket и пишет в window.__AUC). */
function joinInject(tabId, cb) {
  chrome.scripting.executeScript({ target: { tabId: tabId }, world: 'MAIN', files: ['velocicast_logger.js'] }, function (res) {
    var err = chrome.runtime.lastError;
    cb(!err, err ? err.message : ((res && res[0] && res[0].result) || null));
  });
}

/* Итоги из логгера: счётчики и (если пора) CSV — программе. */
function joinPull(tabId, withCsv) {
  return new Promise(function (resolve) {
    chrome.scripting.executeScript({ target: { tabId: tabId }, world: 'MAIN', args: [!!withCsv], func: function (csv) {
      var A = window.__AUC;
      if (!A || !A.stats) { return null; }
      var out = { stats: A.stats(), lanes: document.querySelectorAll('.lane-panel-container').length };
      if (csv && A.combinedCSV) { var c = A.combinedCSV(); out.text = c.text; out.cars = c.nCars; out.bids = c.nBids; }
      return out;
    } }, function (res) {
      if (chrome.runtime.lastError) { resolve({ gone: true, error: chrome.runtime.lastError.message }); return; }
      resolve((res && res[0] && res[0].result) || { missing: true });
    });
  });
}

function joinExport(state, got) {
  return fetch(JOIN_APP + '/api/auction_log', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LA': '1' },
    body: JSON.stringify({ name: state.file, text: got.text }) })
    .then(function (r) { return r.json(); })
    .then(function (a) { if (a && a.ok) { state.exported = Date.now(); state.note = ''; } else { state.note = 'файл не записан: ' + ((a && a.error) || 'программа не ответила'); } })
    .catch(function () { state.note = 'программа не запущена — итоги заберу, когда она будет открыта'; });
}

/* Раз в 5 минут: пора ли стартовать; как идёт запись; не закончились ли торги. */
function joinTick() {
  joinGet(async function (state) {
    var now = new Date();
    if (joinDue(now, state)) { joinRun(false); return; }
    if (['open', 'join', 'lanes'].indexOf(state.step) >= 0 && Date.now() - (state.stepAt || 0) > JOIN_STEP_MS) {
      joinFail(state, state.step, state.step === 'lanes' ? 'Velocicast открылся, но линии не открылись — выберите их сами («CHOOSE LANES»), запись начнётся сама'
                                                         : 'не дождался Velocicast после «Join Auction» — проверьте окно CarMax');
      return;
    }
    if (state.step !== 'logging' || !state.vtab) { return; }
    var due = Date.now() - (state.exported || state.loggingAt || 0) >= JOIN_EXPORT_MIN * 60000;
    var got = await joinPull(state.vtab, due);
    if (got.gone) {
      state.step = 'lost'; state.note = 'вкладку Velocicast закрыли — записано до ' + new Date(state.exported || Date.now()).toLocaleTimeString('ru-RU');
      joinPut(state); return;
    }
    if (got.missing) {                                       // страницу перезагрузили — логгер заново (данные он восстановит сам)
      joinInject(state.vtab, function () {});
      return;
    }
    state.stats = got.stats; state.lanes = got.lanes;
    if (got.text) { await joinExport(state, got); }
    var last = got.stats && got.stats.last ? Date.parse(got.stats.last) : 0;
    var idle = last && Date.now() - last > JOIN_IDLE_MIN * 60000 && got.stats.sold > 0;
    if (idle || now.getHours() >= JOIN_STOP_HOUR) {
      var fin = await joinPull(state.vtab, true);
      if (fin && fin.text) { await joinExport(state, fin); state.stats = fin.stats; }
      state.step = 'done';
    }
    joinPut(state);
  });
}

chrome.runtime.onMessage.addListener(function (msg, sender, reply) {
  if (!msg) { return false; }
  /* Окно программы */
  if (msg.type === 'join-status') { joinGet(function (state) { var n = joinNext(new Date()); state.next = n ? n.getTime() : 0; reply({ state: state }); }); return true; }
  if (msg.type === 'join-run') { joinRun(true); reply({ started: true }); return false; }
  if (msg.type === 'join-enable') {
    joinGet(function (state) { state.enabled = !!msg.on; joinPut(state); reply({ state: state }); });
    return true;
  }
  var tabId = sender.tab && sender.tab.id;
  /* Главная CarMax: это окно записи торгов? */
  if (msg.type === 'join-tab') {
    joinGet(function (state) { reply({ yes: state.step === 'open' && state.tab === tabId && state.date === joinIso(new Date()) }); });
    return true;
  }
  if (msg.type === 'join-goto') {                            // на главной кнопки нет — та же проверка на check-in
    joinGet(function (state) {
      if (state.tab !== tabId || state.step !== 'open' || !/^https:\/\/www\.carmaxauctions\.com\//.test(msg.href || '')) { return; }
      state.stepAt = Date.now(); state.note = 'на главной нет «Join Auction» — ищу на check-in';
      joinPut(state);
      chrome.tabs.update(tabId, { url: msg.href });
    });
    return false;
  }
  if (msg.type === 'join-href' || msg.type === 'join-clicked') {
    joinGet(function (state) {
      if (state.tab !== tabId) { return; }
      state.step = 'join'; state.stepAt = Date.now(); state.note = msg.text || '';
      joinPut(state);
      if (msg.type === 'join-href' && /^https:\/\//.test(msg.href || '')) { chrome.tabs.update(tabId, { url: msg.href }); }
    });
    return false;
  }
  if (msg.type === 'vc-wait') {                              // линии ещё не открылись — Velocicast пробует каждую минуту
    joinGet(function (state) { if (state.vtab === tabId && state.step === 'lanes') { state.stepAt = Date.now(); state.note = msg.note || ''; joinPut(state); } });
    return false;
  }
  if (msg.type === 'join-fail') {
    joinGet(function (state) { if (state.tab === tabId || state.vtab === tabId) { joinFail(state, msg.step || state.step, msg.note || 'не вышло', msg.html); } });
    return false;
  }
  /* Velocicast: наша ли вкладка; линии открыты — запускаем логгер. */
  if (msg.type === 'vc-ask') {
    joinGet(function (state) {
      var today = state.date === joinIso(new Date());
      var ours = today && (state.vtab === tabId || (['join', 'open'].indexOf(state.step) >= 0 && (state.tab === tabId || state.win === (sender.tab && sender.tab.windowId))));
      if (ours && state.vtab !== tabId) { state.vtab = tabId; state.step = 'lanes'; state.stepAt = Date.now(); joinPut(state); }
      var logging = ours && state.step === 'logging';
      if (logging) { joinInject(tabId, function () {}); }      // страницу перезагрузили — логгер заново, запись восстановится
      reply({ auto: ours && ['lanes', 'join', 'open'].indexOf(state.step) >= 0, logging: logging || (today && state.step === 'logging' && state.vtab === tabId) });
    });
    return true;
  }
  if (msg.type === 'vc-lanes' || msg.type === 'vc-manual-start') {
    joinGet(function (state) {
      if (msg.type === 'vc-manual-start' && (state.step !== 'logging' || state.vtab !== tabId)) {
        // Velocicast открыт вручную — запись по кнопке на странице: тот же логгер, те же файлы в программу.
        var now = new Date();
        state = { enabled: state.enabled !== false, date: joinIso(now), manual: true, started: Date.now(), vtab: tabId,
                  file: joinFile(now) };
      } else if (state.vtab !== tabId) { reply({ ok: false }); return; }
      joinInject(tabId, function (ok, res) {
        if (!ok) { joinFail(state, 'logger', 'логгер не запустился: ' + res); reply({ ok: false }); return; }
        state.step = 'logging'; state.loggingAt = Date.now(); state.note = ''; state.lanes = msg.lanes || 0; state.stats = res && res.lots !== undefined ? res : state.stats;
        joinPut(state);
        reply({ ok: true });
      });
    });
    return true;
  }
  return false;
});

chrome.alarms.get('la-join-tick', function (a) { if (!a) { chrome.alarms.create('la-join-tick', { delayInMinutes: 1, periodInMinutes: 5 }); } });
joinSchedule();
chrome.alarms.onAlarm.addListener(function (a) {
  if (a.name === 'la-join') { joinRun(false); joinSchedule(); }
  if (a.name === 'la-join-tick') { joinTick(); }
});
