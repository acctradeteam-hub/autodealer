/* Lot Analyzer: отчёт Carfax, открытый на vin-reports-pro.com (и в рамке carfax.com внутри него), — в программу:
   текст отчёта и адрес страницы. Программа достаёт главное (владельцы, ДТП и их сила, штаты, титул, пробег, сервис)
   и показывает у машины «📄 Carfax: …» со ссылкой «открыть ↗». Отчёт дорисовывается — смотрим несколько раз. */
(function () {
  var sent = {};
  var vinOf = function (text) {
    var all = text.match(/\b[A-HJ-NPR-Z0-9]{17}\b/g) || [];
    var counts = {};
    all.filter(function (v) { return /\d/.test(v) && /[A-Z]/.test(v); }).forEach(function (v) { counts[v] = (counts[v] || 0) + 1; });
    return Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; })[0] || '';
  };
  var look = function () {
    var text = (document.body && document.body.innerText) || '';
    if (text.length < 1500 || !/carfax|vehicle history/i.test(text)) { return; }
    var vin = vinOf(text);
    var url = window.top === window ? location.href : (document.referrer || location.href);
    var key = vin + '|' + text.length;
    if (!vin || sent[key]) { return; }
    sent[key] = 1;
    try { chrome.runtime.sendMessage({ type: 'carfax', vin: vin, url: url, text: text.slice(0, 300000) }, function () { void chrome.runtime.lastError; }); } catch (e) { /* расширение обновили */ }
  };
  [3000, 8000, 15000, 30000].forEach(function (ms) { setTimeout(look, ms); });
})();
