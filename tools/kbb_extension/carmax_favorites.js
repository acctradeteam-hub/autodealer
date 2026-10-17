/* Lot Analyzer: звёздочка «в избранное» на CarMax → машина в окне программы, KBB запрашивается сам
   (вкладка kbb.com в фоне), а потом KBB с датой пишется в Notes этой машины (carmax_notes.js).
   Звёздочку сняли — машина уходит из списка на печать, и Notes ей программа больше не пишет.
   Звёздочка — кнопка с data-click-event="FavoriteAdd" и номером машины data-vehicle-id. */
(function () {
  var text = function (el) { return el ? String(el.textContent || '').replace(/\s+/g, ' ').trim() : ''; };
  var cardOf = function (el) {
    var c = el;
    while (c && !(c.tagName === 'DIV' && /^\d{5,}$/.test(c.id || ''))) { c = c.parentNode; }
    return c;
  };
  var carFrom = function (box, stock) {
    var vinBtn = box.querySelector('[data-testid="copy-vin-button"]');
    var vin = (/\b[A-HJ-NPR-Z0-9]{17}\b/.exec(vinBtn ? text(vinBtn.parentNode) : text(box)) || [])[0] || '';
    var title = '';
    var parts = box.querySelectorAll('button, h1, h2, h3, p, span');
    for (var i = 0; i < parts.length && !title; i++) { var t = text(parts[i]); if (/^(19|20)\d\d [A-Z][\w-]* \S/.test(t) && t.length < 80) { title = t; } }
    var head = '', miles = '', start = '';
    var ps = box.querySelectorAll('p, span');
    for (var j = 0; j < ps.length; j++) {
      var s = text(ps[j]);
      if (!head && /^[A-Z]{1,2}\/\d+\s*•/.test(s)) { head = s; }
      if (!miles && /^[\d,]+\s*mi\b/.test(s)) { miles = s.split(' mi')[0]; }
      if (!start && /^(Starts|Started) on /.test(s)) { start = s.replace(/^(Starts|Started) on /, ''); }
    }
    var notes = box.querySelector('textarea[placeholder="Add Notes"]');
    var remarks = [];
    var caps = box.querySelectorAll('span[class*="caption"]');
    for (var k = 0; k < caps.length; k++) { var c = text(caps[k]); if (c && !/^Updated/.test(c) && c !== 'No announcement(s)' && remarks.indexOf(c) < 0) { remarks.push(c); } }
    var img = box.querySelector('img');
    return {
      vin: vin, stock: stock, title: title, lane_run: head.split('•')[0].trim(), location: (head.split('•')[1] || '').trim(),
      miles: miles, announcements: remarks.join(', '), notes: notes ? notes.value : '', start: start,
      lot_url: stock ? 'https://www.carmaxauctions.com/vehicledetail/' + stock : location.href.split('#')[0],
      photo: img && /^https?:/.test(img.src || '') ? img.src : ''
    };
  };
  var toast = function (msg) {
    var d = document.createElement('div');
    d.setAttribute('style', 'position:fixed;z-index:2147483647;right:16px;bottom:16px;max-width:380px;padding:10px 14px;background:#1f6f43;color:#fff;font:13px/1.4 system-ui,sans-serif;border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.3)');
    d.textContent = msg;
    document.body.appendChild(d);
    setTimeout(function () { d.remove(); }, 5000);
  };
  document.addEventListener('click', function (e) {
    var star = e.target && e.target.closest ? e.target.closest('[data-click-event]') : null;
    var kind = star ? star.getAttribute('data-click-event') : '';
    if (kind !== 'FavoriteAdd' && kind !== 'FavoriteRemove') { return; }
    var stock = star.getAttribute('data-vehicle-id') || ((/vehicledetail\/(\d+)/.exec(location.pathname) || [])[1] || '');
    var box = cardOf(star) || document.body;
    var car = carFrom(box, stock);
    if (kind === 'FavoriteRemove') {
      /* Звёздочку сняли — машина уходит из списка на печать, и заметку ей программа больше не пишет. */
      try {
        chrome.runtime.sendMessage({ type: 'unfavorite', car: { vin: car.vin, stock: stock } }, function (answer) {
          void chrome.runtime.lastError;
          if (answer && answer.ok) { toast('Lot Analyzer: ' + (car.title || car.vin || stock) + ' — убрана из списка на печать'); }
          else { toast('Lot Analyzer: окно программы не отвечает — машина останется в списке на печать, пока не сохраните эту страницу закладкой.'); }
        });
      } catch (err) { toast('Lot Analyzer: расширение обновлено — обновите эту страницу CarMax (⌘R).'); }
      return;
    }
    if (!car.vin) { return; }
    try {
      chrome.runtime.sendMessage({ type: 'favorite', car: car }, function (answer) {
        void chrome.runtime.lastError;
        if (answer && answer.ok) { toast('Lot Analyzer: ' + (car.title || car.vin) + ' — в программе' + (answer.queued ? ', KBB запрошу сам' : '')); }
        else { toast('Lot Analyzer: окно программы не отвечает — запустите программу и поставьте звёздочку ещё раз.'); }
      });
    } catch (err) { toast('Lot Analyzer: расширение обновлено — обновите эту страницу CarMax (⌘R).'); }
  }, true);
})();
