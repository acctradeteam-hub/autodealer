/* Фон расширения «Lot Analyzer KBB»:
   — открывает ссылки окна программы соседней вкладкой в фоне (вы остаётесь в окне); для вкладки kbb.com
     запоминает машины автопилота — вкладка спросит их сама;
   — закрывает вкладку kbb.com, когда автопилот закончил (просит сама вкладка);
   — берёт у окна программы (только 127.0.0.1:8765, ваш компьютер) тексты заметок для CarMax и Manheim. */
importScripts('auto_collect.js', 'photo_check.js', 'auction_join.js', 'kbb_week.js');   // автосбор CarMax (чт 18:00, пт 7:00); «📷 Проверить фото»; запись торгов по понедельникам; KBB на неделю (чт 18:00 → пт 9:00)

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
  /* Отчёт Carfax (vin-reports-pro.com) — в программу. */
  if (msg && msg.type === 'carfax' && sender.tab && /^https:\/\/(www\.)?(vin-reports-pro|carfax)\.com\//.test(sender.url || sender.tab.url || '')) {
    fetch('http://127.0.0.1:8765/api/carfax', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ vin: msg.vin, url: msg.url, text: msg.text }) }).catch(function () {});
    return false;
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
chrome.runtime.onMessage.addListener(function (msg, sender, reply) {
  if (!msg) { return; }
  if (msg.type === 'carfax-order' && /^[A-HJ-NPR-Z0-9]{17}$/.test(String(msg.vin || '').toUpperCase())) {
    var vin = String(msg.vin).toUpperCase();
    chrome.tabs.create({ url: 'https://www.vin-reports-pro.com/pro#la-carfax=' + vin, active: false }, function () { void chrome.runtime.lastError; });
    laCarfaxStatus(vin, 'opened');
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
  if (msg.type === 'carfax-status' && msg.vin) { laCarfaxStatus(String(msg.vin).toUpperCase(), String(msg.status || ''), String(msg.note || '')); return; }
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
