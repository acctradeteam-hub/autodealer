/* Фон расширения «Lot Analyzer KBB»:
   — открывает ссылки окна программы соседней вкладкой в фоне (вы остаётесь в окне); для вкладки kbb.com
     запоминает машины автопилота — вкладка спросит их сама;
   — закрывает вкладку kbb.com, когда автопилот закончил (просит сама вкладка);
   — берёт у окна программы (только 127.0.0.1:8765, ваш компьютер) тексты заметок для CarMax. */
chrome.runtime.onMessage.addListener(function (msg, sender, reply) {
  if (msg && msg.type === 'open-bg' && sender.tab && /^http:\/\/127\.0\.0\.1:8765\//.test(sender.url || '') && /^https:\/\//.test(msg.url || '')) {
    chrome.tabs.create({ url: msg.url, active: false, index: sender.tab.index + 1, openerTabId: sender.tab.id }, function (tab) {
      if (msg.cars && tab) { var item = {}; item['cars-' + tab.id] = msg.cars; chrome.storage.session.set(item); }
    });
    return false;
  }
  if (msg && msg.type === 'la-cars' && sender.tab) {
    var key = 'cars-' + sender.tab.id;
    chrome.storage.session.get(key, function (got) { chrome.storage.session.remove(key); reply({ cars: (got || {})[key] || '' }); });
    return true;
  }
  if (msg && msg.type === 'close' && sender.tab && /^https:\/\/www\.kbb\.com\//.test(sender.tab.url || '')) {
    chrome.tabs.remove(sender.tab.id);
    return false;
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
