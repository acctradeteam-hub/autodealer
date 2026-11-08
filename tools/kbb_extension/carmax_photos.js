/* Lot Analyzer: есть ли уже фото у машин «без фото» — по карточкам лотов CarMax (/vehicledetail/<номер>).
   Список поиска CarMax ещё пишет «Check back for images», а на карточке лота фото уже есть.
   Проверку ведёт фон расширения (photo_check.js): вкладка сама по очереди открывает карточки; здесь — на открытой
   карточке считаем фото и отвечаем фону. Открылась не карточка (выбор аукциона, вход) — сообщаем, куда перекинуло. */
(function () {
  if (window.top !== window) { return; }
  var pause = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
  var ask = function (msg) {
    return new Promise(function (r) {
      try { chrome.runtime.sendMessage(msg, function (a) { void chrome.runtime.lastError; r(a); }); } catch (e) { r(null); }
    });
  };
  /* Старый адрес (кнопка окна до версии 2.18): проверку ведёт фон — по карточкам в этой же вкладке. */
  if (/la-photos/.test(location.hash)) {
    ask({ type: 'photos-start', here: true, why: 'кнопка' });
    return;
  }
  var box = document.createElement('div');
  box.setAttribute('style', 'position:fixed;z-index:2147483647;right:16px;bottom:16px;max-width:420px;padding:10px 14px;background:#1f6f43;color:#fff;font:13px/1.4 system-ui,sans-serif;border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.3)');
  var say = function (s) { box.textContent = s; if (!box.isConnected && document.body) { document.body.appendChild(box); } };
  /* Фото карточки: картинки img2.carmax.com/assets/<номер>/image/…; «Check back for images» — фото нет (0); не дорисовалась — null. */
  var count = function (doc, stock) {
    var names = Array.prototype.map.call(doc.querySelectorAll('img, source'), function (i) { return i.currentSrc || i.src || i.srcset || ''; })
      .concat(Array.prototype.map.call(doc.querySelectorAll('[style*="/assets/"]'), function (e) { return e.getAttribute('style') || ''; }))
      .filter(function (u) { return u.indexOf('/assets/' + stock + '/image/') >= 0; })
      .map(function (u) { return u.split('/assets/' + stock + '/image/')[1].split(/[?"')\s,]/)[0]; })
      .filter(function (n, i, all) { return n && all.indexOf(n) === i; });
    if (names.length) { return { count: names.length, photo: 'https://img2.carmax.com/assets/' + stock + '/image/' + names[0] }; }
    return /check back for images|no images available|images coming soon|photos coming soon/i.test((doc.body && doc.body.innerText) || '') ? { count: 0, photo: '' } : null;
  };
  var stockHere = function () { return ((/\/vehicledetail\/(\d+)/.exec(location.pathname) || [])[1]) || ''; };
  (async function () {
    var job = await ask({ type: 'photo-job' });
    if (!job || !job.stock) { return; }                        // обычная вкладка CarMax — не наша проверка
    say('Lot Analyzer: проверка фото — карточка ' + job.n + ' из ' + job.total + (job.pass > 1 ? ' (перепроверка)' : '') + '…');
    /* CarMax мог открыть не карточку, а выбор аукциона / вход: ждём пару секунд (переход внутри сайта) и сообщаем. */
    for (var w = 0; w < 4 && stockHere() !== String(job.stock); w++) { await pause(1000); }
    if (stockHere() !== String(job.stock)) {
      chrome.runtime.sendMessage({ type: 'photo-card', stock: job.stock, error: 'redirect', url: location.href, title: document.title });
      say('Lot Analyzer: CarMax открыл не карточку лота, а «' + (document.title || location.pathname) + '». Пробую следующую…');
      return;
    }
    /* Фото дорисовываются не сразу: до 20 с. «Нет фото» — только если так и осталось несколько секунд подряд. */
    var got = null, zeros = 0;
    for (var i = 0; i < 20; i++) {
      await pause(1000);
      got = count(document, job.stock) || got;
      if (got && got.count > 0) { break; }
      zeros = got && got.count === 0 ? zeros + 1 : 0;
      if (zeros >= 4 && i >= 6) { break; }
    }
    if (!got) {
      chrome.runtime.sendMessage({ type: 'photo-card', stock: job.stock, error: 'no-answer', url: location.href, title: document.title });
      return;
    }
    say('Lot Analyzer: ' + (got.count > 0 ? 'фото есть — ' + got.count : 'фото пока нет') + '. Дальше…');
    chrome.runtime.sendMessage({ type: 'photo-card', stock: job.stock, count: got.count, photo: got.photo });
  })();
})();
