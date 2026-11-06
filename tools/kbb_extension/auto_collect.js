/* Автосбор CarMax: в пятницу утром расширение само открывает поиск каждой площадки (ваши «Favorite auctions»
   с Member Dashboard), ждёт, пока загрузятся все машины, и сохраняет страницу в «Загрузки/Lot Analyzer» — как закладка
   «💾 Сохранить для анализа». Программа берёт файлы из «Загрузок» как обычно.
   — Аукционы понедельника и вторника собираются в пятницу с 7:00; компьютер или Chrome был выключен —
     соберёт при первом включении, пока торги не начались (понедельник / вторник до 6:00).
   — Площадка без машин («Check back for cars»), неполная загрузка или ошибка — ещё попытка через 3 часа.
   — Нужен вход на carmaxauctions.com в этом Chrome; не вошли — в окне программы будет «войдите на CarMax». */
var AUTO_PLACES = {
  mon: ['Sacramento', 'Murrieta', 'Oceanside', 'Roseville', 'El Mirage'],
  tue: ['Las Vegas', 'Chino', 'Fresno', 'Oxnard', 'Fremont', 'Pleasanton']
};
var AUTO_START_HOUR = 7;          // пятница, с этого часа
var AUTO_DEADLINE_HOUR = 6;       // день торгов, до этого часа
var AUTO_RETRY_MS = 3 * 3600 * 1000;
var AUTO_PAGE_MS = 5 * 60 * 1000; // на одну площадку, не дольше
var autoBusy = false;
var autoWaiting = {};             // tabId → функция, которой вкладка отдаст файл

function autoUrl(place) {
  return 'https://www.carmaxauctions.com/search?view=detailed&auctioneventtype=SequentialSale&weblocationnames=' +
    encodeURIComponent(place).replace(/%20/g, '+') + '&sort=makes_model&direction=asc';
}
function autoIso(d) { return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2); }

/* Ближайшие торги, которые пора собирать: { mon: '2026-11-02', tue: '2026-11-03' } — только те, у кого окно сбора открыто. */
function autoTargets(now) {
  var out = {};
  [['mon', 1], ['tue', 2]].forEach(function (pair) {
    var sale = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    sale.setDate(sale.getDate() + ((pair[1] - sale.getDay() + 7) % 7));
    var deadline = new Date(sale.getFullYear(), sale.getMonth(), sale.getDate(), AUTO_DEADLINE_HOUR);
    if (now >= deadline) { sale.setDate(sale.getDate() + 7); deadline.setDate(deadline.getDate() + 7); }
    var start = new Date(sale.getFullYear(), sale.getMonth(), sale.getDate() - ((sale.getDay() + 2) % 7), AUTO_START_HOUR);   // пятница перед торгами
    if (now >= start && now < deadline) { out[pair[0]] = autoIso(sale); }
  });
  return out;
}

function autoState(cb) { chrome.storage.local.get('auto-state', function (got) { cb((got || {})['auto-state'] || {}); }); }
function autoSave(state) { chrome.storage.local.set({ 'auto-state': state }); }

/* Файл страницы: сначала — прямо программе (она кладёт его в «Загрузки/Lot Analyzer»), так он не зависит от загрузок
   Chrome; программа не запущена — через загрузки Chrome, и площадка «собрана», только когда файл правда записан.
   Итог: { ok, path, via, error }. */
function autoDownload(name, html) {
  return fetch('http://127.0.0.1:8765/api/save_page', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LA': '1' },
    body: JSON.stringify({ name: name, html: html }) })
    .then(function (r) { return r.json().catch(function () { return null; }); }, function () { return null; })
    .then(function (got) {
      if (got && got.ok) { return { ok: true, path: got.path, via: 'program' }; }
      return autoChromeDownload(name, html).then(function (res) {
        if (!res.ok && got && got.error) { res.error = 'программа: ' + got.error + '; Chrome: ' + (res.error || ''); }
        return res;
      });
    });
}

/* data: URL — у фона расширения нет Blob-ссылок. Ждём, пока Chrome допишет файл (или скажет, почему не смог). */
function autoChromeDownload(name, html) {
  var bytes = new TextEncoder().encode(html), bin = '';
  for (var i = 0; i < bytes.length; i += 0x8000) { bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000)); }
  return new Promise(function (resolve) {
    chrome.downloads.download({ url: 'data:text/html;charset=utf-8;base64,' + btoa(bin), filename: 'Lot Analyzer/' + name, conflictAction: 'uniquify', saveAs: false },
      function (id) {
        if (chrome.runtime.lastError || !id) { resolve({ ok: false, error: (chrome.runtime.lastError && chrome.runtime.lastError.message) || 'Chrome не начал скачивание' }); return; }
        var settled = false;
        var check = function () {
          chrome.downloads.search({ id: id }, function (items) {
            var it = (items || [])[0];
            if (settled || !it) { return; }
            if (it.state === 'complete') { settled = true; chrome.downloads.onChanged.removeListener(onChange); resolve({ ok: true, path: it.filename, via: 'chrome' }); }
            else if (it.state === 'interrupted') { settled = true; chrome.downloads.onChanged.removeListener(onChange); resolve({ ok: false, path: it.filename, error: 'Chrome: ' + (it.error || 'скачивание прервано') }); }
          });
        };
        var onChange = function (d) { if (d.id === id && d.state) { check(); } };
        chrome.downloads.onChanged.addListener(onChange);
        check();
        setTimeout(function () { if (!settled) { settled = true; chrome.downloads.onChanged.removeListener(onChange); resolve({ ok: false, error: 'Chrome не дописал файл за минуту' }); } }, 60000);
      });
  });
}

/* Одна площадка: вкладка поиска → код закладки → файл. Итог: { status, cars, expected, note }. */
function autoOne(place, focused) {
  return new Promise(function (resolve) {
    var done = false, tabId = null, winId = null, timer = null, listener = null;
    var finish = function (res) {
      if (done) { return; }
      done = true; clearTimeout(timer);
      if (listener) { chrome.tabs.onUpdated.removeListener(listener); }
      if (tabId !== null) { delete autoWaiting[tabId]; }
      if (winId !== null) { chrome.windows.remove(winId, function () { void chrome.runtime.lastError; }); }
      resolve(res);
    };
    chrome.windows.create({ url: autoUrl(place), focused: !!focused, width: 1300, height: 900, type: 'normal' }, function (win) {
      if (chrome.runtime.lastError || !win || !win.tabs || !win.tabs.length) { finish({ status: 'error', note: 'не открылось окно Chrome' }); return; }
      winId = win.id; tabId = win.tabs[0].id;
      timer = setTimeout(function () { finish({ status: 'short', note: 'страница не догрузилась за 5 минут' }); }, AUTO_PAGE_MS);
      autoWaiting[tabId] = function (msg) {
        var m = /expected (\d+), got (\d+)/.exec(msg.diag || '');
        var expected = m ? Number(m[1]) : 0, cars = msg.total || (m ? Number(m[2]) : 0);
        if (!cars) { finish({ status: 'empty', cars: 0, note: 'машин пока нет (Check back for cars)' }); return; }
        autoDownload(msg.name, msg.html).then(function (saved) {
          if (!saved.ok) { finish({ status: 'error', cars: cars, note: 'файл не сохранился — ' + (saved.error || 'причина неизвестна') }); return; }
          finish({ status: expected && cars < expected * 0.95 ? 'short' : 'ok', cars: cars, expected: expected, file: saved.path || msg.name, via: saved.via,
                   note: expected && cars < expected * 0.95 ? 'загружено ' + cars + ' из ' + expected : '' });
        });
      };
      var started = false;
      listener = function (id, info, tab) {
        if (id !== tabId || info.status !== 'complete' || started) { return; }
        if (!/^https:\/\/www\.carmaxauctions\.com\/search/.test(tab.url || '')) { chrome.tabs.onUpdated.removeListener(listener); finish({ status: 'login', note: 'войдите на carmaxauctions.com в Chrome' }); return; }
        started = true;
        chrome.tabs.onUpdated.removeListener(listener);
        setTimeout(function () {                       // сайт дорисовывает список после загрузки
          chrome.scripting.executeScript({ target: { tabId: tabId }, func: function () {
            window.laAutoSave = function (name, html, diag, total) {
              chrome.runtime.sendMessage({ type: 'auto-page', name: name, html: html, diag: diag, total: total });
            };
          } }, function () {
            if (chrome.runtime.lastError) { finish({ status: 'error', note: 'нет доступа к странице CarMax' }); return; }
            chrome.scripting.executeScript({ target: { tabId: tabId }, files: ['save_page.js'] }, function () { void chrome.runtime.lastError; });
          });
        }, 6000);
      };
      chrome.tabs.onUpdated.addListener(listener);
    });
  });
}

/* Собрать то, что пора: все площадки открытых окон сбора, кроме уже собранных (и пустых / неполных — не чаще раза в 3 часа).
   force — кнопка окна программы «Собрать …»: ближайшие торги, даже если сейчас не пятница; only — 'mon' / 'tue' (иначе оба дня). */
function autoRun(force, only) {
  if (autoBusy) { return Promise.resolve(false); }
  var now = new Date(), targets = autoTargets(now);
  if (force) {                                                     // ближайшие понедельник и вторник
    targets = {};
    [['mon', 1], ['tue', 2]].forEach(function (pair) {
      var sale = new Date(now.getFullYear(), now.getMonth(), now.getDate());
      sale.setDate(sale.getDate() + ((pair[1] - sale.getDay() + 7) % 7));
      if (now >= new Date(sale.getFullYear(), sale.getMonth(), sale.getDate(), AUTO_DEADLINE_HOUR)) { sale.setDate(sale.getDate() + 7); }
      if (!only || only === pair[0]) { targets[pair[0]] = autoIso(sale); }
    });
  }
  if (!Object.keys(targets).length) { return Promise.resolve(false); }
  autoBusy = true;
  /* Фон расширения Chrome усыпляет через 30 с тишины — пока идёт сбор, держим его бодрым. */
  var awake = setInterval(function () { chrome.runtime.getPlatformInfo(function () {}); }, 20000);
  return new Promise(function (resolve) {
    autoState(async function (state) {
      for (var day in state) { if (!/^\d{4}-\d\d-\d\d$/.test(day) || day < autoIso(new Date(now.getTime() - 14 * 864e5))) { delete state[day]; } }
      var noLogin = false, collected = false;
      for (var which of ['mon', 'tue']) {
        var sale = targets[which];
        if (!sale || noLogin) { continue; }
        state[sale] = state[sale] || { which: which, places: {} };
        for (var place of AUTO_PLACES[which]) {
          var prev = state[sale].places[place];
          if (!force && prev && (prev.status === 'ok' || Date.now() - (prev.at || 0) < AUTO_RETRY_MS)) { continue; }
          var tries = (prev && prev.tries) || 0;
          var res = await autoOne(place, tries >= 1);                 // вторая попытка — окно на переднем плане
          res.at = Date.now(); res.tries = tries + 1;
          state[sale].places[place] = res;
          if (res.status === 'ok') { collected = true; }
          state.last = { at: Date.now(), place: place, sale: sale };
          autoSave(state);
          if (res.status === 'login') { noLogin = true; break; }
        }
      }
      autoSave(state);
      autoBusy = false;
      clearInterval(awake);
      if (collected) { autoPhotos(); }
      resolve(true);
    });
  });
}

/* После сбора: у машин «без фото» смотрим карточки лотов — список CarMax пишет «Check back for images» дольше,
   чем у самой карточки уже есть фото (carmax_photos.js на вкладке #la-photos; вкладка закроется сама). */
function autoPhotos() {
  chrome.tabs.create({ url: 'https://www.carmaxauctions.com/#la-photos', active: false }, function (tab) {
    if (chrome.runtime.lastError || !tab) { return; }
    var mark = {}; mark['photos-' + tab.id] = 1; chrome.storage.session.set(mark);
  });
}

chrome.runtime.onMessage.addListener(function (msg, sender, reply) {
  if (msg && msg.type === 'auto-page' && sender.tab && autoWaiting[sender.tab.id]) { autoWaiting[sender.tab.id](msg); return false; }
  if (msg && msg.type === 'auto-status') { autoState(function (state) { reply({ state: state, busy: autoBusy, places: AUTO_PLACES }); }); return true; }
  if (msg && msg.type === 'auto-run') { var busy = autoBusy; autoRun(true, msg.which === 'mon' || msg.which === 'tue' ? msg.which : ''); reply({ started: !busy }); return false; }
  return false;
});

chrome.alarms.get('la-auto', function (a) { if (!a) { chrome.alarms.create('la-auto', { delayInMinutes: 1, periodInMinutes: 30 }); } });
chrome.alarms.onAlarm.addListener(function (a) { if (a.name === 'la-auto') { autoRun(false); } });
chrome.runtime.onStartup.addListener(function () { setTimeout(function () { autoRun(false); }, 60000); });
