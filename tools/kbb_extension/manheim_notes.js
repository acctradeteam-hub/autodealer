/* Lot Analyzer: в заметку (Add Note) машин в результатах поиска Manheim — KBB с датой расчёта, ваша ставка
   и замечания аукциона, как в Notes на CarMax. Только машинам, у которых в программе есть настоящий KBB или ставка.
   Ваш текст заметки остаётся. Окно программы должно быть запущено; пишет, пока эта вкладка открыта на экране. */
(function () {
  var tries = {};
  var busy = false;
  var toast = null;
  var say = function (text, hide) {
    if (!toast || !toast.isConnected) {
      toast = document.createElement('div');
      toast.setAttribute('style', 'position:fixed;z-index:2147483647;left:16px;bottom:16px;max-width:420px;padding:10px 14px;background:#1f6f43;color:#fff;font:13px/1.4 system-ui,sans-serif;border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.3)');
      document.body.appendChild(toast);
    }
    toast.textContent = text;
    if (hide) { var t = toast; toast = null; setTimeout(function () { t.remove(); }, hide); }
  };
  var pause = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
  var OWN = /^(LA |CarMax: |Manheim: |ACV: |ADESA: |Был: )/;
  /* То же правило, что на CarMax: строка KBB с датой (тот же KBB — не трогаем), MP — заменяется, замечания аукциона — свежие. */
  var merged = function (current, note) {
    var parts = note.split('\n');
    var kbbLine = parts.filter(function (l) { return /^KBB /.test(l); })[0] || '';
    var mpLine = parts.filter(function (l) { return /^MP /.test(l); })[0] || '';
    var rest = parts.filter(function (l) { return l !== kbbLine && l !== mpLine; });
    var lines = String(current || '').split('\n').filter(function (l) { return !OWN.test(l); });
    var add = [];
    if (kbbLine) {
      var num = (/^KBB ([\d,]+)\$/.exec(kbbLine) || [])[1] || '';
      var same = new RegExp('^\\s*KBB\\s*(PP\\s*)?\\$?\\s*' + num.replace(/,/g, ',?') + '\\s*\\$?(\\s|$)', 'i');
      var kept = lines.some(function (l) { return same.test(l) && /\d{1,2}\/\d{1,2}/.test(l); });
      if (!kept) { lines = lines.filter(function (l) { return !same.test(l); }); add.push(kbbLine); }
    }
    if (mpLine) { lines = lines.filter(function (l) { return !/^\s*MP\s*\$?\s*[\d,]+\s*\$?\s*$/i.test(l); }); add.push(mpLine); }
    var own = lines.join('\n').replace(/\s+$/, '');
    add = add.concat(rest);
    return (own && add.length ? own + '\n' : own) + add.join('\n');
  };
  /* Машины на странице: строка результата с JSON объявления (VIN и текущая заметка). */
  var cars = function () {
    var out = [];
    var rows = document.querySelectorAll('.stockwave-vehicle-row, [data-test-id="search-results-row"], .SearchResultsDetailView');
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      var info = row.querySelector('.stockwave-vehicle-info');
      var data = null;
      try { data = info ? JSON.parse(info.textContent) : null; } catch (e) { data = null; }
      var vin = data && data.vin ? String(data.vin).toUpperCase() : ((/\b[A-HJ-NPR-Z0-9]{17}\b/.exec(row.textContent || '') || [])[0] || '');
      var button = row.querySelector('[data-test-id="add-note-button"], [data-test-id="edit-note-button"], .add-note-button--prism');
      if (!button) {
        var all = row.querySelectorAll('button');
        for (var b = 0; b < all.length; b++) { if (/^\s*(add|edit|view)?\s*note\s*$/i.test(all[b].textContent || '')) { button = all[b]; break; } }
      }
      if (vin && button && !out.some(function (c) { return c.vin === vin; })) { out.push({ vin: vin, row: row, button: button, note: data && typeof data.note === 'string' ? data.note : '' }); }
    }
    return out;
  };
  var visible = function (el) { return !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length)); };
  var openArea = function () {
    var areas = document.querySelectorAll('textarea, [contenteditable="true"]');
    for (var i = areas.length - 1; i >= 0; i--) { if (visible(areas[i]) && !areas[i].readOnly && areas[i].getAttribute('aria-hidden') !== 'true') { return areas[i]; } }
    return null;
  };
  var saveButton = function (area) {
    var box = area.closest('[role="dialog"], .modal, .modal-dialog, [class*="Modal"], [class*="modal"], [class*="Note"], [class*="note"]') || area.parentNode;
    for (var up = 0; up < 6 && box; up++, box = box.parentNode) {
      var btns = box.querySelectorAll ? box.querySelectorAll('button') : [];
      for (var j = 0; j < btns.length; j++) {
        if (/^\s*(save|save note|add note|update|update note|submit|done|ok|сохранить)\s*$/i.test(btns[j].textContent || '') && !btns[j].disabled) { return btns[j]; }
      }
    }
    return null;
  };
  var typeInto = function (area, text) {
    area.focus();
    if (area.isContentEditable) {
      document.execCommand('selectAll', false, null);
      document.execCommand('insertText', false, text);
      return;
    }
    area.select();
    if (!document.execCommand('insertText', false, text) || area.value !== text) {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(area, text);
      area.dispatchEvent(new Event('input', { bubbles: true }));
    }
  };
  var lastReport = '';
  var report = function (data) {
    data.url = location.href.split('#')[0];
    data.site = 'Manheim';
    var key = JSON.stringify(data);
    if (key === lastReport) { return; }
    lastReport = key;
    try { chrome.runtime.sendMessage({ type: 'notes-report', report: data }, function () { void chrome.runtime.lastError; }); } catch (e) { /* расширение обновили */ }
  };
  var stale = false;
  /* Одна машина: открыть «Add Note», вписать, «Save», дождаться, пока окно заметки закроется. */
  var writeOne = async function (car, note) {
    car.button.scrollIntoView({ block: 'center' });
    car.button.click();
    var area = null;
    for (var w = 0; w < 25 && !area; w++) { await pause(200); area = openArea(); }
    if (!area) { return 'нет окна заметки'; }
    var current = area.isContentEditable ? area.innerText : area.value;
    var text = merged(current || car.note, note);
    var max = Number(area.getAttribute('maxlength')) || 0;
    if (max && text.length > max) { text = text.slice(0, max); }
    if (text !== current) { typeInto(area, text); }
    var btn = null;
    for (var s = 0; s < 15 && !btn; s++) { await pause(150); btn = saveButton(area); }
    if (!btn) { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); return 'нет кнопки Save'; }
    btn.click();
    for (var c = 0; c < 25 && area.isConnected && visible(area); c++) { await pause(200); }
    await pause(400);
    return area.isConnected && visible(area) ? 'окно не закрылось' : '';
  };
  var run = async function () {
    if (busy || stale || document.visibilityState !== 'visible') { return; }
    var list = cars();
    if (!list.length) { return; }
    busy = true;
    try {
      var answer;
      try {
        answer = await new Promise(function (r, fail) {
          try { chrome.runtime.sendMessage({ type: 'notes' }, function (a) { void chrome.runtime.lastError; r(a); }); } catch (e) { fail(e); }
        });
      } catch (e) {
        stale = true;
        say('Lot Analyzer: расширение обновлено — обновите эту страницу Manheim (⌘R), чтобы KBB снова записывался в заметки.');
        return;
      }
      if (!answer || !answer.ok) { say('Lot Analyzer: окно программы не отвечает — запустите программу, KBB в заметки Manheim запишется сам.', 8000); return; }
      var ready = list.filter(function (c) { return answer.notes[c.vin]; });
      var done = 0, same = 0, failed = 0, why = '';
      var progress = function (busyNow) { report({ fields: list.length, in_program: Object.keys(answer.notes).length, ready: ready.length, written: done, already: same, failed: failed, busy: busyNow ? 1 : 0, note: why && !done && failed ? 'Manheim: ' + why : '' }); };
      progress(true);
      for (var i = 0; i < ready.length; i++) {
        var car = ready[i];
        var note = answer.notes[car.vin];
        /* Уже записано: заметка (из данных или видна в строке) содержит эту строку KBB. */
        var kbbLine = (note.split('\n').filter(function (l) { return /^KBB /.test(l); })[0] || '').replace(/\s\S+$/, '');
        var shown = car.note + '\n' + (car.row.textContent || '');
        /* Все строки программы (кроме даты KBB) уже видны в строке — записано. */
        var rest = note.split('\n').filter(function (l) { return !/^KBB /.test(l); });
        if ((kbbLine ? shown.indexOf(kbbLine) >= 0 : true) && rest.every(function (l) { return shown.indexOf(l) >= 0; }) || merged(car.note, note) === car.note) { same += 1; continue; }
        if ((tries[car.vin] || 0) >= 2 || document.activeElement && /TEXTAREA|INPUT/.test(document.activeElement.tagName)) { continue; }
        tries[car.vin] = (tries[car.vin] || 0) + 1;
        say('Lot Analyzer: пишу KBB, ставку и замечания аукциона в заметку Manheim — ' + car.vin + '…');
        var err = await writeOne(car, note);
        if (err) { failed += 1; why = err; } else { done += 1; }
        if ((done + failed) % 5 === 0) { progress(true); }
      }
      if (done) { say('Lot Analyzer: KBB с датой, ставка и замечания записаны в заметки Manheim у ' + done + ' машин.', 6000); }
      progress(false);
    } finally { busy = false; }
  };
  setTimeout(run, 4000);
  setInterval(run, 20000);
})();
