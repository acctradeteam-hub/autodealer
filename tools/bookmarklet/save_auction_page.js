/* Закладка «Сохранить для анализа»: один клик на странице аукциона (watch list,
   карточка лота) — и страница сохраняется в «Загрузки» как .html для lot_analyzer.
   Работает в вашем браузере, под вашим входом на сайт; никуда ничего не отправляет.
   Правила записи: только полные строки-комментарии, после каждой команды «;» —
   build.py склеивает код в одну строку. */
(function () {
  var host = location.hostname.toLowerCase();
  var auction = /carmax/.test(host) ? 'CarMax' : /acvauctions/.test(host) ? 'ACV' : /manheim|coxauto/.test(host) ? 'Manheim' : /adesa|openlane/.test(host) ? 'ADESA' : 'auction';
  var live = document.documentElement;
  var copy = live.cloneNode(true);
  /* Текущий текст заметок и полей ввода — в разметку: cloneNode его не переносит. */
  var liveAreas = live.querySelectorAll('textarea');
  var copyAreas = copy.querySelectorAll('textarea');
  for (var i = 0; i < liveAreas.length && i < copyAreas.length; i++) { copyAreas[i].textContent = liveAreas[i].value; }
  var liveInputs = live.querySelectorAll('input');
  var copyInputs = copy.querySelectorAll('input');
  for (var j = 0; j < liveInputs.length && j < copyInputs.length; j++) {
    var type = (liveInputs[j].type || '').toLowerCase();
    if (type === 'password' || type === 'hidden') { copyInputs[j].removeAttribute('value'); } else { copyInputs[j].setAttribute('value', liveInputs[j].value); }
  }
  /* Лишнее: скрипты (кроме JSON с данными), стили, значки, фреймы. */
  var junk = copy.querySelectorAll('script, style, link[rel="stylesheet"], link[rel="preload"], svg, iframe, noscript, video, audio');
  for (var k = 0; k < junk.length; k++) {
    var el = junk[k];
    var keep = el.tagName === 'SCRIPT' && (/json/i.test(el.type || '') || el.id === '__NEXT_DATA__');
    if (!keep && el.parentNode) { el.parentNode.removeChild(el); }
  }
  var now = new Date();
  var pad = function (n) { return (n < 10 ? '0' : '') + n; };
  var stamp = now.getFullYear() + '-' + pad(now.getMonth() + 1) + '-' + pad(now.getDate()) + '_' + pad(now.getHours()) + pad(now.getMinutes());
  var kind = (document.title.split('|')[0] || 'page').trim().replace(/[^A-Za-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 40) || 'page';
  var name = auction + '_' + kind + '_' + stamp + '.html';
  var html = '<!DOCTYPE html>\n<!-- saved-by: lot_analyzer bookmarklet; saved-at: ' + now.toISOString() + '; url: ' + location.href.replace(/--/g, '-') + ' -->\n' + copy.outerHTML;
  var blob = new Blob([html], { type: 'text/html;charset=utf-8' });
  var link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = name;
  document.body.appendChild(link);
  link.click();
  setTimeout(function () { URL.revokeObjectURL(link.href); link.remove(); }, 2000);
  var vins = {};
  (document.body.innerText.match(/\b[A-HJ-NPR-Z0-9]{17}\b/g) || []).forEach(function (v) { vins[v] = 1; });
  var count = Object.keys(vins).length;
  var toast = document.createElement('div');
  toast.textContent = 'Сохранено: ' + name + (count ? ' — VIN на странице: ' + count : '');
  toast.setAttribute('style', 'position:fixed;z-index:2147483647;right:16px;bottom:16px;max-width:420px;padding:12px 16px;background:#1f6f43;color:#fff;font:14px/1.4 system-ui,sans-serif;border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.3)');
  document.body.appendChild(toast);
  setTimeout(function () { toast.remove(); }, 5000);
})();
