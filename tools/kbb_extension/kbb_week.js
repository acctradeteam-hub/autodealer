/* «KBB на неделю»: с четверга 18:00 до пятницы 9:00 расширение само берёт настоящий KBB у всех кандидатов недели —
   популярных машин с торгами впереди, без стоп-факторов (рама, титул) и без Major Engine/Transmission
   (Civic и Corolla — все). Кого спрашивать, решает программа (/api/kbb_week).
   — Сначала списки CarMax (автосбор, auto_collect.js: четверг 18:00 и ещё раз пятница 7:00 — новые машины).
   — Потом пачками по 25 машин: отдельное окно Chrome с kbb.com (KBB-автопилот, content.js), закрывается само;
     следующая пачка — после закрытия, проверка каждые 5 минут.
   — К пятнице 9:00 у всех кандидатов должен быть KBB; не успели — доделываем до 12:00, окно программы предупредит.
   — Компьютер и Chrome должны быть включены, программа запущена. Ставок и покупок расширение не делает. */
var KW_DAY = 4;                          // четверг
var KW_START_HOUR = 18;                  // четверг, с этого часа
var KW_DEADLINE_HOUR = 9;                // пятница: к этому часу всё готово
var KW_STOP_HOUR = 12;                   // пятница: не успели к 9:00 — доделываем до полудня
var KW_BATCH = 25;                       // машин на одну вкладку KBB
var KW_TAB_MAX_MS = 60 * 60 * 1000;      // вкладка KBB работает дольше часа — закрываем, берём следующую пачку
var KW_MANUAL_MS = 3 * 3600 * 1000;      // «▶ Взять KBB сейчас» в окне — работает до 3 часов в любой день
var KW_API = 'http://127.0.0.1:8765/api/kbb_week';
var kwBusy = false;

function kwIso(d) { return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2); }

/* Четверг 18:00 этой недели, если сейчас окно «KBB на неделю» (чт 18:00 → пт 12:00), иначе null. */
function kwWeekStart(now) {
  var back = (now.getDay() - KW_DAY + 7) % 7;
  var thu = new Date(now.getFullYear(), now.getMonth(), now.getDate() - back, KW_START_HOUR);
  var stop = new Date(thu.getFullYear(), thu.getMonth(), thu.getDate() + 1, KW_STOP_HOUR);
  return now >= thu && now < stop ? thu : null;
}
function kwDeadline(thu) { return new Date(thu.getFullYear(), thu.getMonth(), thu.getDate() + 1, KW_DEADLINE_HOUR); }

function kwState(cb) { chrome.storage.local.get('kbbweek-state', function (got) { cb((got || {})['kbbweek-state'] || {}); }); }
function kwSave(st) {
  chrome.storage.local.set({ 'kbbweek-state': st });
  var show = { week: st.week, started: st.started || null, batches: st.batches || 0, asked: st.asked || 0, last: st.last || null, finished: st.finished || null,
    note: st.note || '', manual: !!(st.manual_until && Date.now() < st.manual_until), deadline: st.deadline || null, busy: !!st.tab };
  fetch(KW_API, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LA': '1' }, body: JSON.stringify({ action: 'status', state: show }) })
    .catch(function () {});
}
function kwTabAlive(id) { return new Promise(function (ok) { chrome.tabs.get(id, function (t) { void chrome.runtime.lastError; ok(!!t); }); }); }

/* Окно Chrome с kbb.com для пачки: не на переднем плане, закрывается само, когда автопилот закончил. */
function kwOpen(url, carsJson) {
  return new Promise(function (ok) {
    chrome.windows.create({ url: url, focused: false, width: 1200, height: 900, type: 'normal' }, function (win) {
      if (chrome.runtime.lastError || !win || !win.tabs || !win.tabs.length) { ok(null); return; }
      var tab = win.tabs[0], item = {};
      item['cars-' + tab.id] = carsJson;                 // kbb.com может потерять #la=… при переадресации — вкладка спросит сама
      chrome.storage.session.set(item, function () { ok(tab); });
    });
  });
}

/* Один шаг: своя вкладка ещё работает — ждём; идёт автосбор CarMax — ждём; иначе следующая пачка.
   force — кнопка окна «▶ Взять KBB сейчас»: работает в любой день до 3 часов. */
function kwTick(force) {
  if (kwBusy) { return Promise.resolve(false); }
  kwBusy = true;
  return new Promise(function (resolve) {
    kwState(async function (st) {
      var done = function (res) { kwBusy = false; resolve(res); };
      try {
        var now = new Date(), thu = kwWeekStart(now);
        if (force) { st.manual_until = Date.now() + KW_MANUAL_MS; st.finished = null; }
        var manual = !!(st.manual_until && Date.now() < st.manual_until);
        if (!thu && !manual) { done(false); return; }
        var week = thu ? kwIso(thu) : kwIso(now);
        if (st.week !== week) { st = { week: week, manual_until: st.manual_until, tab: st.tab }; }   // новая неделя — счёт с нуля
        st.deadline = thu ? kwDeadline(thu).getTime() : null;
        if (st.tab) {
          var alive = await kwTabAlive(st.tab.id);
          if (alive && Date.now() - st.tab.at < KW_TAB_MAX_MS) { kwSave(st); done(false); return; }
          if (alive) { chrome.tabs.remove(st.tab.id, function () { void chrome.runtime.lastError; }); }
          st.tab = null;
        }
        /* Сначала списки CarMax: автосбор этой недели (чт 18:00, пт 7:00) — без него программа не знает машин. */
        if (typeof autoRun === 'function' && !autoBusy) { await autoRun(false); }
        if (typeof autoBusy !== 'undefined' && autoBusy) { st.note = 'жду автосбор CarMax'; kwSave(st); done(false); return; }
        var res = await fetch(KW_API, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LA': '1' },
          body: JSON.stringify({ action: 'batch', limit: KW_BATCH }) }).then(function (r) { return r.json(); }).catch(function () { return null; });
        if (!res) { st.note = 'программа не отвечает — запустите её'; kwSave(st); done(false); return; }
        if (!res.cars || !res.cars.length) {
          if (!st.finished) { st.finished = Date.now(); }
          st.note = '';
          kwSave(st);
          done(false);
          return;
        }
        var tab = await kwOpen(res.url, res.cars_json || '');
        if (!tab) { st.note = 'не открылось окно Chrome для kbb.com'; kwSave(st); done(false); return; }
        st.started = st.started || Date.now();
        st.finished = null;
        st.tab = { id: tab.id, at: Date.now(), n: res.cars.length };
        st.batches = (st.batches || 0) + 1;
        st.asked = (st.asked || 0) + res.cars.length;
        st.last = Date.now();
        st.note = '';
        kwSave(st);
        done(true);
      } catch (e) {
        st.note = 'ошибка: ' + (e && e.message ? e.message : e);
        kwSave(st);
        done(false);
      }
    });
  });
}

/* Вкладка пачки закрылась (автопилот закончил) — следующая пачка через минуту, не дожидаясь 5-минутной проверки. */
chrome.tabs.onRemoved.addListener(function (id) {
  kwState(function (st) {
    if (st.tab && st.tab.id === id) {
      st.tab = null;
      kwSave(st);                                         // окну: пачка закончилась
      setTimeout(function () { kwTick(false); }, 60000);
    }
  });
});

chrome.runtime.onMessage.addListener(function (msg, sender, reply) {
  if (msg && msg.type === 'kbbweek-run') { var busy = kwBusy; kwTick(true); reply({ started: !busy }); return false; }
  if (msg && msg.type === 'kbbweek-status') { kwState(function (st) { reply({ state: st, busy: kwBusy }); }); return true; }
  return false;
});

chrome.alarms.get('la-kbbweek', function (a) { if (!a) { chrome.alarms.create('la-kbbweek', { delayInMinutes: 2, periodInMinutes: 5 }); } });
chrome.alarms.onAlarm.addListener(function (a) { if (a.name === 'la-kbbweek') { kwTick(false); } });
