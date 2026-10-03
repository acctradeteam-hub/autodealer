/* Lot Analyzer: ваша ставка из окна программы — в форму «Set early bid» на странице лота CarMax.
   Окно открывает https://www.carmaxauctions.com/vehicledetail/<stock>#la-bid=<сумма>. Здесь: «Bid early» →
   сумма (вниз до шага $50) → подсветка. Способ оплаты и «Place bid» — ВЫ: сами ставку программа не ставит. */
(function () {
  var m = /[#&]la-bid=(\d+)/.exec(location.hash);
  try {
    if (m) { sessionStorage.setItem('la-bid', m[1]); } else { m = [null, sessionStorage.getItem('la-bid') || '']; }
  } catch (e) { m = m || [null, '']; }
  if (!m || !m[1]) { return; }
  var amount = Math.floor(Number(m[1]) / 50) * 50;
  if (!amount) { return; }
  var toast = null;
  var say = function (text, tone) {
    if (!toast || !toast.isConnected) {
      toast = document.createElement('div');
      document.body.appendChild(toast);
    }
    toast.setAttribute('style', 'position:fixed;z-index:2147483647;left:16px;bottom:16px;max-width:460px;padding:12px 16px;color:#fff;font:14px/1.45 system-ui,sans-serif;border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.3);background:' + (tone === 'bad' ? '#9b2c2c' : '#1f6f43'));
    toast.textContent = text;
  };
  var pause = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
  var text = function (el) { return (el.textContent || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim(); };
  var find = function (selector, re) {
    var all = document.querySelectorAll(selector);
    for (var i = 0; i < all.length; i++) { if (re.test(text(all[i]))) { return all[i]; } }
    return null;
  };
  var dialog = function () { return find('[role="dialog"]', /set early bid|max bid|proxy/i); };
  /* Поле суммы: hzn-currency-input; настоящий <input> — внутри (shadow DOM), если он открыт. */
  var fill = function (box) {
    var host = box.querySelector('hzn-currency-input, [data-testid="currency-input"] *');
    var input = (host && host.shadowRoot && host.shadowRoot.querySelector('input')) || box.querySelector('input');
    var value = String(amount);
    if (input) {
      input.focus();
      if (input.select) { input.select(); }
      if (!document.execCommand('insertText', false, value)) {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value);
      }
      input.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
      input.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
      input.blur();
      return /\d/.test(input.value) && input.value.replace(/\D/g, '').indexOf(value) === 0;
    }
    if (host) {
      host.value = '$' + value;
      host.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
      host.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
      return true;
    }
    return false;
  };
  (async function () {
    var box = null;
    for (var t = 0; t < 40 && !box; t++) {
      box = dialog();
      if (!box) {
        var open = find('button, hzn-button, [role="button"]', /^(bid early|set early bid|edit (early )?bid|change bid)$/i);
        if (open) { open.click(); await pause(800); box = dialog(); }
      }
      if (!box) { await pause(500); }
    }
    try { sessionStorage.removeItem('la-bid'); } catch (e) { /* нет — не страшно */ }
    if (!box) { say('Lot Analyzer: не нашёл на странице кнопку «Bid early» — ставка $' + amount.toLocaleString('en-US') + ', поставьте её вручную.', 'bad'); return; }
    var ok = fill(box);
    box.style.outline = '4px solid #f2b600';
    box.style.outlineOffset = '4px';
    say(ok ? 'Lot Analyzer: вписал ставку $' + amount.toLocaleString('en-US') + (amount !== Number(m[1]) ? ' (вниз до шага $50)' : '') +
             '. Проверьте машину и сумму, выберите способ оплаты и нажмите «Place bid» сами.'
           : 'Lot Analyzer: окно ставки открыто, но поле суммы не заполнилось — впишите $' + amount.toLocaleString('en-US') + ' вручную, выберите оплату и нажмите «Place bid».', ok ? '' : 'bad');
  })();
})();
