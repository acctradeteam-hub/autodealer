/* Запись торгов (auction_join.js) на Velocicast (CarMax live).
   — Окно записи торгов (понедельник 7:55 или «▶ Запустить сейчас» в окне программы): «CHOOSE LANES» → отметить все
     линии → подтвердить; линии ещё не открыты (торги не начались) — пробуем каждую минуту до 40 минут. Линии на
     экране — фон запускает логгер, дальше всё само.
   — Velocicast открыт вручную — внизу кнопка «● Записывать торги в программу»: тот же логгер, те же файлы.
   Нажимаются только выбор линий и кнопка подтверждения; ничего, где есть «bid» / «buy», не нажимается. */
(function () {
  if (window.top !== window) { return; }
  var ask = function (msg) {
    return new Promise(function (r) { try { chrome.runtime.sendMessage(msg, function (a) { void chrome.runtime.lastError; r(a); }); } catch (e) { r(null); } });
  };
  var pause = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
  var text = function (el) { return String(el.innerText || el.value || el.getAttribute('aria-label') || el.title || '').replace(/\s+/g, ' ').trim(); };
  var visible = function (el) { var r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
  var NEVER = /\b(bid|bidding|buy|purchase|offer|proxy)\b|ставк/i;
  var lanesOpen = function () { return document.querySelectorAll('.lane-panel-container').length; };
  var clickable = function (scope) {
    return Array.prototype.filter.call((scope || document).querySelectorAll('a, button, [role="button"], [role="menuitem"], input[type="button"], input[type="submit"]'),
      function (el) { return visible(el) && !el.disabled && !NEVER.test(text(el)); });
  };
  var findText = function (re, scope) { return clickable(scope).filter(function (el) { return re.test(text(el)); })[0] || null; };
  var dialog = function () {
    var list = document.querySelectorAll('[role="dialog"], [aria-modal="true"], .modal, .mat-dialog-container, .MuiDialog-root, .ReactModal__Content, .v-dialog, .dialog, .popup');
    for (var i = list.length - 1; i >= 0; i--) { if (visible(list[i])) { return list[i]; } }
    return null;
  };
  var box = document.createElement('div');
  box.setAttribute('style', 'position:fixed;z-index:2147483646;left:16px;bottom:16px;max-width:460px;padding:10px 14px;background:#1f6f43;color:#fff;font:13px/1.4 system-ui,sans-serif;border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.3)');
  var say = function (s, ms) {
    box.textContent = s;
    if (!box.isConnected && document.body) { document.body.appendChild(box); }
    clearTimeout(box.t);
    if (ms) { box.t = setTimeout(function () { box.remove(); }, ms); }
  };

  /* Все линии: «CHOOSE LANES» → «Select all» (или каждая неотмеченная) → подтверждение. */
  var chooseAll = async function () {
    var open = findText(/choose\s+lanes?|select\s+lanes?|add\s+lanes?|^lanes$/i);
    if (!open) { return lanesOpen() > 0; }
    open.click();
    await pause(1500);
    var scope = dialog() || document;
    var all = findText(/^(select|check)\s+all\b|^all\s+lanes\b|выбрать\s+все/i, scope);
    if (all) {
      all.click();
    } else {
      var boxes = Array.prototype.filter.call(scope.querySelectorAll('input[type="checkbox"]'), function (cb) { return !cb.checked && !cb.disabled; });
      if (boxes.length) {
        boxes.forEach(function (cb) { (cb.closest('label') || cb).click(); });
      } else {                                         // линии кнопками / пунктами списка: «Lane A - CarMax Murrieta»
        clickable(scope).concat(Array.prototype.slice.call(scope.querySelectorAll('[role="option"], [role="checkbox"], li')))
          .filter(function (el) { return /\blane\s+\w+/i.test(text(el)) && !NEVER.test(text(el)) && visible(el) &&
                                          el.getAttribute('aria-checked') !== 'true' && el.getAttribute('aria-selected') !== 'true' && !/\b(selected|active|checked)\b/.test(el.className || ''); })
          .forEach(function (el) { el.click(); });
      }
    }
    await pause(700);
    var ok = findText(/^(done|ok|okay|apply|save|confirm|view|view lanes|open|open lanes|show lanes|join|go|continue|готово)$/i, dialog() || scope);
    if (ok) { ok.click(); }
    for (var i = 0; i < 20 && !lanesOpen(); i++) { await pause(1000); }
    return lanesOpen() > 0;
  };

  var manualButton = function () {
    if (document.getElementById('la-rec') || !document.body) { return; }
    var b = document.createElement('button');
    b.id = 'la-rec';
    b.textContent = '● Записывать торги в программу';
    b.title = 'Lot Analyzer: логгер торгов (как навык velocicast-auction-logger) — итоги каждые 15 минут в программу, файлом auction_all_….csv';
    b.setAttribute('style', 'position:fixed;z-index:2147483646;left:16px;bottom:16px;padding:9px 14px;border:0;border-radius:8px;background:#b91c1c;color:#fff;font:600 13px system-ui,sans-serif;cursor:pointer;box-shadow:0 4px 16px rgba(0,0,0,.3)');
    b.onclick = async function () {
      b.disabled = true; b.textContent = 'Запускаю запись…';
      var a = await ask({ type: 'vc-manual-start', lanes: lanesOpen() });
      b.remove();
      say(a && a.ok ? 'Lot Analyzer: запись торгов идёт — итоги каждые 15 минут в программу. Вкладку не закрывайте и не обновляйте.' : 'Lot Analyzer: запись не запустилась — обновите расширение.', 12000);
    };
    document.body.appendChild(b);
  };

  (async function () {
    var job = await ask({ type: 'vc-ask' });
    if (job && job.logging) { say('Lot Analyzer: запись торгов идёт — итоги каждые 15 минут в программу.', 8000); return; }
    if (!job || !job.auto) {                           // открыт вручную — кнопка записи, когда линии на экране
      for (var w = 0; w < 600 && !lanesOpen(); w++) { await pause(2000); }
      if (lanesOpen()) { manualButton(); }
      return;
    }
    say('Lot Analyzer: запись торгов — открываю все линии…');
    var started = Date.now();
    for (var attempt = 1; Date.now() - started < 40 * 60000; attempt++) {
      await pause(attempt === 1 ? 4000 : 60000);
      var ok = false;
      try { ok = await chooseAll(); } catch (e) { ok = lanesOpen() > 0; }
      if (ok) {
        say('Lot Analyzer: открыто линий: ' + lanesOpen() + '. Запускаю запись…');
        var a = await ask({ type: 'vc-lanes', lanes: lanesOpen() });
        say(a && a.ok ? 'Lot Analyzer: запись торгов идёт (линий: ' + lanesOpen() + ') — итоги каждые 15 минут в программу. Окно не закрывайте.' : 'Lot Analyzer: логгер не запустился.', 15000);
        return;
      }
      chrome.runtime.sendMessage({ type: 'vc-wait', note: 'линии ещё не открылись — попытка ' + attempt });
      say('Lot Analyzer: линии ещё не открылись (торги не начались?) — пробую снова через минуту…');
    }
    chrome.runtime.sendMessage({ type: 'join-fail', step: 'lanes', note: 'за 40 минут линии не открылись — выберите их сами («CHOOSE LANES»), запись начнётся сама', html: document.documentElement.outerHTML });
    say('Lot Analyzer: не смог открыть линии сам. Выберите их («CHOOSE LANES») — запись начнётся сама.');
    for (var k = 0; k < 5400 && !lanesOpen(); k++) { await pause(2000); }     // до 3 часов ждём, пока вы откроете линии
    if (lanesOpen()) {
      var m = await ask({ type: 'vc-manual-start', lanes: lanesOpen() });
      say(m && m.ok ? 'Lot Analyzer: запись торгов идёт — итоги каждые 15 минут в программу.' : 'Lot Analyzer: логгер не запустился.', 15000);
    }
  })();
})();
