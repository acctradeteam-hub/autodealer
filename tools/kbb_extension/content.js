/* Собрано из tools/bookmarklet/save_auction_page.js (python3 tools/bookmarklet/build.py) — не править вручную. */
window.lotAnalyzerAuto = true;
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
  if (/^(127\.0\.0\.1|localhost)$/.test(host)) {
    say('Это окно программы — здесь сохранять не нужно. Нажимайте закладку на странице аукциона или KBB.', 6000);
    return;
  }
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
  /* KBB-автопилот: окно программы открывает kbb.com с машинами лотов в адресе (#la=…).
     Для каждой: список комплектаций со страницы модели → похожая на трим лота →
     страница цены этой комплектации (пробег лота, ZIP 92620, Private Party, Good) → файл в «Загрузки».
     Всё в вашем браузере, по одному запросу за раз, с паузой между машинами. */
  /* Машины — в адресе (#la=…) или в имени вкладки (window.name), если kbb.com при переадресации потерял хвост адреса. */
  var laMatch = /[#&]la=([^&]+)/.exec(location.hash) || /^la=(.+)$/.exec(window.name || '');
  /* Расширение «Lot Analyzer KBB» запускает этот код само на каждой странице kbb.com — работаем, только если есть машины из окна. */
  if (window.lotAnalyzerAuto && !(auction === 'KBB' && laMatch)) { return; }
  if (auction === 'KBB' && !laMatch && !/Private Party|privateparty|Sell it yourself|valuations\(/i.test(document.documentElement.innerHTML)) {
    say('На этой странице KBB нет цены и нет машин из программы. Откройте KBB из окна программы: «получить KBB ↗» в строке машины или «KBB для лучших 15» — и нажмите закладку на открывшейся вкладке.', 12000);
    return;
  }
  if (auction === 'KBB' && laMatch) {
    window.name = '';
    if (location.hash.indexOf('la=') >= 0) { history.replaceState(null, '', location.pathname + location.search); }
    var cars = [];
    try { cars = JSON.parse(decodeURIComponent(laMatch[1])); } catch (e) { cars = []; }
    var slugOf = function (text) { return String(text || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, ''); };
    var styleWords = /^(sedan|sport|utility|suv|pickup|truck|hatchback|coupe|wagon|van|minivan|convertible|cab|crew|extended|regular|double|quad|super|supercrew|supercab|\d+d|awd|fwd|rwd|4wd|2wd)$/;
    var nextData = function (html) {
      var found = /<script[^>]*id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/.exec(html);
      if (!found) { return null; }
      try { return JSON.parse(found[1]); } catch (e) { return null; }
    };
    var trimsOf = function (data) {
      var out = [];
      var seen = {};
      var walk = function (node, depth) {
        if (!node || typeof node !== 'object' || depth > 12) { return; }
        if (node.__typename === 'Trim' && node.name && node.vehicleId && !seen[node.vehicleId]) { seen[node.vehicleId] = 1; out.push({ name: node.name, id: node.vehicleId }); }
        Object.keys(node).forEach(function (key) { walk(node[key], depth + 1); });
      };
      walk(data, 0);
      return out;
    };
    var pickTrim = function (trims, want) {
      if (trims.length === 1) { return trims[0]; }
      var wanted = slugOf(want).replace('2-5i', '25i').replace('2-0t', '20t').replace('1-5t', '15t').split('-').filter(function (w) { return w && w !== 'base' && w !== 'w'; });
      var score = function (trim) {
        var words = slugOf(trim.name).split('-');
        var hits = wanted.filter(function (w) { return words.indexOf(w) >= 0; }).length;
        var extra = words.filter(function (w) { return wanted.indexOf(w) < 0 && !styleWords.test(w); }).length;
        return hits * 100 - extra;
      };
      var ranked = trims.slice().sort(function (a, b) { return score(b) - score(a); });
      if (!wanted.length || score(ranked[0]) < 100 || score(ranked[0]) === score(ranked[1])) { return null; }
      return ranked[0];
    };
    /* Private Party Good из страницы KBB: блок valuations во встроенных данных (с нужным пробегом)
       или, если его нет, середина диапазона «Sell it yourself» из текста страницы. */
    var privateParty = function (html, miles, url) {
      var data = nextData(html);
      var root = data && data.props && data.props.apolloState && data.props.apolloState.ROOT_QUERY;
      var keys = root ? Object.keys(root).filter(function (k) { return k.indexOf('valuations(') === 0; }) : [];
      for (var i = 0; i < keys.length; i++) {
        var params = {};
        try { params = JSON.parse(keys[i].slice('valuations('.length, -1)); } catch (e) { params = {}; }
        if (String(params.mileage) !== String(miles)) { continue; }
        var prices = (root[keys[i]] && root[keys[i]].prices) || [];
        for (var j = 0; j < prices.length; j++) {
          if (prices[j].priceType === 'Private Party' && prices[j].condition === 'Good' && prices[j].configuredValue) { return prices[j].configuredValue; }
        }
      }
      var text = html.replace(/<script[\s\S]*?<\/script>/gi, ' ').replace(/<[^>]+>/g, ' ').replace(/&nbsp;/g, ' ').replace(/\s+/g, ' ');
      var shown = Number(miles).toLocaleString('en-US');
      if (/pricetype=private-party/.test(url) && text.indexOf('currently ' + shown) >= 0) {
        var range = /Sell it yourself\s*\$([\d,]+)\s*[-–]\s*\$([\d,]+)/.exec(text);
        if (range) { return Math.round((Number(range[1].replace(/,/g, '')) + Number(range[2].replace(/,/g, ''))) / 2); }
      }
      return 0;
    };
    var pause = function (ms) { return new Promise(function (done) { setTimeout(done, ms); }); };
    var getText = function (url) { return fetch(url, { credentials: 'include' }).then(function (r) { if (!r.ok) { throw new Error('KBB ответил ' + r.status); } return r.text(); }); };
    var saveFile = function (html, url, car) {
      var body = html.replace(/<script[^>]*\bsrc=[^>]*>\s*<\/script>/gi, '').replace(/<link[^>]*>/gi, '');
      var head = '<!DOCTYPE html>\n<!-- saved-by: lot_analyzer bookmarklet; saved-at: ' + new Date().toISOString() + '; lot-vin: ' + car.v + '; url: https://www.kbb.com' + url + ' -->\n';
      var blob = new Blob([head + body], { type: 'text/html;charset=utf-8' });
      var link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = 'KBB_' + [car.y, slugOf(car.mk), slugOf(car.md), car.v].join('_') + '.html';
      document.body.appendChild(link);
      link.click();
      setTimeout(function () { URL.revokeObjectURL(link.href); link.remove(); }, 3000);
    };
    var one = async function (car) {
      var query = '?intent=trade-in-sell&mileage=' + car.mi + '&zipcode=92620';
      var models = [slugOf(car.md), slugOf(car.md).replace(/-/g, ''), slugOf(car.md).split('-')[0]].filter(function (m, i, all) { return m && all.indexOf(m) === i; });
      var trims = [];
      var modelSlug = '';
      for (var n = 0; n < models.length && !trims.length; n++) {
        var path = '/' + slugOf(car.mk) + '/' + models[n] + '/' + car.y + '/';
        var html = location.pathname === path ? document.documentElement.outerHTML : await getText(path + query).catch(function () { return ''; });
        trims = trimsOf(nextData(html));
        modelSlug = models[n];
      }
      if (!trims.length) { throw new Error('на KBB не нашлось комплектаций'); }
      var trim = pickTrim(trims, car.t);
      if (!trim) {
        var answer = prompt(car.y + ' ' + car.mk + ' ' + car.md + ' «' + (car.t || '?') + '», ' + car.mi + ' миль — какая комплектация на KBB?\n' + trims.map(function (t, i) { return (i + 1) + ') ' + t.name; }).join('\n') + '\nНомер:');
        trim = trims[(parseInt(answer, 10) || 0) - 1];
        if (!trim) { throw new Error('комплектация не выбрана'); }
      }
      /* Страница цены: пробуем несколько адресов, сохраняем только ту, где есть Private Party Good для пробега лота. */
      var base = '/' + slugOf(car.mk) + '/' + modelSlug + '/' + car.y + '/' + slugOf(trim.name) + '/';
      var tail = 'mileage=' + car.mi + '&zipcode=92620';
      var urls = [base + '?intent=trade-in-sell&' + tail, base + '?' + tail, base + '?vehicleid=' + trim.id + '&intent=trade-in-sell&pricetype=private-party&condition=good&' + tail];
      var tried = [];
      for (var u = 0; u < urls.length; u++) {
        var page = '';
        try { page = await getText(urls[u]); } catch (e) { tried.push(e.message); continue; }
        var price = privateParty(page, car.mi, urls[u]);
        if (price) { saveFile(page, urls[u], car); return trim.name + ': $' + price.toLocaleString('en-US'); }
        tried.push('нет цены для ' + car.mi + ' миль');
        if (u < urls.length - 1) { await pause(1500); }
      }
      throw new Error(trim.name + ' — ' + tried.join(', '));
    };
    (async function () {
      var done = [];
      var failed = [];
      for (var c = 0; c < cars.length; c++) {
        var car = cars[c];
        var label = car.y + ' ' + car.mk + ' ' + car.md;
        say('KBB: ' + (c + 1) + ' из ' + cars.length + ' — ' + label + '…');
        try { done.push(label + ' → ' + await one(car)); } catch (e) { failed.push(label + ': ' + e.message); }
        if (c < cars.length - 1) { await pause(2500 + Math.random() * 2000); }
      }
      say('KBB готово: ' + done.length + ' из ' + cars.length + '. Вернитесь в окно программы — цены уже там.' + (failed.length ? ' Не получилось: ' + failed.join('; ') : ''), 15000);
      /* Отчёт для окна программы: что получилось и что нет — видно внизу окна, в списке файлов. */
      var report = '<!DOCTYPE html>\n<!-- saved-by: lot_analyzer bookmarklet; saved-at: ' + new Date().toISOString() + '; url: https://www.kbb.com/lot-analyzer-report -->\n' +
        '<html><body><pre id="kbb-report">' + ('KBB-автопилот: ' + done.length + ' из ' + cars.length + '\n' + done.map(function (d) { return 'OK  ' + d; }).concat(failed.map(function (f) { return 'НЕТ ' + f; })).join('\n')).replace(/&/g, '&amp;').replace(/</g, '&lt;') + '</pre></body></html>';
      var stamp = new Date().toISOString().replace(/[-:]/g, '').slice(0, 15);
      var rep = document.createElement('a');
      rep.href = URL.createObjectURL(new Blob([report], { type: 'text/html;charset=utf-8' }));
      rep.download = 'KBB_report_' + stamp + '.html';
      document.body.appendChild(rep);
      rep.click();
    })();
    return;
  }
  var hasPages = auction === 'Manheim' && document.querySelector('.pagination__control--next') && document.querySelector('.stockwave-vehicle-info');
  if (hasPages && confirm('Manheim: собрать ВСЕ страницы результатов в один файл?\nОК — все страницы (около 3 секунд на страницу), Отмена — только эту.')) {
    var back = document.querySelector('.pagination__control--page-1');
    if (back && !/selected/.test(back.className)) { var b0 = firstKey(); back.click(); var w0 = 0; var wait0 = function () { w0 += 400; if (firstKey() !== b0 || w0 > 15000) { setTimeout(collectAll, 800); } else { setTimeout(wait0, 400); } }; setTimeout(wait0, 400); } else { collectAll(); }
  } else { save(null); }
})();
