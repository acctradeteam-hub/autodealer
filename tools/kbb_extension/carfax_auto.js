/* Lot Analyzer: Carfax сам. Каждой машине, у которой программа получила KBB (вручную, пачкой, «KBB на неделю» —
   при любой цене), — отчёт Carfax. Раз в минуту (и сразу после готового отчёта) спрашиваем программу о следующей
   машине (/api/carfax_next) и открываем её отчёт на Carfax Online во вкладке в фоне — как кнопка «📄 Carfax»
   (laCarfaxOrder в background.js). По одной; нужен вход на Carfax Online, три ошибки подряд, лимит за сутки — пауза
   (решает программа, причина видна в окне). На сайте ничего не нажимаем: только открываем адрес отчёта. */
var LA_CFA_ALARM = 'la-carfax-auto';
var laCfaBusy = false;
function laCarfaxAutoTick() {
  if (laCfaBusy) { return; }
  laCfaBusy = true;
  fetch('http://127.0.0.1:8765/api/carfax_next', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LA': '1' }, body: '{}' })
    .then(function (r) { return r.json(); })
    .then(function (d) { if (d && d.vin) { laCarfaxOrder(d.vin, true); } })
    .catch(function () { /* программа не запущена — спросим через минуту */ })
    .then(function () { laCfaBusy = false; });
}
/* Отчёт по авто-заказу готов (или не вышел) — следующую машину через 25 с, не дожидаясь будильника. */
function laCarfaxAutoDone() { setTimeout(laCarfaxAutoTick, 25000); }
chrome.alarms.get(LA_CFA_ALARM, function (a) { if (!a) { chrome.alarms.create(LA_CFA_ALARM, { delayInMinutes: 1, periodInMinutes: 1 }); } });
chrome.alarms.onAlarm.addListener(function (a) { if (a.name === LA_CFA_ALARM) { laCarfaxAutoTick(); } });
