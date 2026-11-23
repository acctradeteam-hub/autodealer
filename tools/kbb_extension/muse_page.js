/* Lot Analyzer: Muse сам — на странице muse.ai. Работает только во вкладке, которую расширение открыло под задание
   (muse_auto.js): вставляет текст задания в поле сообщения, нажимает «Отправить», ждёт в ответе таблицу (CSV) и отдаёт
   её программе. Больше на странице ничего не нажимает: ни ссылок и кнопок в ответе Muse, ни подтверждений действий
   (Muse попросит подтвердить — таблицы не будет, вкладка останется открытой для вас). Muse просит войти — вкладка
   показывается, войти можете только вы. */
(function () {
  if (window.__laMuse) { return; }
  window.__laMuse = 1;
  var HEAD = /group[ \t]*[,;|\t][ \t]*year[ \t]*[,;|\t][ \t]*make[ \t]*[,;|\t][ \t]*model/i;   // заголовок таблицы ответа
  var STABLE_MS = 60000;            // таблица не меняется минуту и Muse не «печатает» — ответ готов
  var NO_TABLE_MS = 6 * 60000;      // страница не меняется 6 минут, а таблицы нет — Muse ждёт вас (вопрос, подтверждение)
  function ask(m) {
    return new Promise(function (res) {
      try { chrome.runtime.sendMessage(m, function (a) { void chrome.runtime.lastError; res(a); }); } catch (e) { res(null); }
    });
  }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function shown(el) {
    var r = el.getBoundingClientRect();
    return r.width > 60 && r.height > 12 && getComputedStyle(el).visibility !== 'hidden' && !el.closest('[aria-hidden="true"]');
  }
  /* Поле сообщения: видимое, редактируемое, ниже всех на странице (у чатов поле внизу). */
  function findInput() {
    var list = Array.prototype.slice.call(document.querySelectorAll('textarea, [contenteditable="true"], [contenteditable="plaintext-only"], [role="textbox"][contenteditable]'))
      .filter(function (el) { return shown(el) && !el.disabled && !el.readOnly; });
    list.sort(function (a, b) { return b.getBoundingClientRect().bottom - a.getBoundingClientRect().bottom; });
    return list[0] || null;
  }
  function pageText() { return (document.body && document.body.innerText) || ''; }
  function looksLogin() {
    if (/log-?in|sign-?in|auth|checkpoint/i.test(location.pathname)) { return true; }
    if (document.querySelector('input[type="password"]')) { return true; }
    return /\b(log in|sign in|continue with (facebook|instagram|meta))\b/i.test(pageText().slice(0, 4000)) && !findInput();
  }
  function valueOf(el) { return el.tagName === 'TEXTAREA' ? el.value : el.innerText; }
  function put(el, text) {
    el.focus();
    if (el.tagName === 'TEXTAREA') {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(el, text);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      return;
    }
    try {                                                   // редакторы чатов принимают вставку (как ⌘V)
      var dt = new DataTransfer();
      dt.setData('text/plain', text);
      el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
    } catch (e) { /* нет вставки — ниже печать */ }
    if (valueOf(el).indexOf('Reply ONLY') < 0) {
      try { document.execCommand('insertText', false, text); } catch (e) { /* не вышло */ }
    }
  }
  /* Кнопка «Отправить» рядом с полем (по подписи); нет — Enter в поле. */
  function sendButton(el) {
    var scope = el.parentElement;
    for (var i = 0; i < 7 && scope; i++, scope = scope.parentElement) {
      var b = scope.querySelector('button[aria-label*="send" i], [role="button"][aria-label*="send" i], button[aria-label*="отправ" i], button[data-testid*="send" i], button[type="submit"]');
      if (b && !b.disabled && b.getAttribute('aria-disabled') !== 'true') { return b; }
    }
    return null;
  }
  function busy() {                                         // Muse ещё пишет: видна кнопка «Stop …» («Stop generating»)
    return Array.prototype.some.call(document.querySelectorAll('button[aria-label], [role="button"][aria-label]'), function (b) {
      return /^\s*(stop|остановить)/i.test(b.getAttribute('aria-label') || '') && shown(b);
    });
  }
  function firstLine(text) { return String(text).split('\n')[0].trim().slice(0, 60); }
  /* Ответ: после нашего задания на странице — таблица с заголовком group,year,make,model… (не заголовок из самого задания). */
  function answer(job) {
    var all = pageText();
    var at = all.lastIndexOf(firstLine(job.text));
    var tail = at >= 0 ? all.slice(at) : all;
    var lines = tail.split('\n'), start = -1;
    for (var i = 0; i < lines.length; i++) {
      if (HEAD.test(lines[i]) && !/^\s*group\s*=/i.test(lines[i + 1] || '')) { start = i; }   // в задании за заголовком — «group = …»
    }
    if (start < 0) { return { tail: tail, table: '', rows: 0 }; }
    var take = [lines[start]];                              // таблица: строки подряд за заголовком (дальше — подписи страницы)
    for (var j = start + 1; j < lines.length; j++) {
      if (lines[j].trim() && lines[j].split(/[,\t|;]/).length < 6) { break; }
      take.push(lines[j]);
    }
    var rows = take.slice(1).filter(function (ln) { return /\b(19|20)\d\d\b/.test(ln); }).length;
    return { tail: tail, table: take.join('\n').trim(), rows: rows };
  }
  function banner(text) {
    var b = document.getElementById('la-muse-banner');
    if (!b) {
      b = document.createElement('div');
      b.id = 'la-muse-banner';
      b.style.cssText = 'position:fixed;top:8px;left:50%;transform:translateX(-50%);z-index:2147483647;max-width:92vw;padding:8px 14px;border-radius:8px;background:#fff1db;color:#5a3a00;border:1px solid #f5c26b;font:14px/1.4 -apple-system,Arial,sans-serif;box-shadow:0 2px 10px rgba(0,0,0,.15)';
      document.documentElement.appendChild(b);
    }
    b.textContent = 'Lot Analyzer: ' + text;
  }
  function fail(job, note, show) { banner(note); return ask({ type: 'muse-fail', id: job.id, note: note, show: !!show }); }

  function watch(job) {
    var lastTail = '', sinceTail = Date.now(), lastTable = '', sinceTable = Date.now(), began = Date.now(), seenPrompt = false;
    banner('задание отправлено Muse — жду таблицу; вкладка закроется сама, когда ответ будет в программе.');
    var timer = setInterval(function () {
      var a = answer(job), now = Date.now();
      var calm = a.tail.replace(/\b\d{1,2}:\d{2}(:\d{2})?\s*(am|pm)?/gi, '').replace(/\b\d+\s*(s|sec|m|min|h|hr|seconds?|minutes?|hours?)\s+ago\b/gi, '');
      if (calm !== lastTail) { lastTail = calm; sinceTail = now; }   // часы и «1m ago» на странице — не изменение ответа
      if (a.table !== lastTable) { lastTable = a.table; sinceTable = now; }
      if (pageText().indexOf(firstLine(job.text)) >= 0) { seenPrompt = true; }
      if (!seenPrompt && now - began > 90000) {
        clearInterval(timer);
        fail(job, 'задание не видно в чате Muse — похоже, не отправилось. Нажмите «🤖 Muse сам» ещё раз.', true);
        return;
      }
      if (busy() && now - sinceTable < 3 * STABLE_MS) { return; }   // пишет (или «Stop» висит, а таблица давно не меняется)
      if (a.rows > 0 && now - sinceTable >= STABLE_MS) {
        clearInterval(timer);
        banner('таблица получена — отдаю программе…');
        ask({ type: 'muse-done', id: job.id, text: a.table }).then(function (r) {
          banner(r && r.close ? 'готово: ' + (r.note || 'ответ в программе') : 'таблицу в ответе не разобрал — посмотрите ответ Muse; в окне программы — «📥 Ответ Muse» вручную.');
        });
        return;
      }
      if (!a.rows && seenPrompt && now - sinceTail >= NO_TABLE_MS) {
        clearInterval(timer);
        fail(job, 'Muse не дал таблицу (ждёт ответа или подтверждения?) — посмотрите эту вкладку; ничего не подтверждаю за вас.', false);
      }
    }, 5000);
  }

  async function run() {
    var job = await ask({ type: 'muse-hello' });
    if (!job || !job.id) { return; }                        // обычная вкладка Muse — не наша
    if (job.state !== 'open') { watch(job); return; }      // страница перезагрузилась после отправки — только ждём ответ
    var el = null;
    for (var i = 0; i < 40 && !el; i++) {                   // до 40 с: страница чата загружается
      el = findInput();
      if (!el && looksLogin()) { fail(job, 'Muse просит войти — войдите на muse.ai в этой вкладке (это делаете только вы) и нажмите «🤖 Muse сам» ещё раз.', true); return; }
      if (!el) { await sleep(1000); }
    }
    if (!el) { fail(job, 'не нашёл поле для сообщения на muse.ai — вкладка открыта; пришлите Claude эту страницу (сохранить страницу), поправим.', true); return; }
    put(el, job.text);
    await sleep(800);
    if (valueOf(el).indexOf('Reply ONLY') < 0) {          // фоновая вкладка: редактор не принял текст — покажем её и повторим
      ask({ type: 'muse-show' });
      await sleep(1500);
      el = findInput() || el;
      put(el, job.text);
      await sleep(800);
      if (valueOf(el).indexOf('Reply ONLY') < 0) { fail(job, 'текст задания не вставился в поле Muse — вкладка открыта.', true); return; }
    }
    var b = sendButton(el);
    if (b) { b.click(); }
    else { el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true })); }
    await sleep(3000);
    if (valueOf(el).indexOf('Reply ONLY') >= 0 && !b) {   // Enter не отправил — ищем кнопку ещё раз
      b = sendButton(el);
      if (b) { b.click(); await sleep(3000); }
    }
    if (valueOf(el).indexOf('Reply ONLY') >= 0) { fail(job, 'не получилось нажать «Отправить» в Muse — вкладка открыта.', true); return; }
    ask({ type: 'muse-sent', id: job.id });
    watch(job);
  }
  run();
})();
