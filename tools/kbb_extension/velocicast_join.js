/* Запись торгов (auction_join.js) на Velocicast (CarMax live).
   — Окно записи торгов (понедельник 7:55 или «▶ Запустить сейчас» в окне программы): «CHOOSE LANES» → отметить все
     линии → подтвердить. Торги CarMax начинаются в 8:00, 8:30 (El Mirage) и 9:00 — линии выбираются заранее, за 5 минут
     до начала: в 7:55 (сразу при входе), в 8:25 и в 8:55 — добавляются линии аукционов, которые начинаются в это время
     (уже открытые не трогаются). В 7:55 линий не оказалось — ещё одна попытка в 8:00. Линии на экране — фон запускает
     логгер, дальше всё само; итоги дня — одним файлом в 16:00.
   — Velocicast открыт вручную — внизу кнопка «● Записывать торги в программу»: тот же логгер, тот же один файл.
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

  /* Все линии: «CHOOSE LANES» → «Select all» (или каждая неотмеченная) → подтверждение.
     addOnly — линии уже открыты (8:25, 8:55): только неотмеченные галочки или линии, которых ещё нет на экране,
     без «Select all», — чтобы не снять уже выбранные линии. */
  var chooseAll = async function (addOnly) {
    var open = findText(/choose\s+lanes?|select\s+lanes?|add\s+lanes?|^lanes$/i);
    if (!open) { return lanesOpen() > 0; }
    open.click();
    await pause(1500);
    var scope = dialog() || document;
    var all = addOnly ? null : findText(/^(select|check)\s+all\b|^all\s+lanes\b|выбрать\s+все/i, scope);
    if (all) {
      all.click();
    } else if (addOnly) {
      var allBoxes = scope.querySelectorAll('input[type="checkbox"]');
      if (allBoxes.length) {
        Array.prototype.filter.call(allBoxes, function (cb) { return !cb.checked && !cb.disabled; })
          .forEach(function (cb) { (cb.closest('label') || cb).click(); });
      } else {                                         // линии кнопками: нажимаем только те, которых ещё нет на экране
        var norm = function (t) { return String(t || '').toLowerCase().replace(/\s+/g, ' ').trim(); };
        var shown = Array.prototype.map.call(document.querySelectorAll('.lane-title-bar__name'), function (e) { return norm(e.innerText || e.textContent); });
        clickable(scope).concat(Array.prototype.slice.call(scope.querySelectorAll('[role="option"], [role="checkbox"], li')))
          .filter(function (el) { var t = norm(text(el)); return /\blane\s+\w+/.test(t) && !NEVER.test(t) && visible(el) && el.getAttribute('aria-checked') !== 'true' &&
                                          el.getAttribute('aria-selected') !== 'true' && !shown.some(function (n) { return n === t || t.indexOf(n) >= 0 || n.indexOf(t) >= 0; }); })
          .forEach(function (el) { el.click(); });
      }
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
    b.title = 'Lot Analyzer: логгер торгов (как навык velocicast-auction-logger) — итоги всех торгов дня одним файлом auction_all_….csv в программу в 16:00';
    b.setAttribute('style', 'position:fixed;z-index:2147483646;left:16px;bottom:16px;padding:9px 14px;border:0;border-radius:8px;background:#b91c1c;color:#fff;font:600 13px system-ui,sans-serif;cursor:pointer;box-shadow:0 4px 16px rgba(0,0,0,.3)');
    b.onclick = async function () {
      b.disabled = true; b.textContent = 'Запускаю запись…';
      var a = await ask({ type: 'vc-manual-start', lanes: lanesOpen() });
      b.remove();
      say(a && a.ok ? 'Lot Analyzer: запись торгов идёт — итоги дня одним файлом в программу в 16:00. Вкладку не закрывайте.' : 'Lot Analyzer: запись не запустилась — обновите расширение.', 12000);
    };
    document.body.appendChild(b);
  };

  /* Ждём до времени t, сообщая фону, что вкладка жива (раз в 3 минуты). */
  var waitUntil = async function (t, note) {
    while (Date.now() < t) {
      chrome.runtime.sendMessage({ type: 'vc-wait', note: note });
      await pause(Math.min(180000, t - Date.now()));
    }
  };
  var at = function (h, m) { var d = new Date(); d.setHours(h, m, 0, 0); return d.getTime(); };
  /* Ещё раз выбрать линии — за 5 минут до начала торгов: 8:30 (El Mirage) и 9:00. В 7:55 — при входе. */
  var LANE_TIMES = [[8, 25], [8, 55]];

  (async function () {
    var job = await ask({ type: 'vc-ask' });
    if (job && job.logging) { say('Lot Analyzer: запись торгов идёт — итоги дня одним файлом в программу в 16:00.', 8000); return; }
    if (!job || !job.auto) {                           // открыт вручную — кнопка записи, когда линии на экране
      for (var w = 0; w < 600 && !lanesOpen(); w++) { await pause(2000); }
      if (lanesOpen()) { manualButton(); }
      return;
    }
    var logging = false;
    var start = async function () {
      say('Lot Analyzer: открыто линий: ' + lanesOpen() + '. Запускаю запись…');
      var a = await ask({ type: 'vc-lanes', lanes: lanesOpen() });
      logging = !!(a && a.ok);
      say(logging ? 'Lot Analyzer: запись торгов идёт (линий: ' + lanesOpen() + ') — итоги дня одним файлом в программу в 16:00. Окно не закрывайте.' : 'Lot Analyzer: логгер не запустился.', 15000);
    };
    var attempt = async function () {
      var had = lanesOpen(), ok = false;
      try { ok = await chooseAll(had > 0); } catch (e) { ok = lanesOpen() > 0; }
      if (ok && !logging) { await start(); }
      else if (ok && lanesOpen() > had) { say('Lot Analyzer: добавлены линии — теперь их ' + lanesOpen() + '.', 10000); }
    };
    say('Lot Analyzer: запись торгов — открываю все линии…');
    await pause(4000);
    await attempt();
    /* Торги начинаются в 8:00, 8:30 (El Mirage) и 9:00 — линии выбираем заранее, за 5 минут: сейчас (7:55), в 8:25
       и в 8:55. Сейчас линий не нашлось — ещё раз в 8:00, к началу первых торгов. */
    var checks = (logging ? [] : [at(8, 0)]).concat(LANE_TIMES.map(function (hm) { return at(hm[0], hm[1]); }));
    for (var i = 0; i < checks.length; i++) {
      if (Date.now() >= checks[i]) { continue; }
      var hhmm = new Date(checks[i]).toTimeString().slice(0, 5);
      if (!logging) { say('Lot Analyzer: линий пока нет — выберу их ещё раз в ' + hhmm + '.'); }
      await waitUntil(checks[i], logging ? 'идёт запись' : 'линий пока нет — ещё раз в ' + hhmm);
      await attempt();
    }
    if (logging) { return; }
    chrome.runtime.sendMessage({ type: 'join-fail', step: 'lanes', note: 'линии не открылись — выберите их сами («CHOOSE LANES»), запись начнётся сама', html: document.documentElement.outerHTML });
    say('Lot Analyzer: не смог открыть линии сам. Выберите их («CHOOSE LANES») — запись начнётся сама.');
    for (var k = 0; k < 5400 && !lanesOpen(); k++) { await pause(2000); }     // до 3 часов ждём, пока вы откроете линии
    if (lanesOpen()) {
      var m = await ask({ type: 'vc-manual-start', lanes: lanesOpen() });
      say(m && m.ok ? 'Lot Analyzer: запись торгов идёт — итоги дня одним файлом в программу в 16:00.' : 'Lot Analyzer: логгер не запустился.', 15000);
    }
  })();
})();
