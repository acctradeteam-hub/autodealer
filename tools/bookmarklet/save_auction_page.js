/* Закладка «Сохранить для анализа»: один клик на странице аукциона (watch list,
   карточка лота) — и страница сохраняется в «Загрузки» как .html для lot_analyzer.
   Работает в вашем браузере, под вашим входом на сайт; никуда ничего не отправляет.
   Правила записи: только полные строки-комментарии, после каждой команды «;» —
   build.py склеивает код в одну строку. */
(function () {
  var host = location.hostname.toLowerCase();
  var auction = /carmax/.test(host) ? 'CarMax' : /acvauctions/.test(host) ? 'ACV' : /manheim|coxauto/.test(host) ? 'Manheim' : /adesa|openlane/.test(host) ? 'ADESA' : /kbb\.com/.test(host) ? 'KBB' : 'auction';
  var toast = null;
  var say = function (text, hide) {
    if (!toast) {
      toast = document.createElement('div');
      toast.setAttribute('style', 'position:fixed;z-index:2147483647;right:16px;bottom:16px;max-width:420px;padding:12px 16px;background:#1f6f43;color:#fff;font:14px/1.4 system-ui,sans-serif;border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.3)');
      document.body.appendChild(toast);
    }
    toast.textContent = text;
    if (hide) { var t = toast; toast = null; setTimeout(function () { t.remove(); }, hide); }
  };
  var save = function (parts) {
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
  /* Картинки-шкалы с ценой (KBB рисует свою цену в SVG): текст с «$» сохраняем, саму картинку выбрасываем. */
  var pics = copy.querySelectorAll('svg');
  for (var s = 0; s < pics.length; s++) {
    var words = (pics[s].textContent || '').replace(/\s+/g, ' ').trim();
    if (words.indexOf('$') >= 0 && pics[s].parentNode) { var note = document.createElement('span'); note.className = 'svg-text'; note.textContent = ' ' + words + ' '; pics[s].parentNode.replaceChild(note, pics[s]); }
  }
  /* Лишнее: скрипты (кроме JSON с данными), стили, значки, фреймы. */
  var junk = copy.querySelectorAll('script, style, link[rel="stylesheet"], link[rel="preload"], svg, iframe, noscript, video, audio');
  for (var k = 0; k < junk.length; k++) {
    var el = junk[k];
    var keep = el.tagName === 'SCRIPT' && (/json/i.test(el.type || '') || el.id === '__NEXT_DATA__');
    if (!keep && el.parentNode) { el.parentNode.removeChild(el); }
  }
  /* Режим «все страницы»: карточки текущей страницы убираем, вместо них — JSON всех собранных машин. */
  if (parts) {
    var own = copy.querySelectorAll('.stockwave-vehicle-info');
    for (var m = 0; m < own.length; m++) { own[m].parentNode.removeChild(own[m]); }
    var box = document.createElement('div');
    box.id = 'lot-analyzer-all-pages';
    box.setAttribute('style', 'display:none');
    box.innerHTML = parts.map(function (t) { return '<div class="StockWaveInfo stockwave-vehicle-info">' + t.replace(/&/g, '&amp;').replace(/</g, '&lt;') + '</div>'; }).join('');
    copy.querySelector('body').appendChild(box);
  }
  var now = new Date();
  var pad = function (n) { return (n < 10 ? '0' : '') + n; };
  var stamp = now.getFullYear() + '-' + pad(now.getMonth() + 1) + '-' + pad(now.getDate()) + '_' + pad(now.getHours()) + pad(now.getMinutes());
  var kind = (document.title.split('|')[0] || 'page').trim().replace(/[^A-Za-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 40) || 'page';
  var name = auction + '_' + kind + (parts ? '_all' + parts.length : '') + '_' + stamp + '.html';
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
  var count = parts ? parts.length : Object.keys(vins).length;
  say('Сохранено: ' + name + (count ? (parts ? ' — машин со всех страниц: ' : ' — VIN на странице: ') + count : ''), 5000);
  };
  /* Manheim показывает по 100 машин на странице. Режим «все страницы»: листаем кнопкой «следующая»,
     с каждой страницы берём JSON машин (.stockwave-vehicle-info), в конце — один файл. */
  var rowsNow = function () { return document.querySelectorAll('.stockwave-vehicle-row'); };
  var firstKey = function () { var r = rowsNow()[0]; return r ? r.getAttribute('data-key') : ''; };
  var collectAll = function () {
    var seen = {};
    var parts = [];
    var pages = 0;
    var grab = function () {
      var rows = rowsNow();
      for (var i = 0; i < rows.length; i++) {
        var info = rows[i].querySelector('.stockwave-vehicle-info');
        var key = rows[i].getAttribute('data-key') || String(parts.length);
        var text = info ? info.textContent.trim() : '';
        if (text.charAt(0) === '{' && !seen[key]) { seen[key] = 1; parts.push(text); }
      }
    };
    var ready = function (before) {
      var rows = rowsNow();
      if (!rows.length || firstKey() === before) { return false; }
      for (var i = 0; i < rows.length; i++) {
        var info = rows[i].querySelector('.stockwave-vehicle-info');
        if (!info || info.textContent.trim().charAt(0) !== '{') { return false; }
      }
      return true;
    };
    var step = function () {
      grab();
      pages += 1;
      say('Manheim: страниц ' + pages + ', машин ' + parts.length + '… не трогайте страницу');
      var next = document.querySelector('.pagination__control--next');
      if (!next || next.disabled || /disabled/i.test(next.className) || next.getAttribute('aria-disabled') === 'true' || pages >= 80) { save(parts); return; }
      var before = firstKey();
      var waited = 0;
      next.click();
      var poll = function () {
        waited += 400;
        if (ready(before)) { setTimeout(step, 300); } else if (waited > 30000) { save(parts); } else { setTimeout(poll, 400); }
      };
      setTimeout(poll, 400);
    };
    step();
  };
  var hasPages = auction === 'Manheim' && document.querySelector('.pagination__control--next') && document.querySelector('.stockwave-vehicle-info');
  if (hasPages && confirm('Manheim: собрать ВСЕ страницы результатов в один файл?\nОК — все страницы (около 3 секунд на страницу), Отмена — только эту.')) {
    var back = document.querySelector('.pagination__control--page-1');
    if (back && !/selected/.test(back.className)) { var b0 = firstKey(); back.click(); var w0 = 0; var wait0 = function () { w0 += 400; if (firstKey() !== b0 || w0 > 15000) { setTimeout(collectAll, 800); } else { setTimeout(wait0, 400); } }; setTimeout(wait0, 400); } else { collectAll(); }
  } else { save(null); }
})();
