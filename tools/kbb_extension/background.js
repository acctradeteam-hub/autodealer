/* Фон расширения «Lot Analyzer KBB»:
   — открывает ссылки окна программы соседней вкладкой в фоне (вы остаётесь в окне); для вкладки kbb.com
     запоминает машины автопилота — вкладка спросит их сама;
   — закрывает вкладку kbb.com, когда автопилот закончил (просит сама вкладка);
   — берёт у окна программы (только 127.0.0.1:8765, ваш компьютер) тексты заметок для CarMax и Manheim. */
importScripts('auto_collect.js', 'photo_check.js', 'auction_join.js', 'kbb_week.js', 'remote_cmd.js', 'carfax_auto.js');   // + Carfax сам (машинам с KBB)   // + команды с телефона   // автосбор CarMax (чт 18:00, пт 7:00); «📷 Проверить фото»; запись торгов по понедельникам; KBB на неделю (чт 18:00 → пт 9:00)

var favQueue = [];
var favTimer = null;
function flushFavorites() {
  var batch = favQueue.splice(0, favQueue.length);
  if (!batch.length) { return; }
  fetch('http://127.0.0.1:8765/api/favorite', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cars: batch.map(function (b) { return b.car; }) }) })
    .then(function (r) { return r.json(); })
    .then(function (answer) {
      if (answer.url) {
        var tab = batch[0].tab;
        chrome.tabs.create({ url: answer.url, active: false, index: tab.index + 1, openerTabId: tab.id }, function (t) {
          if (t) { var item = {}; item['cars-' + t.id] = answer.url.split('#la=')[1] || ''; chrome.storage.session.set(item); }
        });
      }
      batch.forEach(function (b) { try { b.reply({ ok: true, queued: !!answer.need_kbb }); } catch (e) { /* вкладку закрыли */ } });
    })
    .catch(function () { batch.forEach(function (b) { try { b.reply({ ok: false }); } catch (e) { /* вкладку закрыли */ } }); });
}

chrome.runtime.onMessage.addListener(function (msg, sender, reply) {
  if (msg && msg.type === 'open-bg' && sender.tab && /^http:\/\/127\.0\.0\.1:8765\//.test(sender.url || '') && /^https:\/\//.test(msg.url || '')) {
    chrome.tabs.create({ url: msg.url, active: false, index: sender.tab.index + 1, openerTabId: sender.tab.id }, function (tab) {
      if (msg.cars && tab) { var item = {}; item['cars-' + tab.id] = msg.cars; chrome.storage.session.set(item); }
      /* Вкладка сбора владельцев: CarMax может переадресовать и потерять #la-owners — помним её сами. */
      if (tab && /#la-owners/.test(msg.url)) { var own = {}; own['owners-' + tab.id] = 1; chrome.storage.session.set(own); }
      if (tab && /#la-photos/.test(msg.url)) { var ph = {}; ph['photos-' + tab.id] = 1; chrome.storage.session.set(ph); }
    });
    return false;
  }
  if (msg && msg.type === 'la-cars' && sender.tab) {
    var key = 'cars-' + sender.tab.id;
    chrome.storage.session.get(key, function (got) { chrome.storage.session.remove(key); reply({ cars: (got || {})[key] || '' }); });
    return true;
  }
  if (msg && msg.type === 'owners' && sender.tab && /^https:\/\/www\.carmaxauctions\.com\//.test(sender.tab.url || '')) {
    fetch('http://127.0.0.1:8765/api/owners', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cars: msg.cars || [] }) })
      .then(function (r) { return r.json(); }).then(function (a) { reply(a); }).catch(function () { reply(null); });
    return true;
  }
  if (msg && msg.type === 'owners-needed') {
    fetch('http://127.0.0.1:8765/api/owners_needed', { cache: 'no-store' })
      .then(function (r) { return r.json(); }).then(function (a) { reply(a); }).catch(function () { reply({ vins: [] }); });
    return true;
  }
  if (msg && msg.type === 'photos-needed') {
    fetch('http://127.0.0.1:8765/api/photos_needed', { cache: 'no-store' })
      .then(function (r) { return r.json(); }).then(function (a) { reply(a); }).catch(function () { reply({ cars: [] }); });
    return true;
  }
  if (msg && msg.type === 'photos-tab' && sender.tab) {
    chrome.storage.session.get('photos-' + sender.tab.id, function (got) { reply({ yes: !!(got || {})['photos-' + sender.tab.id] }); });
    return true;
  }
  if (msg && msg.type === 'owners-tab' && sender.tab) {
    chrome.storage.session.get('owners-' + sender.tab.id, function (got) { reply({ yes: !!(got || {})['owners-' + sender.tab.id] }); });
    return true;
  }
  /* Вкладку сбора владельцев закрывает она сама, когда закончила, — только ту, что открыли для этого. */
  if (msg && msg.type === 'close' && sender.tab && /^https:\/\/www\.carmaxauctions\.com\//.test(sender.tab.url || '')) {
    var key = 'owners-' + sender.tab.id, pkey = 'photos-' + sender.tab.id;
    chrome.storage.session.get([key, pkey], function (got) {
      if ((got || {})[key] || (got || {})[pkey]) { chrome.storage.session.remove([key, pkey]); chrome.tabs.remove(sender.tab.id); }
    });
    return false;
  }
  if (msg && msg.type === 'close' && sender.tab && /^https:\/\/www\.kbb\.com\//.test(sender.tab.url || '')) {
    chrome.tabs.remove(sender.tab.id);
    return false;
  }
  if (msg && msg.type === 'notes-report' && sender.tab && /^https:\/\/(www\.carmaxauctions\.com|search\.manheim\.com|www\.manheim\.com)\//.test(sender.tab.url || '')) {
    fetch('http://127.0.0.1:8765/api/notes_report', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(msg.report || {}) })
      .catch(function () {});
    return false;
  }
  if (msg && msg.type === 'favorite' && sender.tab && /^https:\/\/www\.carmaxauctions\.com\//.test(sender.tab.url || '')) {
    /* Звёздочки за пару секунд — одной пачкой: программа запоминает машины и даёт адрес KBB-автопилота для тех,
       у кого KBB ещё нет; вкладка kbb.com открывается в фоне одна на всю пачку. */
    favQueue.push({ car: msg.car, reply: reply, tab: sender.tab });
    clearTimeout(favTimer);
    favTimer = setTimeout(flushFavorites, 2500);
    return true;
  }
  if (msg && msg.type === 'unfavorite' && sender.tab && /^https:\/\/www\.carmaxauctions\.com\//.test(sender.tab.url || '')) {
    fetch('http://127.0.0.1:8765/api/unfavorite', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cars: [msg.car || {}] }) })
      .then(function (r) { return r.json(); })
      .then(function (a) { reply({ ok: !!(a && a.removed) }); })
      .catch(function () { reply({ ok: false }); });
    return true;
  }
  /* Отчёт Carfax (Carfax Online, vin-reports-pro.com, carfax.com) — в программу; ответ — вкладке (заказ закрывает её). */
  if (msg && msg.type === 'carfax' && sender.tab && /^https:\/\/(www\.)?(vin-reports-pro|carfax|carfaxonline|carfaxfordealers)\.com\//.test(sender.url || sender.tab.url || '')) {
    fetch('http://127.0.0.1:8765/api/carfax', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ vin: msg.vin, url: msg.url, text: msg.text }) })
      .then(function (r) { return r.json(); })
      .then(function (got) { if (got && got.ok && typeof cfoSignedIn === 'function') { cfoSignedIn(); } reply(got); }, function () { reply({ ok: false }); });
    return true;
  }
  /* Вкладка Carfax Online спрашивает: её открыли по кнопке «📄 Carfax»? Какой VIN? */
  if (msg && msg.type === 'cfo-job' && sender.tab) {
    chrome.storage.session.get(['cfo-' + sender.tab.id, 'cfo-auto-' + sender.tab.id], function (got) {
      got = got || {};
      reply({ vin: got['cfo-' + sender.tab.id] || '', auto: !!got['cfo-auto-' + sender.tab.id] });
    });
    return true;
  }
  /* Фото на карточке лота CarMax — в программу: машина больше не «без фото», если CarMax их добавил. */
  if (msg && msg.type === 'photos' && sender.tab && /^https:\/\/www\.carmaxauctions\.com\//.test(sender.tab.url || '')) {
    fetch('http://127.0.0.1:8765/api/photos', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cars: msg.cars || [] }) })
      .catch(function () {});
    return false;
  }
  /* Ваш текст в Notes на CarMax — в программу (окно показывает его сразу). */
  if (msg && msg.type === 'site-notes' && sender.tab && /^https:\/\/www\.carmaxauctions\.com\//.test(sender.tab.url || '')) {
    fetch('http://127.0.0.1:8765/api/site_notes', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cars: msg.cars || [] }) })
      .catch(function () {});
    return false;
  }
  if (msg && msg.type === 'notes') {
    fetch('http://127.0.0.1:8765/api/notes?ext=' + chrome.runtime.getManifest().version, { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (data) { reply({ ok: true, notes: data }); })
      .catch(function (e) { reply({ ok: false, error: String(e) }); });
    return true;
  }
  return false;
});

/* После установки / обновления (⟳) расширения уже открытое окно программы его «не видит», пока вкладку не обновить.
   Подключаемся к нему сами, чтобы ссылки сразу открывались в фоне. */
chrome.runtime.onInstalled.addListener(function () {
  chrome.tabs.query({ url: 'http://127.0.0.1:8765/*' }, function (tabs) {
    (tabs || []).forEach(function (tab) {
      chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ['app_bridge.js'] }, function () { void chrome.runtime.lastError; });
    });
  });
});

/* Carfax: кнопка «📄 Carfax» в окне → вкладка vin-reports-pro.com/pro в фоне (VIN — в адресе, заказ делает vrp_order.js);
   готовый PDF от vrp_order.js → программе. Ход заказа — в окно программы (/api/carfax_status). */
function laCarfaxStatus(vin, status, note) {
  fetch('http://127.0.0.1:8765/api/carfax_status', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LA': '1' },
    body: JSON.stringify({ vin: vin, status: status, note: note || '' }) }).catch(function () {});
}
/* Carfax по VIN: кнопка «📄 Carfax» в окне на Mac или с телефона (remote_cmd.js) и «Carfax сам» (carfax_auto.js). Отчёт — на
   Carfax Online (carfaxonline.com/vhr/<VIN>) с вашим входом в этом Chrome; carfax_online.js отдаёт текст программе.
   — Вкладка Carfax — всегда ОДНА: пока она открыта, новые нажатия ждут в очереди (cfo-queue), «Carfax сам» не заказывает.
   — Отчёт пришёл или не вышел — вкладку закрываем мы (не дожидаясь страницы), следующая — через 3 с.
   — Сайт просит вход / код по телефону — вкладка входа остаётся одна, очередь стоит, программа на паузе («Carfax: нужен вход»);
     войдёте — пауза снимается сама. Страница за минуту не ответила (чужой адрес входа) — тоже вход. 4 минуты без отчёта — закрываем. */
var CFO_REPORT = 'https://www.carfaxonline.com/vhr/';
var CFO_KEY = 'cfo-open';              // storage.session: {tab, vin, auto, at, since, seen, state: 'open' | 'login'}
var CFO_QUEUE = 'cfo-queue';           // storage.session: VIN, заказанные кнопкой, пока открыта другая вкладка Carfax
var CFO_LOGIN = 'cfo-login';           // storage.local: когда сайт попросил вход (очередь кнопок ждёт входа)
var CFO_TABS = ['https://www.carfaxonline.com/*', 'https://carfaxonline.com/*', 'https://auth.carfax.com/*'];
var cfoChain = Promise.resolve();
function cfoSerial(fn) { var next = cfoChain.then(fn); cfoChain = next.catch(function () {}); return next; }   // по очереди: две вкладки разом не откроются
function cfoGet(key, area) { return new Promise(function (ok) { chrome.storage[area || 'session'].get(key, function (got) { void chrome.runtime.lastError; ok((got || {})[key]); }); }); }
function cfoPut(key, value, area) {
  return new Promise(function (ok) {
    if (value === null || value === undefined) { chrome.storage[area || 'session'].remove(key, function () { void chrome.runtime.lastError; ok(); }); return; }
    var item = {}; item[key] = value; chrome.storage[area || 'session'].set(item, function () { void chrome.runtime.lastError; ok(); });
  });
}
function cfoTab(id) { return new Promise(function (ok) { chrome.tabs.get(id, function (tab) { ok(chrome.runtime.lastError ? null : tab); }); }); }
function cfoClose(id) { chrome.tabs.remove(id, function () { void chrome.runtime.lastError; }); }
/* Открытая расширением вкладка Carfax (закрытую вручную — забываем). */
async function cfoCurrent() {
  var cur = await cfoGet(CFO_KEY);
  if (cur && cur.tab && await cfoTab(cur.tab)) { return cur; }
  if (cur) { await cfoPut(CFO_KEY, null); }
  return null;
}
function laCarfaxOrder(vin, auto) {             // auto — «Carfax сам» (carfax_auto.js)
  vin = String(vin || '').toUpperCase();
  if (!/^[A-HJ-NPR-Z0-9]{17}$/.test(vin)) { return Promise.resolve(); }
  return cfoSerial(async function () {
    var cur = await cfoCurrent();
    if (cur) {                                   // вкладка Carfax уже открыта — вторую не открываем
      if (auto || cur.vin === vin) { return; }
      var queue = (await cfoGet(CFO_QUEUE)) || [];
      if (queue.indexOf(vin) < 0) { queue.push(vin); await cfoPut(CFO_QUEUE, queue); }
      laCarfaxStatus(vin, cur.state === 'login' ? 'wait' : 'queued', '');   // wait — ждёт вашего входа на Carfax
      return;
    }
    var tab = await new Promise(function (ok) { chrome.tabs.create({ url: CFO_REPORT + vin, active: false }, function (t) { void chrome.runtime.lastError; ok(t); }); });
    if (!tab) { return; }
    var item = {};
    item['cfo-' + tab.id] = vin;
    item['cfo-auto-' + tab.id] = !!auto;
    item[CFO_KEY] = { tab: tab.id, vin: vin, auto: !!auto, at: Date.now(), since: Date.now(), seen: 0, state: 'open' };
    await new Promise(function (ok) { chrome.storage.session.set(item, ok); });
    laCarfaxStatus(vin, 'sent');
  });
}
/* Следующий по кнопке (если Carfax не просит вход), иначе — «Carfax сам». */
function cfoAfter() {
  setTimeout(function () {
    cfoNextQueued().then(function (opened) { if (!opened && typeof laCarfaxAutoDone === 'function') { laCarfaxAutoDone(); } });
  }, 3000);
}
async function cfoNextQueued() {
  if (await cfoGet(CFO_LOGIN, 'local')) { return false; }
  if (await cfoCurrent()) { return true; }
  var queue = (await cfoGet(CFO_QUEUE)) || [];
  if (!queue.length) { return false; }
  var vin = queue.shift();
  await cfoPut(CFO_QUEUE, queue);
  await laCarfaxOrder(vin, false);
  return true;
}
/* Вкладка Carfax сообщила: открылась / просит вход / готово / не вышло. */
function cfoTabEvent(tabId, state, note) {
  return cfoSerial(async function () {
    var cur = await cfoGet(CFO_KEY);
    if (!cur || cur.tab !== tabId) { return; }
    if (state === 'opened' || state === 'login') {
      if (state === 'login') { await cfoPut(CFO_LOGIN, Date.now(), 'local'); }
      if (cur.state !== (state === 'login' ? 'login' : 'open')) { cur.since = Date.now(); }   // после входа — новые 4 минуты на отчёт
      cur.state = state === 'login' ? 'login' : 'open';
      cur.seen = Date.now();
      await cfoPut(CFO_KEY, cur);
      return;
    }
    if (state === 'done' || state === 'error') {
      if (state === 'done') { await cfoPut(CFO_LOGIN, null, 'local'); }
      await cfoPut(CFO_KEY, null);
      setTimeout(function () { cfoClose(tabId); }, 1500);
      cfoAfter();
    }
  });
}
/* Вы вошли на Carfax Online (видна ваша страница / пришёл отчёт): пауза снимается, очередь идёт дальше. */
function cfoSignedIn() {
  fetch('http://127.0.0.1:8765/api/carfax_status', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LA': '1' },
    body: JSON.stringify({ signed_in: true }) }).catch(function () {});
  cfoGet(CFO_LOGIN, 'local').then(function (was) {
    if (was) { cfoPut(CFO_LOGIN, null, 'local').then(cfoAfter); }
  });
}
/* Раз в минуту (будильник «Carfax сам»): вкладка без ответа минуту — вход на чужом адресе; 4 минуты без отчёта — закрыть. */
function cfoWatch() {
  return cfoSerial(async function () {
    var cur = await cfoGet(CFO_KEY);
    if (!cur) { return; }
    if (!await cfoTab(cur.tab)) { await cfoPut(CFO_KEY, null); return; }
    var age = Date.now() - (cur.since || cur.at);
    if (cur.state === 'open' && !cur.seen && age > 60000) {
      cur.state = 'login'; cur.since = Date.now(); cur.seen = Date.now();
      await cfoPut(CFO_KEY, cur);
      await cfoPut(CFO_LOGIN, Date.now(), 'local');
      laCarfaxStatus(cur.vin, 'login', 'Carfax не открыл отчёт — похоже, просит войти; войдите на Carfax Online в Chrome на Mac');
      return;
    }
    if (cur.state === 'open' && age > 4 * 60000) {
      await cfoPut(CFO_KEY, null);
      cfoClose(cur.tab);
      laCarfaxStatus(cur.vin, 'error', 'отчёт не открылся за 4 минуты');
      cfoAfter();
    }
  });
}
/* Вкладку Carfax закрыли вручную: отчёт не дождались — дальше по очереди; вкладку входа — ждём, пока войдёте. */
chrome.tabs.onRemoved.addListener(function (tabId) {
  cfoSerial(async function () {
    var cur = await cfoGet(CFO_KEY);
    if (!cur || cur.tab !== tabId) { return; }
    await cfoPut(CFO_KEY, null);
    if (cur.state === 'open') { laCarfaxStatus(cur.vin, 'error', 'вкладку Carfax закрыли'); cfoAfter(); }
  });
});
/* «🔑 Открыть Carfax» в окне: показать вкладку входа (или открыть Carfax Online) — войти можете только вы. */
async function cfoShowLogin() {
  var cur = await cfoCurrent();
  if (cur) {
    chrome.tabs.update(cur.tab, { active: true }, function (tab) {
      void chrome.runtime.lastError;
      if (tab) { chrome.windows.update(tab.windowId, { focused: true }, function () { void chrome.runtime.lastError; }); }
    });
    return;
  }
  chrome.tabs.create({ url: 'https://www.carfaxonline.com/', active: true }, function () { void chrome.runtime.lastError; });
}
/* Обновили расширение / запустили Chrome: зависшие вкладки отчётов и входа Carfax (прежние версии открывали их пачками) —
   закрыть. Вкладку, которую вы сейчас смотрите, не трогаем. */
function cfoCleanup() {
  cfoGet(CFO_KEY).then(function (cur) { chrome.tabs.query({ url: CFO_TABS }, function (tabs) {
    void chrome.runtime.lastError;
    var ids = (tabs || []).filter(function (t) { return !t.active && !(cur && cur.tab === t.id) && /\/vhrs?\/|auth\.carfax\.com|\/(login|accountprotection)/i.test(t.url || ''); })
      .map(function (t) { return t.id; });
    if (ids.length) { chrome.tabs.remove(ids, function () { void chrome.runtime.lastError; }); }
  }); });
}
chrome.runtime.onInstalled.addListener(cfoCleanup);
chrome.runtime.onStartup.addListener(cfoCleanup);
chrome.runtime.onMessage.addListener(function (msg, sender, reply) {
  if (!msg) { return; }
  if (msg.type === 'carfax-order' && /^[A-HJ-NPR-Z0-9]{17}$/.test(String(msg.vin || '').toUpperCase())) {
    laCarfaxOrder(msg.vin);
    return;
  }
  /* Сайт просит войти: логин и пароль — из программы (хранятся только на этом компьютере). */
  if (msg.type === 'vrp-login') {
    fetch('http://127.0.0.1:8765/api/vrp_login', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LA': '1' }, body: '{}' })
      .then(function (r) { return r.json(); }).then(reply, function () { reply({}); });
    return true;
  }
  /* Поле VIN не нашлось: снимок страницы заказа — в программу (data/carfax), чтобы настроить заказ точно под неё. */
  if (msg.type === 'vrp-page') {
    fetch('http://127.0.0.1:8765/api/vrp_page', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LA': '1' }, body: JSON.stringify({ html: msg.html || '' }) })
      .then(function (r) { return r.json(); }).then(reply, function () { reply({}); });
    return true;
  }
  if (msg.type === 'carfax-status' && msg.vin) {
    laCarfaxStatus(String(msg.vin).toUpperCase(), String(msg.status || ''), String(msg.note || ''));
    if (sender.tab) { cfoTabEvent(sender.tab.id, String(msg.status || ''), String(msg.note || '')); }   // готово / не вышло — закрыть, дальше
    return;
  }
  if (msg.type === 'carfax-signed-in' && sender.tab) { cfoSignedIn(); return; }
  if (msg.type === 'carfax-login') { cfoShowLogin(); return; }
  if (msg.type === 'carfax-show' && sender.tab) { chrome.tabs.update(sender.tab.id, { active: true }, function () { void chrome.runtime.lastError; }); return; }
  if (msg.type === 'carfax-close' && sender.tab) { chrome.tabs.remove(sender.tab.id, function () { void chrome.runtime.lastError; }); return; }
  if (msg.type === 'carfax-pdf') {
    fetch('http://127.0.0.1:8765/api/carfax_pdf', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LA': '1' },
      body: JSON.stringify({ vin: msg.vin || '', url: msg.url || '', pdf: msg.pdf || '' }) })
      .then(function (r) { return r.json(); })
      .then(function (got) {
        if (msg.vin) { laCarfaxStatus(String(msg.vin).toUpperCase(), got && got.ok ? 'done' : 'error', got && got.ok ? '' : (got && got.error) || ''); }
        reply(got);
      }, function () { reply({ ok: false, error: 'программа не запущена (127.0.0.1:8765)' }); });
    return true;                                  // ответ — асинхронно
  }
});

/* Файлы закладки и расширения (страницы KBB, аукционов, отчёты KBB) — не в сами «Загрузки», а в их папку «Lot Analyzer»:
   «Загрузки» не засоряются, программа читает эту папку. Остальные скачивания не трогаем. */
var LA_FILE = /^(CarMax|ACV|Manheim|ADESA|KBB|auction)_.+\.html?$|^auction_(all|results)_\d{4}-\d\d-\d\d_\d{4}\.(csv|xlsx)$/i;   // + итоги записи аукциона (Velocicast)
chrome.downloads.onDeterminingFilename.addListener(function (item, suggest) {
  var name = String(item.filename || '').split(/[\\/]/).pop();
  if (LA_FILE.test(name)) { suggest({ filename: 'Lot Analyzer/' + name, conflictAction: 'uniquify' }); } else { suggest(); }
});
