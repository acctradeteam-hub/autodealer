/* Lot Analyzer: Carfax с vin-reports-pro.com (VinTrace) — сам.
   1) Кнопка «📄 Carfax» в окне программы открывает https://www.vin-reports-pro.com/pro#la-carfax=<VIN>: здесь VIN
      вписывается в поле, выбирается Carfax и нажимается заказ. Нужен вход — просим войти, после входа заказ продолжается
      (VIN помним в этой вкладке).
   2) Страница готового отчёта (/pro/report/<id>) — открытая и после заказа, и вручную: берём его PDF (ссылка «Open PDF»)
      и отдаём программе: она хранит PDF и разбирает (владельцы, ДТП и их сила, штаты, титул) — в окно и в расчёт.
   Вкладку, открытую программой, после этого закрываем; открытую вами — оставляем. */
(function () {
  if (window.top !== window) { return; }
  var KEY = 'la-carfax';
  var job = null;
  try { job = JSON.parse(sessionStorage.getItem(KEY) || 'null'); } catch (e) { job = null; }
  var save = function () { try { if (job) { sessionStorage.setItem(KEY, JSON.stringify(job)); } else { sessionStorage.removeItem(KEY); } } catch (e) { /* нет */ } };
  var hashVin = (/[#&]la-carfax=([A-HJ-NPR-Z0-9]{17})/i.exec(location.hash) || [])[1];
  if (hashVin) {
    job = { vin: hashVin.toUpperCase(), at: Date.now(), step: 'order', fromProgram: true };
    save();
    try { history.replaceState(null, '', location.pathname + location.search); } catch (e) { /* пусть */ }
  }
  if (job && Date.now() - job.at > 40 * 60 * 1000) { job = null; save(); }      // давний заказ — забыть

  var send = function (msg, cb) {
    try { chrome.runtime.sendMessage(msg, function (r) { void chrome.runtime.lastError; if (cb) { cb(r); } }); } catch (e) { if (cb) { cb(null); } }
  };
  var status = function (st, note) { if (job) { send({ type: 'carfax-status', vin: job.vin, status: st, note: note || '' }); } };

  var banner = function (html, bad) {
    var el = document.getElementById('la-carfax-banner');
    if (!el) {
      el = document.createElement('div');
      el.id = 'la-carfax-banner';
      el.style.cssText = 'position:fixed;right:12px;top:12px;z-index:2147483647;max-width:380px;padding:10px 12px;border-radius:8px;' +
        'font:13px/1.4 system-ui,-apple-system,sans-serif;box-shadow:0 6px 20px rgba(0,0,0,.3);color:#fff';
      (document.body || document.documentElement).appendChild(el);
    }
    el.style.background = bad ? '#b45309' : '#1e3a8a';
    el.innerHTML = '<b>Lot Analyzer · Carfax</b><br>' + html;
    return el;
  };
  var esc = function (s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); };

  var visible = function (el) {
    var r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden';
  };
  var labelOf = function (el) {
    var lab = el.id ? document.querySelector('label[for="' + (window.CSS && CSS.escape ? CSS.escape(el.id) : el.id) + '"]') : null;
    return [el.placeholder, el.name, el.id, el.getAttribute('aria-label'), lab && lab.textContent, el.closest('label') && el.closest('label').textContent].join(' ');
  };
  var vinInput = function () {
    var list = [].slice.call(document.querySelectorAll('input, textarea')).filter(function (el) {
      return !/^(hidden|password|email|checkbox|radio|submit|button|file|search)$/i.test(el.type || '') && visible(el) && !el.disabled && !el.readOnly;
    });
    return list.filter(function (el) { return /\bvins?\b|vin\s*(number|#|code)|vehicle identification/i.test(labelOf(el)); })[0] ||
      list.filter(function (el) { return Number(el.maxLength) === 17; })[0] || (list.length === 1 ? list[0] : null);
  };
  var setValue = function (el, v) {                         // React / Next: значение — через «родной» сеттер и событие input
    var proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };
  var textOf = function (b) { return String(b.textContent || b.value || b.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim(); };
  var BAD = /sign\s*out|log\s*out|cancel|close|share|\bpdf\b|autocheck|delete|remove|back|settings|account|help|×/i;
  var chooseCarfax = function () {                            // выбор отчёта на странице (Carfax / AutoCheck) — Carfax
    [].slice.call(document.querySelectorAll('select')).forEach(function (sel) {
      var opt = [].slice.call(sel.options).filter(function (o) { return /carfax/i.test(o.textContent); })[0];
      if (opt && sel.value !== opt.value) { setValue(sel, opt.value); }
    });
    [].slice.call(document.querySelectorAll('input[type=radio]')).forEach(function (r) {
      if (!r.checked && /carfax/i.test(labelOf(r) + ' ' + r.value)) { r.click(); }
    });
  };
  var orderButton = function (input) {
    var btns = [].slice.call(document.querySelectorAll('button, input[type=submit], [role=button]')).filter(function (b) {
      return visible(b) && !b.disabled && !BAD.test(textOf(b));
    });
    var form = input && input.form;
    return btns.filter(function (b) { return /carfax/i.test(textOf(b)); })[0] ||
      (form ? btns.filter(function (b) { return b.form === form && (b.type === 'submit' || b.tagName === 'BUTTON'); })[0] : null) ||
      btns.filter(function (b) { return /\b(get|run|order|pull|request|search|submit|check|look\s*up|go|generate|create)\b/i.test(textOf(b)); })[0] || null;
  };
  var confirmIfAsked = function (tries) {                     // «This will use 1 credit — Continue?» — подтвердить один раз
    var dialog = [].slice.call(document.querySelectorAll('[role=dialog], [role=alertdialog], .modal, dialog[open]')).filter(visible)[0];
    var ok = dialog && [].slice.call(dialog.querySelectorAll('button, [role=button]')).filter(function (b) {
      return visible(b) && !b.disabled && /\b(confirm|yes|continue|order|purchase|buy|ok|proceed|run|get)\b/i.test(textOf(b)) && !BAD.test(textOf(b));
    })[0];
    if (ok) { ok.click(); return; }
    if (tries > 0) { setTimeout(function () { confirmIfAsked(tries - 1); }, 500); }
  };

  var manual = function () {
    status('manual');
    var el = banner('Не нашёл на странице, куда вписать VIN. Вставьте его сами и закажите Carfax — готовый отчёт придёт в программу сам.<br>' +
      '<span style="font:600 15px ui-monospace,monospace">' + esc(job.vin) + '</span> <button type="button" id="la-cf-copy" style="margin-left:6px">Скопировать VIN</button>', true);
    var copy = el.querySelector('#la-cf-copy');
    if (copy) { copy.onclick = function () { try { navigator.clipboard.writeText(job.vin); copy.textContent = 'Скопировано ✓'; } catch (e) { /* нет */ } }; }
    send({ type: 'carfax-show' });
    job.step = 'wait'; job.clickedAt = Date.now(); save();
  };

  var order = function () {
    if (!job || job.step !== 'order') { return; }
    if (/\/pro\/(login|forgot-password)/.test(location.pathname)) {
      banner('Войдите в VinTrace — после входа Carfax для <b>' + esc(job.vin) + '</b> закажется сам.', true);
      status('login');
      send({ type: 'carfax-show' });
      return;                                                // после входа сайт откроет /pro — заказ продолжится
    }
    if (/\/pro\/report\//.test(location.pathname)) { return; }
    banner('Заказываю Carfax для <b>' + esc(job.vin) + '</b>…');
    var started = Date.now();
    var tick = function () {
      if (!job || job.step !== 'order') { return; }
      var input = vinInput();
      if (input) {
        setValue(input, job.vin);
        chooseCarfax();
        setTimeout(function () {
          var btn = orderButton(input);
          if (!btn) { manual(); return; }
          job.step = 'wait'; job.clickedAt = Date.now(); save();
          btn.click();
          confirmIfAsked(10);
          status('ordered');
          banner('Carfax для <b>' + esc(job.vin) + '</b> заказан — жду отчёт…');
        }, 500);
        return;
      }
      if (Date.now() - started > 25000) { manual(); return; }
      setTimeout(tick, 700);
    };
    tick();
  };

  /* Готовый отчёт: PDF — в программу. */
  var done = {};
  var b64 = function (buf) {
    var bytes = new Uint8Array(buf), bin = '';
    for (var i = 0; i < bytes.length; i += 0x8000) { bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000)); }
    return btoa(bin);
  };
  var vinOnPage = function () {
    var text = (document.body && document.body.innerText) || '';
    if (job && text.indexOf(job.vin) >= 0) { return job.vin; }
    var m = text.match(/\b[A-HJ-NPR-Z0-9]{17}\b/g) || [];
    return m.filter(function (v) { return /\d/.test(v) && /[A-Z]/.test(v); })[0] || (job ? job.vin : '');
  };
  var grab = function (id, tries) {
    if (done[id] === 'ok') { return; }
    var link = [].slice.call(document.querySelectorAll('a[href*="/pdf"]')).filter(function (a) { return /\/api\/.*requests\//.test(a.getAttribute('href') || ''); })[0];
    var url = link ? link.href : location.origin + '/api/requests/' + id + '/pdf';
    var vin = vinOnPage();
    done[id] = 'busy';
    fetch(url, { credentials: 'include' }).then(function (r) {
      if (!r.ok) { throw new Error('HTTP ' + r.status); }
      return r.arrayBuffer();
    }).then(function (buf) {
      var head = String.fromCharCode.apply(null, new Uint8Array(buf.slice(0, 4)));
      if (head !== '%PDF' || buf.byteLength < 2000) { throw new Error('PDF ещё не готов'); }
      send({ type: 'carfax-pdf', vin: vin, url: location.href, pdf: b64(buf) }, function (got) {
        if (got && got.ok) {
          done[id] = 'ok';
          banner('✓ Carfax в программе: ' + esc(got.line || 'отчёт прочитан') + ((got.flags || []).length ? '<br>⚠ ' + esc(got.flags.join('; ')) : ''));
          var mine = job && job.fromProgram;
          job = null; save();
          if (mine) { setTimeout(function () { send({ type: 'carfax-close' }); }, 4000); }
        } else {
          done[id] = 'fail';
          banner('Отчёт не попал в программу: ' + esc((got && got.error) || 'программа не ответила') + '. Откройте окно программы и обновите эту страницу.', true);
        }
      });
    }).catch(function (err) {
      done[id] = 'retry';
      if (tries > 0) { setTimeout(function () { grab(id, tries - 1); }, 5000); }
      else { done[id] = 'fail'; banner('Не смог взять PDF отчёта (' + esc(err.message) + '). Нажмите «Open PDF» и сохраните его — или обновите страницу.', true); status('error', 'PDF не скачался'); }
    });
  };

  var lastPath = '';
  setInterval(function () {
    var m = /\/pro\/report\/([\w-]+)/.exec(location.pathname);
    if (m && !done[m[1]]) { grab(m[1], 24); }                 // до 2 минут: отчёт может дорисовываться
    if (location.pathname !== lastPath) {                     // сайт — одностраничный: переходы без перезагрузки
      lastPath = location.pathname;
      if (job && job.step === 'order') { order(); }
    }
    if (job && job.step === 'wait' && !m) {
      // отчёт готов, но сайт остался на списке — открыть строку с нашим VIN
      var link = [].slice.call(document.querySelectorAll('a[href*="/pro/report/"]')).filter(function (a) {
        var row = a.closest('tr, li, [class*="row"], [class*="card"]') || a;
        return (row.textContent || '').indexOf(job.vin) >= 0;
      })[0];
      if (link) { location.href = link.href; }
      else if (Date.now() - (job.clickedAt || job.at) > 4 * 60 * 1000) {
        banner('Отчёт для <b>' + esc(job.vin) + '</b> не открылся сам — откройте его на сайте: он попадёт в программу сам.', true);
        status('error', 'отчёт не открылся — откройте его на сайте');
        send({ type: 'carfax-show' });
        job = null; save();
      }
    }
  }, 1000);
})();
