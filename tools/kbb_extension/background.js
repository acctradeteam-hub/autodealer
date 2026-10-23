/* Фон расширения «Lot Analyzer KBB»:
   — открывает ссылки окна программы соседней вкладкой в фоне (вы остаётесь в окне); для вкладки kbb.com
     запоминает машины автопилота — вкладка спросит их сама;
   — закрывает вкладку kbb.com, когда автопилот закончил (просит сама вкладка);
   — берёт у окна программы (только 127.0.0.1:8765, ваш компьютер) тексты заметок для CarMax и Manheim. */
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
  if (msg && msg.type === 'owners-tab' && sender.tab) {
    chrome.storage.session.get('owners-' + sender.tab.id, function (got) { reply({ yes: !!(got || {})['owners-' + sender.tab.id] }); });
    return true;
  }
  /* Вкладку сбора владельцев закрывает она сама, когда закончила, — только ту, что открыли для этого. */
  if (msg && msg.type === 'close' && sender.tab && /^https:\/\/www\.carmaxauctions\.com\//.test(sender.tab.url || '')) {
    var key = 'owners-' + sender.tab.id;
    chrome.storage.session.get(key, function (got) { if ((got || {})[key]) { chrome.storage.session.remove(key); chrome.tabs.remove(sender.tab.id); } });
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
  if (msg && msg.type === 'notes') {
    fetch('http://127.0.0.1:8765/api/notes', { cache: 'no-store' })
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
