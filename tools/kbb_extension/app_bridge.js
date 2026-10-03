/* Окно программы (127.0.0.1:8765) ↔ расширение: ссылки окна открываются соседней вкладкой в фоне —
   вы остаётесь в окне программы. Окно узнаёт о расширении по data-la-ext. */
document.documentElement.setAttribute('data-la-ext', '1');
window.addEventListener('message', function (e) {
  if (e.source !== window || !e.data || e.data.source !== 'lot-analyzer' || e.data.type !== 'open-bg') { return; }
  chrome.runtime.sendMessage({ type: 'open-bg', url: String(e.data.url || ''), cars: String(e.data.cars || '') });
});
