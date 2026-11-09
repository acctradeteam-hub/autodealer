/* Запись торгов (auction_join.js): на главной CarMax в окне записи — нажать «Join Auction».
   Ссылка — открываем её в этом же окне; кнопка — нажимаем (Velocicast откроется новым окном, всплывающие окна
   CarMax расширение разрешило). Нет кнопки на главной — ищем на странице check-in. Нет входа — сообщаем.
   Ничего, где есть «bid» / «buy», не нажимается. */
(function () {
  if (window.top !== window) { return; }
  var ask = function (msg) {
    return new Promise(function (r) { try { chrome.runtime.sendMessage(msg, function (a) { void chrome.runtime.lastError; r(a); }); } catch (e) { r(null); } });
  };
  var pause = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
  var text = function (el) { return String(el.innerText || el.value || el.getAttribute('aria-label') || el.title || '').replace(/\s+/g, ' ').trim(); };
  var visible = function (el) { var r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
  var JOIN = /^\s*join(\s+(the|a|live))?\s+(live\s+)?auctions?\b/i;
  var NEVER = /\b(bid|buy|purchase|offer)\b|ставк/i;
  var find = function () {
    var els = document.querySelectorAll('a, button, [role="button"], input[type="button"], input[type="submit"]');
    for (var i = 0; i < els.length; i++) {
      var t = text(els[i]);
      if (JOIN.test(t) && !NEVER.test(t) && visible(els[i])) { return els[i]; }
    }
    return null;
  };
  var box = document.createElement('div');
  box.setAttribute('style', 'position:fixed;z-index:2147483647;right:16px;bottom:16px;max-width:440px;padding:10px 14px;background:#1f6f43;color:#fff;font:13px/1.4 system-ui,sans-serif;border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.3)');
  var say = function (s) { box.textContent = s; if (!box.isConnected && document.body) { document.body.appendChild(box); } };
  (async function () {
    var job = await ask({ type: 'join-tab' });
    if (!job || !job.yes) { return; }
    say('Lot Analyzer: запись торгов — ищу кнопку «Join Auction»…');
    var el = null;
    for (var i = 0; i < 40 && !el; i++) {
      if (document.querySelector('input[type="password"]') || /\/(login|signin|account\/login)/i.test(location.pathname)) {
        chrome.runtime.sendMessage({ type: 'join-fail', step: 'login', note: 'нужно войти на carmaxauctions.com в этом Chrome', html: document.documentElement.outerHTML });
        say('Lot Analyzer: войдите на CarMax — запись торгов не началась.');
        return;
      }
      el = find();
      if (!el) { await pause(1000); }
    }
    if (!el && !/checkin/i.test(location.pathname)) {           // на главной нет — страница check-in со списком торгов
      chrome.runtime.sendMessage({ type: 'join-goto', href: 'https://www.carmaxauctions.com/checkin#la-join' });
      return;
    }
    if (!el) {
      chrome.runtime.sendMessage({ type: 'join-fail', step: 'join', note: 'не нашёл кнопку «Join Auction» (ни на главной, ни на check-in)', html: document.documentElement.outerHTML });
      say('Lot Analyzer: не нашёл «Join Auction». Страница сохранена в программе для разбора.');
      return;
    }
    var href = el.tagName === 'A' ? el.href : '';
    say('Lot Analyzer: нажимаю «' + text(el) + '»…');
    if (href && /^https:\/\//.test(href) && !/#$/.test(href)) {
      chrome.runtime.sendMessage({ type: 'join-href', href: href, text: text(el) });
      return;
    }
    chrome.runtime.sendMessage({ type: 'join-clicked', text: text(el) });
    el.click();
  })();
})();
