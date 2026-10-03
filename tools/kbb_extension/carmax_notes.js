/* Lot Analyzer: в Notes карточек CarMax (watch list, вид «Detailed table») — KBB с датой расчёта
   и замечания самого аукциона («CarMax: Major engine defect, …»), чтобы они остались в заметке и после торгов.
   Только машинам, у которых в программе есть настоящий KBB. Ваш текст заметки остаётся.
   Окно программы должно быть запущено. Поле, в котором вы сейчас печатаете, не трогается. */
(function () {
  var PREFIX = 'LA ';
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
  var vinOf = function (card) {
    var btn = card.querySelector('[data-testid="copy-vin-button"]');
    var text = btn ? btn.parentNode.textContent : card.textContent;
    var m = /\b[A-HJ-NPR-Z0-9]{17}\b/.exec((text || '').replace(/\s+/g, ' '));
    return m ? m[0] : '';
  };
  var cardOf = function (el) {
    var c = el;
    while (c && !(c.tagName === 'DIV' && /^\d{5,}$/.test(c.id || ''))) { c = c.parentNode; }
    return c;
  };
  /* Строки, которые пишет программа (и прежний формат «LA …»): при пересчёте заменяются. */
  var OWN = /^(LA |CarMax: |Manheim: |ACV: |ADESA: )/;
  var merged = function (current, note) {
    var parts = note.split('\n');
    var kbbLine = parts.filter(function (l) { return /^KBB /.test(l); })[0] || '';
    var mpLine = parts.filter(function (l) { return /^MP /.test(l); })[0] || '';
    var rest = parts.filter(function (l) { return l !== kbbLine && l !== mpLine; });
    var lines = current.split('\n').filter(function (l) { return !OWN.test(l); });
    var add = [];
    if (kbbLine) {
      var num = (/^KBB ([\d,]+)\$/.exec(kbbLine) || [])[1] || '';
      var same = new RegExp('^\\s*KBB\\s*(PP\\s*)?\\$?\\s*' + num.replace(/,/g, ',?') + '\\s*\\$?(\\s|$)', 'i');
      /* Тот же KBB уже записан с датой — оставляем как есть (дата — когда посчитали впервые). */
      var kept = lines.some(function (l) { return same.test(l) && /\d{1,2}\/\d{1,2}/.test(l); });
      if (!kept) { lines = lines.filter(function (l) { return !same.test(l); }); add.push(kbbLine); }
    }
    /* Ставка из окна программы заменяет прежние строки «MP …». */
    if (mpLine) { lines = lines.filter(function (l) { return !/^\s*MP\s*\$?\s*[\d,]+\s*\$?\s*$/i.test(l); }); add.push(mpLine); }
    var own = lines.join('\n').replace(/\s+$/, '');
    add = add.concat(rest);
    return (own && add.length ? own + '\n' : own) + add.join('\n');
  };
  var saveButton = function (t) {
    var box = t;
    for (var i = 0; i < 5 && box; i++, box = box.parentNode) {
      var btns = box.querySelectorAll ? box.querySelectorAll('button') : [];
      for (var j = 0; j < btns.length; j++) {
        if (/^\s*(save|сохранить)\s*$/i.test(btns[j].textContent || '') && !btns[j].disabled) { return btns[j]; }
      }
    }
    return null;
  };
  var write = async function (t, text) {
    t.scrollIntoView({ block: 'center' });
    t.focus();
    t.select();
    /* Как будто напечатано: так React у сайта видит новый текст и показывает «Save». */
    if (!document.execCommand('insertText', false, text) || t.value !== text) {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(t, text);
      t.dispatchEvent(new Event('input', { bubbles: true }));
    }
    var btn = null;
    for (var w = 0; w < 20 && !btn; w++) { await pause(150); btn = saveButton(t); }
    if (btn) { btn.click(); } else { t.blur(); }
    await pause(1200);
  };
  var run = async function () {
    if (busy || document.visibilityState !== 'visible') { return; }
    var areas = Array.prototype.filter.call(document.querySelectorAll('textarea[placeholder="Add Notes"], textarea[placeholder*="Note"]'), function (t) { return t.getAttribute('aria-hidden') !== 'true' && !t.readOnly; });
    if (!areas.length) { return; }
    busy = true;
    try {
      var answer = await new Promise(function (r) { chrome.runtime.sendMessage({ type: 'notes' }, r); });
      if (!answer || !answer.ok) { return; }
      var done = 0;
      for (var i = 0; i < areas.length; i++) {
        var t = areas[i];
        var card = cardOf(t);
        var vin = card ? vinOf(card) : '';
        var note = vin && answer.notes[vin];
        if (!note || document.activeElement === t || (tries[vin] || 0) >= 2) { continue; }
        var text = merged(t.value, note);
        if (text === t.value) { continue; }
        tries[vin] = (tries[vin] || 0) + 1;
        say('Lot Analyzer: пишу KBB, ставку и замечания аукциона в Notes — ' + vin + '…');
        await write(t, text);
        done += 1;
      }
      if (done) { say('Lot Analyzer: KBB, ставка и замечания аукциона записаны в Notes у ' + done + ' машин.', 6000); }
    } finally { busy = false; }
  };
  setTimeout(run, 3000);
  setInterval(run, 20000);
})();
