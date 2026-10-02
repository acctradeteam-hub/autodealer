/* Фон расширения «Lot Analyzer KBB»:
   — закрывает вкладку kbb.com, когда автопилот закончил (просит сама вкладка);
   — берёт у окна программы (только 127.0.0.1:8765, ваш компьютер) тексты заметок для CarMax. */
chrome.runtime.onMessage.addListener(function (msg, sender, reply) {
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
