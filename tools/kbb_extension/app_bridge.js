/* Окно программы (127.0.0.1:8765) ↔ расширение: ссылки окна открываются соседней вкладкой в фоне —
   вы остаётесь в окне программы.
   Окно каждые несколько секунд спрашивает «ping»; отвечает только живая связь. После обновления расширения (⟳)
   старая связь в открытом окне «мёртвая» (chrome.runtime недоступен) и молчит, а новая (её подключает фон
   расширения) отвечает и открывает вкладки. Нет ответа — окно открывает ссылки само, обычной вкладкой. */
(function () {
  var alive = function () { try { return !!(chrome.runtime && chrome.runtime.id); } catch (e) { return false; } };
  var mark = function () {
    if (!alive()) { return; }
    document.documentElement.setAttribute('data-la-ext', '1');
    document.documentElement.setAttribute('data-la-ext-v', chrome.runtime.getManifest().version);
    window.dispatchEvent(new Event('la-ext'));
  };
  mark();
  window.addEventListener('message', function (e) {
    if (e.source !== window || !e.data || e.data.source !== 'lot-analyzer' || !alive()) { return; }
    if (e.data.type === 'ping') {
      window.postMessage({ source: 'lot-analyzer-ext', type: 'pong', v: chrome.runtime.getManifest().version }, '*');
      return;
    }
    /* Автосбор CarMax: состояние для окна и кнопка «Собрать сейчас». */
    if (e.data.type === 'auto-status' || e.data.type === 'auto-run') {
      try {
        chrome.runtime.sendMessage({ type: e.data.type, which: String(e.data.which || '') }, function (a) {
          void chrome.runtime.lastError;
          window.postMessage({ source: 'lot-analyzer-ext', type: e.data.type, answer: a || null }, '*');
        });
      } catch (err) { /* связь умерла */ }
      return;
    }
    /* «📄 Carfax» у машины: заказ на vin-reports-pro.com (вкладка в фоне), отчёт — в программу. */
    if (e.data.type === 'carfax-order') {
      try { chrome.runtime.sendMessage({ type: 'carfax-order', vin: String(e.data.vin || '') }); } catch (err) { /* связь умерла */ }
      return;
    }
    /* «📷 Проверить фото»: фон сам откроет карточки лотов «без фото» на CarMax (вкладка в фоне, закроется сама). */
    if (e.data.type === 'photos-start') {
      /* Две живые связи в одной вкладке — одна проверка: первая отмечает запрос как выполненный. */
      var pid = String(e.data.id || '');
      if (pid && document.documentElement.getAttribute('data-la-done') === pid) { return; }
      if (pid) { document.documentElement.setAttribute('data-la-done', pid); }
      try { chrome.runtime.sendMessage({ type: 'photos-start', why: 'кнопка' }); } catch (err) { /* связь умерла */ }
      return;
    }
    if (e.data.type !== 'open-bg') { return; }
    /* Две живые связи в одной вкладке не должны открыть две вкладки: первая отмечает запрос как выполненный. */
    var id = String(e.data.id || '');
    if (id && document.documentElement.getAttribute('data-la-done') === id) { return; }
    if (id) { document.documentElement.setAttribute('data-la-done', id); }
    try { chrome.runtime.sendMessage({ type: 'open-bg', url: String(e.data.url || ''), cars: String(e.data.cars || '') }); } catch (err) { /* связь умерла — окно откроет само */ }
  });
})();
