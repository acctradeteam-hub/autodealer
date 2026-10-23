/* Lot Analyzer: число владельцев из AutoCheck на CarMax (carmaxauctions.com/autocheck/<VIN>) — в окно программы.
   — Открыли отчёт AutoCheck сами — число владельцев уходит в программу.
   — Кнопка окна «👤 Владельцы (AutoCheck)» открывает CarMax с #la-owners: здесь по очереди (невидимым окошком
     той же страницы, с вашим входом) открываются отчёты машин, у которых владельцев ещё не знаем, и вкладка закрывается сама. */
(function () {
  var text = function (doc) { return doc && doc.body ? String(doc.body.innerText || doc.body.textContent || '').replace(/\s+/g, ' ') : ''; };
  var NUM = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10, single: 1 };
  var num = function (s) { s = String(s || '').toLowerCase(); return /^\d+$/.test(s) ? Number(s) : (NUM[s] || 0); };
  /* «Owners: 2», «Number of Owners 3», «Calculated Owners 2», «2 Owners», «1-Owner»; иначе — последний «Owner N» из истории */
  var ownersFrom = function (t) {
    var w = '(\\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|single)';
    var pats = [new RegExp('(?:number of|calculated|estimated|total|previous)\\s+owners?\\s*[:\\-]?\\s*' + w + '\\b', 'i'),
                new RegExp('\\bowners?\\s*[:\\-]\\s*' + w + '\\b', 'i'),
                new RegExp('\\b' + w + '[\\s-]+(?:previous\\s+|prior\\s+)?owners?\\b', 'i')];
    /* История «Owner 1 … Owner 3»: несколько номеров — берём наибольший (иначе «1 Owner 2» читалось бы как «1 владелец»). */
    var heads = t.match(/\bowner\s*#?\s*(\d{1,2})\b/gi) || [];
    var nums = heads.map(function (h) { return Number(h.replace(/\D/g, '')); }).filter(function (n) { return n > 0 && n < 20; });
    var explicit = pats[0].exec(t);
    if (explicit && num(explicit[1]) > 0 && num(explicit[1]) < 20) { return { owners: num(explicit[1]), note: t.slice(Math.max(0, explicit.index - 40), explicit.index + 60).trim() }; }
    if (new Set(nums).size >= 2) { var top = Math.max.apply(null, nums); return { owners: top, note: 'Owner 1 … Owner ' + top }; }
    for (var i = 0; i < pats.length; i++) {
      var m = pats[i].exec(t);
      if (m && num(m[1]) > 0 && num(m[1]) < 20) { return { owners: num(m[1]), note: t.slice(Math.max(0, m.index - 40), m.index + 60).trim() }; }
    }
    var seq = t.match(/\bowner\s*#?\s*(\d{1,2})\b/gi);
    if (seq) { var max = Math.max.apply(null, seq.map(function (s) { return Number(s.replace(/\D/g, '')); })); if (max > 0 && max < 20) { return { owners: max, note: 'Owner 1 … Owner ' + max }; } }
    return null;
  };
  var send = function (cars, cb) {
    try { chrome.runtime.sendMessage({ type: 'owners', cars: cars }, function (a) { void chrome.runtime.lastError; if (cb) { cb(a); } }); } catch (e) { if (cb) { cb(null); } }
  };
  var vinOf = function (path) { return ((/\/autocheck\/([A-HJ-NPR-Z0-9]{17})/i.exec(path) || [])[1] || '').toUpperCase(); };
  var pause = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };

  /* 1. Отчёт AutoCheck, открытый вами. */
  var own = vinOf(location.pathname);
  if (own) {
    var tries = 0;
    var look = function () {
      var found = ownersFrom(text(document));
      if (found) { send([{ vin: own, owners: found.owners, note: found.note }]); return; }
      if (++tries < 20) { setTimeout(look, 1000); }
    };
    setTimeout(look, 1500);
    return;
  }

  /* 2. Сбор по кнопке окна: carmaxauctions.com/#la-owners */
  if (window.top !== window) { return; }
  var isOwnersTab = function () {
    if (/la-owners/.test(location.hash)) { return Promise.resolve(true); }
    return new Promise(function (r) {
      try { chrome.runtime.sendMessage({ type: 'owners-tab' }, function (a) { void chrome.runtime.lastError; r(!!(a && a.yes)); }); } catch (e) { r(false); }
    });
  };
  var box = document.createElement('div');
  box.setAttribute('style', 'position:fixed;z-index:2147483647;right:16px;bottom:16px;max-width:420px;padding:10px 14px;background:#1f6f43;color:#fff;font:13px/1.4 system-ui,sans-serif;border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.3)');
  var say = function (s) { box.textContent = s; if (!box.isConnected) { document.body.appendChild(box); } };
  var one = async function (vin) {
    var frame = document.createElement('iframe');
    frame.setAttribute('style', 'position:fixed;left:-10000px;top:0;width:1200px;height:900px;opacity:0');
    frame.src = '/autocheck/' + vin;
    document.body.appendChild(frame);
    var found = null;
    for (var w = 0; w < 25 && !found; w++) {
      await pause(1000);
      try { found = ownersFrom(text(frame.contentDocument)); } catch (e) { break; }
    }
    var sample = '';
    try { sample = text(frame.contentDocument).slice(0, 160); } catch (e) { sample = ''; }
    frame.remove();
    return found ? { vin: vin, owners: found.owners, note: found.note } : { vin: vin, owners: 0, note: 'не нашёл число владельцев: ' + sample };
  };
  (async function () {
    if (!(await isOwnersTab())) { return; }
    say('Lot Analyzer: спрашиваю у программы, для каких машин нужны владельцы…');
    var list = await new Promise(function (r) {
      try { chrome.runtime.sendMessage({ type: 'owners-needed' }, function (a) { void chrome.runtime.lastError; r((a && a.vins) || []); }); } catch (e) { r([]); }
    });
    if (!list.length) { say('Lot Analyzer: владельцы уже известны у всех отобранных машин.'); await pause(4000); try { chrome.runtime.sendMessage({ type: 'close' }); } catch (e) { /* расширение обновили */ } return; }
    var got = 0;
    for (var i = 0; i < list.length; i++) {
      say('Lot Analyzer: AutoCheck ' + (i + 1) + ' из ' + list.length + ' — владельцев найдено у ' + got + '…');
      var res = await one(list[i]);
      if (res.owners) { got++; }
      send([res]);
      await pause(800);
    }
    say('Lot Analyzer: готово — владельцы у ' + got + ' из ' + list.length + ' машин. Вкладка закроется.');
    await pause(3000);
    try { chrome.runtime.sendMessage({ type: 'close' }); } catch (e) { /* расширение обновили */ }
  })();
})();
