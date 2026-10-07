/* Закладка «Сохранить для анализа»: один клик на странице аукциона (watch list,
   карточка лота) — и страница сохраняется в «Загрузки» как .html для lot_analyzer.
   Работает в вашем браузере, под вашим входом на сайт; никуда ничего не отправляет.
   Правила записи: только полные строки-комментарии, после каждой команды «;» —
   build.py склеивает код в одну строку. */
(function () {
  /* Версия закладки: пишется в сохранённый файл — окно программы предупредит, если закладка устарела. */
  var LA_VERSION = '2026-10-07.1';
  var host = location.hostname.toLowerCase();
  var auction = /carmax/.test(host) ? 'CarMax' : /acvauctions/.test(host) ? 'ACV' : /manheim|coxauto/.test(host) ? 'Manheim' : /adesa|openlane/.test(host) ? 'ADESA' : /kbb\.com/.test(host) ? 'KBB' : 'auction';
  var toast = null;
  var say = function (text, hide) {
    if (!toast || !toast.isConnected) {
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
  var saveDiag = '';
  var save = function (parts, lot, total) {
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
  var name = lot ? lot.name : auction + '_' + kind + (parts ? '_all' + parts.length : '') + '_' + stamp + '.html';
  var html = '<!DOCTYPE html>\n<!-- saved-by: lot_analyzer bookmarklet; version: ' + LA_VERSION + '; saved-at: ' + now.toISOString() + (saveDiag ? '; diag: ' + saveDiag.replace(/--/g, '-').replace(/>/g, ')') : '') + (lot ? '; lot-vin: ' + lot.vin : '') + '; url: ' + location.href.replace(/--/g, '-') + ' -->\n' + copy.outerHTML;
  var blob = new Blob([html], { type: 'text/html;charset=utf-8' });
  var link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = name;
  document.body.appendChild(link);
  link.click();
  setTimeout(function () { URL.revokeObjectURL(link.href); link.remove(); }, 2000);
  var vins = {};
  (document.body.innerText.match(/\b[A-HJ-NPR-Z0-9]{17}\b/g) || []).forEach(function (v) { vins[v] = 1; });
  var count = total || (parts ? parts.length : Object.keys(vins).length);
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
  /* Manheim «все страницы» без листания: машины на страницу приходят из API Cox Automotive (listings-search).
     Перехватываем один такой запрос (нажатие «следующая»), дальше сами запрашиваем все страницы тем же
     запросом — от вашего входа, страница при этом не перерисовывается. Поэтому сбор идёт и когда вкладка
     в фоне: можно уйти на другую вкладку, только не закрывайте эту. Не вышло — листаем кнопкой, как раньше. */
  var apiList = function (obj, depth) {
    if (!obj || typeof obj !== 'object' || depth > 5) { return null; }
    if (Array.isArray(obj)) {
      if (obj.length && obj[0] && typeof obj[0] === 'object' && /^[A-HJ-NPR-Z0-9]{17}$/.test(String(obj[0].vin || ''))) { return obj; }
      for (var a = 0; a < obj.length && a < 5; a++) { var inner = apiList(obj[a], depth + 1); if (inner) { return inner; } }
      return null;
    }
    for (var k in obj) { if (Object.prototype.hasOwnProperty.call(obj, k)) { var got = apiList(obj[k], depth + 1); if (got) { return got; } } }
    return null;
  };
  var apiTotal = function (obj, depth) {
    if (!obj || typeof obj !== 'object' || Array.isArray(obj) || depth > 4) { return 0; }
    for (var k in obj) {
      if (!Object.prototype.hasOwnProperty.call(obj, k)) { continue; }
      if (/^(total|totalcount|totalresults|totalhits|numfound|totallistings|totalrecords|resultcount|count)$/i.test(k) && typeof obj[k] === 'number') { return obj[k]; }
      var deeper = apiTotal(obj[k], depth + 1);
      if (deeper) { return deeper; }
    }
    return 0;
  };
  /* Поле номера страницы / сдвига в адресе (?page=2) или в теле запроса (JSON). */
  var PAGE_KEY = /^(page|pagenumber|pagenum|pageindex|currentpage|pageno)$/i;
  var OFFSET_KEY = /^(start|offset|from|skip|startindex|startrow)$/i;
  var findPaging = function (obj, size, path, out) {
    if (!obj || typeof obj !== 'object' || path.length > 4) { return out; }
    for (var k in obj) {
      if (!Object.prototype.hasOwnProperty.call(obj, k)) { continue; }
      var v = obj[k];
      var n = typeof v === 'number' ? v : (/^\d+$/.test(String(v)) ? Number(v) : NaN);
      if (PAGE_KEY.test(k) && (n === 1 || n === 2)) { out.push({ path: path.concat(k), kind: 'page', base: n - 1, str: typeof v === 'string' }); }
      if (OFFSET_KEY.test(k) && n === size) { out.push({ path: path.concat(k), kind: 'offset', base: 0, str: typeof v === 'string' }); }
      if (v && typeof v === 'object') { findPaging(v, size, path.concat(k), out); }
    }
    return out;
  };
  var setPath = function (obj, path, value) {
    var o = obj;
    for (var i = 0; i < path.length - 1; i++) { o = o[path[i]]; }
    o[path[path.length - 1]] = value;
  };
  var collectApi = function (fallback) {
    var captured = null;
    var XO = XMLHttpRequest.prototype.open, XS = XMLHttpRequest.prototype.send, XH = XMLHttpRequest.prototype.setRequestHeader, F = window.fetch;
    var restore = function () { XMLHttpRequest.prototype.open = XO; XMLHttpRequest.prototype.send = XS; XMLHttpRequest.prototype.setRequestHeader = XH; window.fetch = F; };
    var offer = function (req, text) {
      if (captured || !text || text.indexOf('"vin"') < 0) { return; }
      try { var json = JSON.parse(text); var list = apiList(json, 0); if (list && list.length) { captured = { req: req, json: json, list: list }; } } catch (e) { /* не JSON */ }
    };
    XMLHttpRequest.prototype.open = function (method, url) { this.laReq = { method: method, url: String(url), headers: {}, body: null }; return XO.apply(this, arguments); };
    XMLHttpRequest.prototype.setRequestHeader = function (k, v) { if (this.laReq) { this.laReq.headers[k] = v; } return XH.apply(this, arguments); };
    XMLHttpRequest.prototype.send = function (body) {
      var x = this;
      if (x.laReq) { x.laReq.body = typeof body === 'string' ? body : null; x.addEventListener('load', function () { try { offer(x.laReq, x.responseType === '' || x.responseType === 'text' ? x.responseText : JSON.stringify(x.response)); } catch (e) { /* пропускаем */ } }); }
      return XS.apply(this, arguments);
    };
    window.fetch = function (input, init) {
      var req = { method: (init && init.method) || (input && input.method) || 'GET', url: String((input && input.url) || input), headers: {}, body: init && typeof init.body === 'string' ? init.body : null };
      var h = (init && init.headers) || (input && input.headers);
      if (h && typeof h.forEach === 'function') { h.forEach(function (v, k) { req.headers[k] = v; }); } else if (h) { for (var hk in h) { req.headers[hk] = h[hk]; } }
      return F.apply(this, arguments).then(function (resp) { try { resp.clone().text().then(function (t) { offer(req, t); }); } catch (e) { /* пропускаем */ } return resp; });
    };
    var next = document.querySelector('.pagination__control--next');
    if (!next) { restore(); fallback(); return; }
    say('Manheim: ищу, откуда страница берёт машины…');
    next.click();
    var waited = 0;
    var wait = function () {
      waited += 300;
      if (!captured && waited < 20000) { setTimeout(wait, 300); return; }
      restore();
      if (!captured) { fallback(); return; }
      run(captured).catch(function () { fallback(); });
    };
    setTimeout(wait, 300);
    var run = async function (cap) {
      var size = cap.list.length;
      var req = cap.req;
      var url = new URL(req.url, location.href);
      /* Запрашиваем только у самого Manheim / Cox Automotive (тот же адрес, что спрашивала страница) — никуда больше. */
      if (url.protocol !== 'https:' || !/(^|\.)(manheim\.com|coxautoinc\.com)$/.test(url.hostname)) { throw new Error('чужой адрес'); }
      var body = null;
      try { body = req.body ? JSON.parse(req.body) : null; } catch (e) { body = null; }
      var query = {};
      url.searchParams.forEach(function (v, k) { query[k] = v; });
      var sizeKey = function (o) { for (var k in o) { if (/^(size|pagesize|limit|rows|perpage|pagelength)$/i.test(k) && Number(o[k]) > 0) { return Number(o[k]); } } return 0; };
      size = sizeKey(query) || (body ? sizeKey(body) : 0) || size;
      var inQuery = findPaging(query, size, [], []);
      var inBody = body ? findPaging(body, size, [], []) : [];
      var pick = inQuery[0] ? { where: 'query', p: inQuery[0] } : inBody[0] ? { where: 'body', p: inBody[0] } : null;
      if (!pick) { throw new Error('нет поля страницы'); }
      var total = apiTotal(cap.json, 0);
      var pages = total ? Math.ceil(total / size) : 100;
      var seen = {};
      var parts = [];
      var add = function (list) { list.forEach(function (item) { var key = (item.id || '') + '|' + item.vin; if (!seen[key]) { seen[key] = 1; parts.push(JSON.stringify(item)); } }); };
      for (var i = 0; i < pages && i < 150; i++) {
        var value = pick.p.kind === 'page' ? pick.p.base + i : i * size;
        if (pick.p.str) { value = String(value); }
        var u = new URL(url.href);
        var b = body ? JSON.parse(JSON.stringify(body)) : null;
        if (pick.where === 'query') { u.searchParams.set(pick.p.path[0], String(value)); } else { setPath(b, pick.p.path, value); }
        var resp = await F.call(window, u.href, { method: req.method, headers: req.headers, body: b ? JSON.stringify(b) : req.body, credentials: 'include' });
        if (!resp.ok) { if (parts.length) { break; } throw new Error('ответ ' + resp.status); }
        var list = apiList(await resp.json(), 0) || [];
        add(list);
        say('Manheim: страница ' + (i + 1) + (total ? ' из ' + pages : '') + ', машин ' + parts.length + '. Можно перейти на другую вкладку — только не закрывайте эту.');
        if (!list.length || (!total && list.length < size)) { break; }
      }
      saveDiag = 'manheim-api: ' + parts.length + (total ? ' of ' + total : '') + ', pages ' + Math.min(pages, 150) + ', field ' + pick.where + ':' + pick.p.path.join('.');
      save(parts);
    };
  };
  /* KBB-автопилот: окно программы открывает kbb.com с машинами лотов в адресе (#la=…).
     Для каждой: список комплектаций со страницы модели → похожая на трим лота →
     страница цены этой комплектации (пробег лота, ZIP 92620, Private Party, Good) → файл в «Загрузки».
     Всё в вашем браузере, по одному запросу за раз, с паузой между машинами. */
  /* Машины — в адресе (#la=…) или в имени вкладки (window.name), если kbb.com при переадресации потерял хвост адреса. */
  var laMatch = /[#&]la=([^&]+)/.exec(location.hash) || /^la=(.+)$/.exec(window.name || '');
  /* Автопилот в расширении переходит по страницам KBB; где остановился — хранится в имени вкладки (la-run=…). */
  var resume = /^la-run=(.+)$/.exec(window.name || '');
  /* Расширение «Lot Analyzer KBB» запускает этот код само на каждой странице kbb.com — работаем, только если есть машины из окна. */
  if (window.lotAnalyzerAuto && !(auction === 'KBB' && (laMatch || resume))) { return; }
  if (auction === 'KBB' && !laMatch && !resume && !/Private Party|privateparty|Sell it yourself|valuations\(/i.test(document.documentElement.innerHTML)) {
    say('На этой странице KBB нет цены и нет машин из программы. Откройте KBB из окна программы: «получить KBB ↗» в строке машины или «KBB: 15 + 5 + 5 + 5 лучших» — и нажмите закладку на открывшейся вкладке.', 12000);
    return;
  }
  if (auction === 'KBB' && (laMatch || resume)) {
    window.name = '';
    if (location.hash.indexOf('la=') >= 0) { history.replaceState(null, '', location.pathname + location.search); }
    var state = null;
    try { state = resume ? JSON.parse(decodeURIComponent(resume[1])) : { cars: JSON.parse(decodeURIComponent(laMatch[1])), i: 0, done: [], failed: [], pending: null }; } catch (e) { state = { cars: [], i: 0, done: [], failed: [], pending: null }; }
    var cars = state.cars;
    /* Модели, которые на KBB называются иначе, чем на аукционах. */
    var KBB_MODEL = { 'bolt': 'bolt-ev', 'bolt-euv': 'bolt-euv', 'gti': 'golf-gti', 'golf-gti': 'golf-gti', 'e-golf': 'e-golf', 'leaf-plus': 'leaf', 'ioniq-electric': 'ioniq', 'niro-ev': 'niro-ev', 'kona-electric': 'kona-electric', 'clarity-plug-in-hybrid': 'clarity-plug-in-hybrid', 'prius-prime': 'prius-prime', 'rav4-prime': 'rav4-prime' };
    var KBB_EXTRA = { 'prius-plug-in-hybrid': 'prius-plug-in', 'prius-plug-in': 'prius-plug-in-hybrid' };
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
    /* Комплектация KBB под трим лота. Вопросов не задаём (автопилот работает без присмотра):
       при равенстве — самый частый кузов: седан, SUV, пикап, хэтчбек, универсал, минивэн, купе, кабриолет. */
    var bodyOrder = ['sedan', 'sport-utility', 'suv', 'crew-cab', 'pickup', 'hatchback', 'wagon', 'minivan', 'van', 'coupe', 'convertible'];
    var premium = /^(long|performance|plaid|dual|awd|4wd|4x4|limited|platinum|signature|prestige|launch|first|edition|gt|gts|turbo|hybrid|plug|prime|max)$/;
    /* Комплектация «по умолчанию», если трим лота на KBB не нашёлся: самая простая (без дорогих версий). */
    var baseTrim = function (trims) {
      var cost = function (trim) {
        var words = slugOf(trim.name).split('-');
        var body = bodyOrder.findIndex(function (b) { return slugOf(trim.name).indexOf(b) >= 0; });
        return words.filter(function (w) { return premium.test(w); }).length * 100 + words.filter(function (w) { return !styleWords.test(w); }).length * 2 + (body < 0 ? bodyOrder.length : body);
      };
      return trims.slice().sort(function (a, b) { return cost(a) - cost(b); })[0];
    };
    var pickTrim = function (trims, want) {
      if (trims.length === 1) { return trims[0]; }
      /* «Range» сам по себе ничего не значит (Standard Range / Long Range у Tesla) — решают «standard» / «long». */
      var wanted = slugOf(want).replace('2-5i', '25i').replace('2-0t', '20t').replace('1-5t', '15t').split('-').filter(function (w) { return w && w !== 'base' && w !== 'w' && w !== 'range'; });
      var score = function (trim) {
        var slug = slugOf(trim.name);
        var words = slug.split('-');
        var hits = wanted.filter(function (w) { return words.indexOf(w) >= 0; }).length;
        /* Дорогие версии (Long Range, Performance, AWD …) — только если они есть у лота: иначе сильный штраф. */
        var extra = words.filter(function (w) { return wanted.indexOf(w) < 0 && !styleWords.test(w); }).reduce(function (sum, w) { return sum + (premium.test(w) ? 15 : 1); }, 0);
        var body = bodyOrder.findIndex(function (b) { return slug.indexOf(b) >= 0; });
        return hits * 1000 - extra * 20 - (body < 0 ? bodyOrder.length : body);
      };
      var ranked = trims.slice().sort(function (a, b) { return score(b) - score(a); });
      if (wanted.length && score(ranked[0]) < 0) { return null; }
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
    /* Запрос страницы того же kbb.com; не дольше 25 секунд, чтобы автопилот не зависал. */
    var getText = function (url) {
      var stop = new AbortController();
      var timer = setTimeout(function () { stop.abort(); }, 25000);
      return fetch(url, { credentials: 'include', signal: stop.signal }).then(function (r) { clearTimeout(timer); if (!r.ok) { throw new Error('KBB ответил ' + r.status); } return r.text(); }, function (e) { clearTimeout(timer); throw new Error(e.name === 'AbortError' ? 'KBB не ответил за 25 секунд' : 'нет связи с KBB'); });
    };
    var saveFile = function (html, url, car) {
      var body = html.replace(/<script[^>]*\bsrc=[^>]*>\s*<\/script>/gi, '').replace(/<link[^>]*>/gi, '');
      var head = '<!DOCTYPE html>\n<!-- saved-by: lot_analyzer bookmarklet; version: ' + LA_VERSION + '; saved-at: ' + new Date().toISOString() + '; lot-vin: ' + car.v + '; url: https://www.kbb.com' + url + ' -->\n';
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
      /* Как модель называется на KBB: аукцион пишет «Bolt», KBB — «Bolt EV»; гибриды у KBB часто отдельной моделью
         («camry-hybrid»). Пробуем варианты по очереди, пока страница не покажет комплектации. */
      /* Старые строки CarMax: «Model» + трим «X 75D» → модель «Model X». EUV, Plug-in — у KBB отдельные модели. */
      if (/^model$/i.test(car.md) && /^[3sxy]\b/i.test(car.t || '')) { car.md = 'Model ' + car.t.charAt(0).toUpperCase(); car.t = car.t.slice(1).trim(); }
      var lead = /^(euv|plug[- ]?in(?: hybrid)?|prime|electric)\b/i.exec(car.t || '');
      if (lead) { car.md = car.md + ' ' + lead[1]; car.t = car.t.slice(lead[0].length).trim(); }
      var md = slugOf(car.md);
      var hybrid = /hybrid/i.test(car.md + ' ' + car.t);
      var models = [KBB_MODEL[md] || '', (KBB_EXTRA[md] || '')].concat([hybrid && md.indexOf('hybrid') < 0 ? md + '-hybrid' : '', md, md.replace(/-/g, ''), md.split('-')[0],
        md + '-ev', md.split('-')[0] + (hybrid ? '-hybrid' : '-ev')]).filter(function (m, i, all) { return m && all.indexOf(m) === i; });
      var trims = [];
      var modelSlug = '';
      for (var n = 0; n < models.length && !trims.length; n++) {
        var path = '/' + slugOf(car.mk) + '/' + models[n] + '/' + car.y + '/';
        var html = location.pathname === path ? document.documentElement.outerHTML : await getText(path + query).catch(function () { return ''; });
        trims = trimsOf(nextData(html));
        /* Гибрид, EUV, Plug-in, Prime, Electric не подменяем другой версией: страница должна быть именно этой модели
           (в адресе модели или в названиях комплектаций). Иначе ищем дальше. */
        var must = (/(hybrid|euv|plug|prime|electric)/i.exec(car.md + ' ' + car.t) || [])[1];
        if (must && trims.length && models[n].indexOf(must.toLowerCase()) < 0 && !trims.some(function (t) { return new RegExp(must, 'i').test(t.name); })) { trims = []; }
        modelSlug = models[n];
      }
      if (!trims.length) { throw new Error('на KBB не нашлось комплектаций (пробовал: ' + models.join(', ') + ') — впишите KBB вручную'); }
      var trim = pickTrim(trims, car.t);
      var byDefault = !trim;
      if (byDefault) { trim = baseTrim(trims); }
      var trimLabel = trim.name + (byDefault ? ' (комплектация по умолчанию — у лота «' + (car.t || '?') + '», проверьте)' : '');
      /* Страница цены: пробуем несколько адресов, сохраняем только ту, где есть Private Party Good для пробега лота. */
      var base = '/' + slugOf(car.mk) + '/' + modelSlug + '/' + car.y + '/' + slugOf(trim.name) + '/';
      var tail = 'mileage=' + car.mi + '&zipcode=92620';
      var urls = [base + '?intent=trade-in-sell&' + tail, base + '?' + tail, base + '?vehicleid=' + trim.id + '&intent=trade-in-sell&pricetype=private-party&condition=good&' + tail];
      var tried = [];
      for (var u = 0; u < urls.length; u++) {
        var page = '';
        try { page = await getText(urls[u]); } catch (e) { tried.push(e.message); continue; }
        var price = privateParty(page, car.mi, urls[u]);
        if (price) { saveFile(page, urls[u], car); return trimLabel + ': $' + price.toLocaleString('en-US'); }
        tried.push('нет цены для ' + car.mi + ' миль');
        if (u < urls.length - 1) { await pause(1500); }
      }
      /* В скачанной странице цены нет — KBB рисует её в браузере. Расширение откроет страницу цены во вкладке и дождётся её. */
      if (window.lotAnalyzerAuto) { return { navigate: urls[2], trim: trimLabel }; }
      throw new Error(trim.name + ' — ' + tried.join(', ') + ' (поставьте расширение «Lot Analyzer KBB» — оно дождётся цены на странице)');
    };
    /* Мы на странице цены после перехода: ждём, пока KBB нарисует цену «Sell it yourself» для пробега лота. */
    var renderedPrice = async function (car) {
      var rangeNow = function () { var m = /Sell it yourself\s*(\$[\d,]+\s*[-–]\s*\$[\d,]+)/.exec(document.body.innerText || ''); return m ? m[1] : ''; };
      var clicked = false;
      var tradeRange = null;
      var last = null;
      var same = 0;
      for (var t = 0; t < 60; t++) {
        var tab = document.getElementById('ymmt-pricing-tab-private');
        if (tab && tab.getAttribute('aria-selected') === 'false') {
          /* Открыта вкладка Trade-In: запоминаем её цифры и переключаемся на «Sell it yourself». */
          if (!clicked) { tradeRange = rangeNow(); tab.click(); clicked = true; }
        } else {
          var price = privateParty(document.documentElement.outerHTML, car.mi, location.href);
          var shownRange = rangeNow();
          /* Берём цену, только когда цифры сменились после переключения и не меняются две проверки подряд. */
          if (price && !(clicked && shownRange === tradeRange)) {
            if (shownRange === last) { same += 1; } else { same = 0; last = shownRange; }
            if (same >= 2) { return price; }
          }
        }
        await pause(500);
      }
      return 0;
    };
    var labelOf = function (car) { return car.y + ' ' + car.mk + ' ' + car.md; };
    (async function () {
      var done = state.done;
      var failed = state.failed;
      if (state.pending && cars[state.i]) {
        /* Вернулись на страницу цены после перехода: ждём цену, сохраняем страницу как есть. */
        var current = cars[state.i];
        say('KBB: ' + (state.i + 1) + ' из ' + cars.length + ' — ' + labelOf(current) + ': жду цену на странице…');
        var shownPrice = await renderedPrice(current);
        if (shownPrice) {
          save(null, { vin: current.v, name: 'KBB_' + [current.y, slugOf(current.mk), slugOf(current.md), current.v].join('_') + '.html' });
          done.push(labelOf(current) + ' → ' + state.pending.trim + ': $' + shownPrice.toLocaleString('en-US'));
        } else {
          failed.push(labelOf(current) + ': ' + state.pending.trim + ' — на странице KBB нет Private Party для ' + current.mi + ' миль');
        }
        state.pending = null;
        state.i += 1;
        await pause(1500);
      }
      for (; state.i < cars.length; state.i++) {
        var car = cars[state.i];
        say('KBB: ' + (state.i + 1) + ' из ' + cars.length + ' — ' + labelOf(car) + '…');
        try {
          var result = await one(car);
          if (result && result.navigate) {
            state.pending = { trim: result.trim };
            window.name = 'la-run=' + encodeURIComponent(JSON.stringify(state));
            location.href = result.navigate;
            return;
          }
          done.push(labelOf(car) + ' → ' + result);
        } catch (e) { failed.push(labelOf(car) + ': ' + e.message); }
        if (state.i < cars.length - 1) { await pause(2500 + Math.random() * 2000); }
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
      /* Готово — вкладка KBB закрывается сама (через 4 секунды, чтобы файлы успели сохраниться). */
      setTimeout(function () {
        if (window.lotAnalyzerAuto && window.chrome && chrome.runtime && chrome.runtime.id) { chrome.runtime.sendMessage({ type: 'close' }); } else { window.close(); }
      }, 4000);
    })();
    return;
  }
  /* CarMax показывает часть машин и кнопку «Show more / Показать следующие машины». Жмём её, пока машин
     прибавляется; если кнопки нет — прокручиваем вниз (подгрузка при прокрутке). Карточки запоминаем по VIN:
     если сайт уберёт верхние при прокрутке, в файл они всё равно попадут. */
  var listPage = auction === 'CarMax' ? !/\/vehicledetail\//.test(location.pathname) :
    (auction === 'ACV' || auction === 'ADESA') && !/\/details\//.test(location.pathname) && document.querySelectorAll(auction === 'ACV' ? 'a[href*="/auction/"]' : 'a[href*="/details/"]').length >= 2;
  if (listPage) {
    var cmCards = {};
    var cmOrder = [];
    /* Карточка CarMax — блок с номером машины в id; ключ — VIN (вид «Detailed table»), иначе этот номер. */
    var cmList = function () {
      var out = [];
      var divs = document.querySelectorAll('div[id]');
      for (var i = 0; i < divs.length; i++) {
        var card = divs[i];
        if (!/^\d{5,}$/.test(card.id) || card.closest('[role="presentation"], [role="dialog"]') || (card.parentNode.closest && card.parentNode.closest('div[id]') && /^\d{5,}$/.test(card.parentNode.closest('div[id]').id))) { continue; }
        var btn = card.querySelector('[data-testid="copy-vin-button"]');
        var vin = btn ? (btn.parentNode.textContent || '').replace(/\s+/g, '').toUpperCase() : '';
        out.push({ key: /^[A-HJ-NPR-Z0-9]{17}$/.test(vin) ? vin : 'id' + card.id, el: card });
      }
      return out.length ? out : cmByVin();
    };
    /* ACV / ADESA: карточка — самый большой блок вокруг ссылки на лот, где других лотов нет. */
    var lotRe = auction === 'ACV' ? /\/auction\/(\d+)/ : /\/details\/([0-9a-f]{8,})/;
    var lotList = function () {
      var out = [];
      var seen = {};
      var links = document.querySelectorAll('a[href]');
      for (var i = 0; i < links.length; i++) {
        var m = lotRe.exec(links[i].getAttribute('href') || '');
        if (!m || seen[m[1]] || links[i].closest('[role="presentation"], [role="dialog"], #auction-detail, [data-testid="ds-carousel-wrapper"]')) { continue; }
        var el = links[i];
        while (el.parentNode && el.parentNode !== document.body) {
          var inner = el.parentNode.querySelectorAll('a[href]');
          var other = false;
          for (var j = 0; j < inner.length && !other; j++) { var mm = lotRe.exec(inner[j].getAttribute('href') || ''); other = !!(mm && mm[1] !== m[1]); }
          if (other) { break; }
          el = el.parentNode;
        }
        seen[m[1]] = 1;
        out.push({ key: m[1], el: el });
      }
      return out;
    };
    /* Страница другого вида (список аукциона, таблица): карточка — самый большой блок вокруг VIN, где этот VIN один. */
    var isVin = function (v) { return /\d/.test(v) && /[A-Z]/.test(v); };
    var cmByVin = function () {
      var out = [];
      var seen = {};
      var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      var node = walker.nextNode();
      for (; node; node = walker.nextNode()) {
        var found = /\b[A-HJ-NPR-Z0-9]{17}\b/.exec(node.nodeValue || '');
        if (!found || !isVin(found[0]) || seen[found[0]]) { continue; }
        var el = node.parentNode;
        if (!el || !el.closest || el.closest('[role="presentation"], [role="dialog"], script, style, textarea')) { continue; }
        while (el.parentNode && el.parentNode !== document.body) {
          var text = el.parentNode.textContent || '';
          var vins = (text.match(/\b[A-HJ-NPR-Z0-9]{17}\b/g) || []).filter(isVin);
          if (vins.some(function (v) { return v !== found[0]; }) || text.length > 5000) { break; }
          el = el.parentNode;
        }
        seen[found[0]] = 1;
        out.push({ key: found[0], el: el });
      }
      return out;
    };
    var cards = function () { return auction === 'CarMax' ? cmList() : lotList(); };
    var cmGrab = function () {
      var list = cards();
      for (var i = 0; i < list.length; i++) {
        if (!cmCards[list[i].key]) { cmOrder.push(list[i].key); }
        cmCards[list[i].key] = list[i].el.outerHTML;
      }
      return cmOrder.length;
    };
    var cmVisible = function (el) { var r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && !el.disabled && el.getAttribute('aria-disabled') !== 'true'; };
    /* Кнопка «ещё машины»: обычная, своя CarMax (hzn-button) или «следующая страница» постраничного списка. */
    /* Кнопки, нажатие которых не добавило машин (например «See More» боковой панели ACV): больше не нажимаем, листаем. */
    var cmDead = [];
    var cmMoreButton = function () {
      var all = document.querySelectorAll('button, a, [role="button"], hzn-button, hzn-text-link');
      for (var i = 0; i < all.length; i++) {
        var el = all[i];
        if (cmDead.indexOf(el) >= 0) { continue; }
        var text = (el.textContent || '').replace(/\s+/g, ' ').trim();
        var label = el.getAttribute('aria-label') || '';
        if (text.length > 60 || el.closest('[role="presentation"], [role="dialog"]') || el.hasAttribute('disabled') || /disabled/i.test(el.className || '')) { continue; }
        if (/^(more|ещё|еще)\s*\d*$/i.test(text) || /^see more$/i.test(label)) { continue; }
        if ((/^(show|load|see|view)\s+(more|next)|more (vehicles|cars|results)|next \d+ (vehicles|cars|results)|показать (ещё|еще|больше|следующ)|загрузить (ещё|еще)|следующие машины|следующие \d+/i.test(text) ||
             /go to next page|^next page$|следующая страница/i.test(label)) && cmVisible(el)) { return el; }
      }
      return null;
    };
    var cmPress = function (el) {
      var inner = el.shadowRoot && el.shadowRoot.querySelector('button');
      (inner || el).click();
    };
    /* Прокрутка вниз шагами (не прыжком в конец): списки, которые рисуют только видимые машины (ACV), иначе
       пропускают середину. Сначала — до последней видимой карточки; если сдвига нет — на экран вниз. */
    /* Прокрутка шагами: каждый блок от карточки до самой страницы, у которого ниже есть содержимое
       (обычная прокрутка, своя полоса прокрутки ACV, сама страница), + «колесо мыши» над списком.
       Что и насколько сдвинулось — в diag (пишется в сохранённый файл, чтобы разобрать, если не долистало). */
    var diag = [];
    var nameOf = function (el) { return el === document.scrollingElement ? 'page' : el.tagName + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : ''); };
    var cmScrollDown = function () {
      var list = cards();
      var last = list.length ? list[list.length - 1].el : null;
      var boxes = [];
      for (var up = last && last.parentNode; up && up.nodeType === 1; up = up.parentNode) {
        if (up.scrollHeight > up.clientHeight + 40 && up.clientHeight > 100) { boxes.push(up); }
      }
      var root = document.scrollingElement || document.documentElement;
      if (boxes.indexOf(root) < 0) { boxes.push(root); }
      var tops = function () { return boxes.map(function (box) { return box.scrollTop; }); };
      var shift = function (was) { var now = tops(); return now.reduce(function (sum, v, i) { return sum + Math.abs(v - was[i]); }, 0); };
      var moved = [];
      var was = tops();
      /* 1) к последней показанной карточке: обычный список — сразу в конец (там сайт подгружает следующие). */
      if (last) { last.scrollIntoView({ block: 'end' }); }
      if (shift(was) >= 40) { moved.push('к последней ' + Math.round(shift(was))); }
      /* 2) сдвинулось мало (виртуальный список: последняя карточка уже почти видна) — на экран вниз каждый блок с прокруткой. */
      if (shift(was) < window.innerHeight * 0.5) {
        boxes.forEach(function (box) {
          var from = box.scrollTop;
          box.scrollTop = from + Math.max(200, Math.min(box.clientHeight, window.innerHeight) * 0.8);
          if (box.scrollTop !== from) { moved.push(nameOf(box) + ' ' + Math.round(from) + '→' + Math.round(box.scrollTop)); }
        });
      }
      /* 3) и это не помогло — «колесо мыши» над списком (свои полосы прокрутки). */
      if (!moved.length && last) {
        last.dispatchEvent(new WheelEvent('wheel', { deltaY: 900, deltaMode: 0, bubbles: true, cancelable: true }));
        moved.push('wheel');
      }
      if (diag.length < 40) { diag.push(cmOrder.length + ':' + moved.join(',')); }
    };
    var cmClicks = 0;
    var cmScrolls = 0;
    /* Сколько машин обещает сам сайт («171 / 15,695 results» у ACV, «28 live» у CarMax): пока не собрали — листаем терпеливее. */
    var cmExpected = function () {
      var text = document.body.innerText || '';
      var m = /(\d[\d,]*)\s*\/\s*[\d,]+\s*results/i.exec(text) || /(\d[\d,]*)\s+live\s*\|/i.exec(text) || /(\d[\d,]*)\s+(?:results|vehicles)\b/i.exec(text);
      return m ? Number(m[1].replace(/,/g, '')) : 0;
    };
    var cmPatience = function () { var want = cmExpected(); return want && cmOrder.length < want ? 8 : 3; };
    var cmStep = function () {
      var before = cmGrab();
      say(auction + ': загружено машин ' + before + '… не трогайте страницу');
      var more = cmMoreButton();
      var want = cmExpected();
      if (want) { say(auction + ': загружено машин ' + before + ' из ' + want + '… не трогайте страницу'); }
      if (more && cmClicks < 400) { cmClicks += 1; more.scrollIntoView({ block: 'center' }); cmPress(more); } else if (cmScrolls < cmPatience()) { cmScrolls += 1; cmScrollDown(); } else { cmFinish(); return; }
      var waited = 0;
      var poll = function () {
        waited += 500;
        if (cmGrab() > before) { cmScrolls = 0; setTimeout(cmStep, 600); } else if (more && waited >= 12000) { cmDead.push(more); if (diag.length < 40) { diag.push(cmOrder.length + ':кнопка «' + (more.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 30) + '» не добавила машин'); } setTimeout(cmStep, 0); } else if (!more && waited >= (cmScrolls > 2 ? 4000 : 2500)) { setTimeout(cmStep, 0); } else { setTimeout(poll, 500); }
      };
      setTimeout(poll, 500);
    };
    var cmFinish = function () {
      cmGrab();
      /* Карточки, которых уже нет на странице, — в скрытый блок, чтобы файл содержал все машины. */
      var box = document.createElement('div');
      box.id = 'lot-analyzer-carmax-all';
      box.setAttribute('style', 'display:none');
      var onPage = {};
      cards().forEach(function (c) { onPage[c.key] = 1; });
      box.innerHTML = cmOrder.filter(function (v) { return !onPage[v]; }).map(function (v) { return cmCards[v]; }).join('');
      if (box.innerHTML) { document.body.appendChild(box); }
      saveDiag = 'expected ' + cmExpected() + ', got ' + cmOrder.length + ', clicks ' + cmClicks + ', steps ' + diag.join(' | ');
      save(null, null, cmOrder.length);
      if (box.parentNode) { box.parentNode.removeChild(box); }
    };
    /* В виде «плитка» CarMax не показывает VIN — переключаем на «Detailed table» и ждём VIN в карточках. */
    var detailed = document.querySelector('[data-testid="detailed"]');
    var hasVins = function () { return cmList().some(function (c) { return c.key.slice(0, 2) !== 'id'; }); };
    if (auction === 'CarMax' && detailed && detailed.getAttribute('aria-pressed') !== 'true' && !hasVins()) {
      say('CarMax: переключаю на вид «Detailed table», чтобы были VIN…');
      detailed.click();
      var waitedView = 0;
      var viewPoll = function () { waitedView += 500; if (hasVins() || waitedView >= 15000) { setTimeout(cmStep, 800); } else { setTimeout(viewPoll, 500); } };
      setTimeout(viewPoll, 500);
    } else { cmStep(); }
    return;
  }
  var hasPages = auction === 'Manheim' &&document.querySelector('.pagination__control--next') && document.querySelector('.stockwave-vehicle-info');
  /* Прежний способ — листать кнопкой (вкладку держать открытой на экране): с первой страницы. */
  var byClicking = function () {
    say('Manheim: собираю листанием страниц — оставайтесь на этой вкладке.');
    var back = document.querySelector('.pagination__control--page-1');
    if (back && !/selected/.test(back.className)) { var b0 = firstKey(); back.click(); var w0 = 0; var wait0 = function () { w0 += 400; if (firstKey() !== b0 || w0 > 15000) { setTimeout(collectAll, 800); } else { setTimeout(wait0, 400); } }; setTimeout(wait0, 400); } else { collectAll(); }
  };
  if (hasPages && confirm('Manheim: собрать ВСЕ страницы результатов в один файл?\nОК — все страницы (можно уйти на другую вкладку, только не закрывайте эту), Отмена — только эту.')) {
    collectApi(byClicking);
  } else { save(null); }
})();
