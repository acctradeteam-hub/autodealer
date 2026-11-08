/* «📷 Проверить фото» — фон расширения. Вкладка CarMax сама по очереди открывает карточки лотов «без фото»
   (carmaxauctions.com/vehicledetail/<номер>), carmax_photos.js на карточке считает фото и отвечает сюда, результат
   уходит в программу. Карточка не открылась (CarMax перекинул на выбор аукциона / вход, страница не ответила) —
   перепроверка в конце, вторым проходом. Три карточки подряд не открылись в самом начале — стоп: вкладка остаётся
   открытой, в окне программы — что показал CarMax (обычно нужно войти на carmaxauctions.com).
   Ход проверки — в окне программы (/api/photos_status). Сама: после автосбора и каждые 3 часа с 8 до 20,
   если в программе есть машины «без фото» с торгами впереди. */
var PHOTO_APP = 'http://127.0.0.1:8765';
var PHOTO_WAIT_MS = 45000;          // на одну карточку: загрузка + до 20 с ожидания фото
var photoJob = null;
var photoStarting = 0;              // старт идёт (программа отвечает списком) — второй запуск не нужен

function photoSave() {
  try { chrome.storage.session.set({ 'photo-job': photoJob ? JSON.stringify(photoJob) : '' }); } catch (e) { /* нет session */ }
}

function photoReport(state, extra) {
  var j = photoJob || {};
  var body = { state: state, total: j.total || 0, done: j.done || 0, found: j.found || 0, failed: (j.failed || []).length,
               pass: j.pass || 1, why: j.why || '', at: Date.now() };
  Object.keys(extra || {}).forEach(function (k) { body[k] = extra[k]; });
  fetch(PHOTO_APP + '/api/photos_status', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LA': '1' }, body: JSON.stringify(body) })
    .catch(function () {});
}

function photoFinish(state, extra) {
  var j = photoJob;
  if (!j) { return; }
  clearTimeout(j.timer);
  clearInterval(j.awake);
  photoReport(state, extra);
  photoJob = null;
  photoSave();
  if (state !== 'blocked') { chrome.tabs.remove(j.tab, function () { void chrome.runtime.lastError; }); }
}

function photoNext() {
  var j = photoJob;
  if (!j) { return; }
  if (!j.queue.length) {
    if (j.pass === 1 && j.failed.length) {                     // перепроверка: не открывшиеся карточки — ещё раз
      j.pass = 2;
      j.queue = j.failed.map(function (f) { return f.car; });
      j.failed = [];
    } else {
      photoFinish('done');
      return;
    }
  }
  var car = j.queue.shift();
  j.current = car;
  photoSave();
  photoReport('running', { current: car.stock });
  chrome.tabs.update(j.tab, { url: 'https://www.carmaxauctions.com/vehicledetail/' + car.stock + '#la-photo' }, function () {
    if (chrome.runtime.lastError) { photoFinish('closed', { note: 'вкладку проверки закрыли' }); }
  });
  clearTimeout(j.timer);
  j.timer = setTimeout(function () { photoResult({ stock: car.stock, error: 'timeout' }); }, PHOTO_WAIT_MS);
}

function photoResult(res) {
  var j = photoJob;
  if (!j || !j.current || String(res.stock) !== String(j.current.stock)) { return; }
  clearTimeout(j.timer);
  var car = j.current;
  j.current = null;
  if (res.error) {
    j.failed.push({ car: car, error: res.error, url: res.url || '' });
    j.misses += 1;
    if (j.misses >= 3 && j.done === 0) {                       // с самого начала ни одна карточка не открылась
      photoFinish('blocked', { url: res.url || '', title: res.title || '', error: res.error });
      return;
    }
  } else {
    j.misses = 0;
    j.done += 1;
    if (res.count > 0) { j.found += 1; }
    fetch(PHOTO_APP + '/api/photos', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cars: [{ stock: car.stock, vin: car.vin || '', count: res.count, photo: res.photo || '' }] }) }).catch(function () {});
  }
  photoSave();
  setTimeout(photoNext, 700);
}

/* Старт: tabId — вкладка, которую уже открыли для проверки (старый адрес #la-photos); иначе — новая, в фоне. */
function photosStart(tabId, why) {
  if ((photoJob && Date.now() - photoJob.started < 40 * 60000) || Date.now() - photoStarting < 30000) {
    if (tabId && (!photoJob || tabId !== photoJob.tab)) { chrome.tabs.remove(tabId, function () { void chrome.runtime.lastError; }); }
    return 'busy';
  }
  photoStarting = Date.now();
  fetch(PHOTO_APP + '/api/photos_needed', { cache: 'no-store' })
    .then(function (r) { return r.json(); })
    .then(function (a) {
      var cars = (a && a.cars) || [];
      if (!cars.length) {
        photoStarting = 0;
        photoReport('done', { total: 0, why: why || '' });
        if (tabId) { chrome.tabs.remove(tabId, function () { void chrome.runtime.lastError; }); }
        return;
      }
      var go = function (tab) {
        photoStarting = 0;
        photoJob = { tab: tab, queue: cars.slice(), total: cars.length, done: 0, found: 0, failed: [], misses: 0, pass: 1,
                     why: why || '', started: Date.now() };
        /* Фон расширения засыпает через 30 с без событий — пока идёт проверка, не даём. */
        photoJob.awake = setInterval(function () { chrome.runtime.getPlatformInfo(function () {}); }, 20000);
        photoNext();
      };
      if (tabId) { go(tabId); return; }
      chrome.tabs.create({ url: 'about:blank', active: false }, function (tab) {
        if (chrome.runtime.lastError || !tab) { photoStarting = 0; photoReport('error', { note: 'не удалось открыть вкладку' }); return; }
        go(tab.id);
      });
    })
    .catch(function () { photoStarting = 0; photoReport('error', { note: 'программа не отвечает' }); });
  return 'started';
}

/* Фон проснулся заново посреди проверки (Chrome его усыплял) — продолжаем с той же карточки. */
try {
  chrome.storage.session.get('photo-job', function (got) {
    var saved = (got || {})['photo-job'];
    if (!saved || photoJob) { return; }
    try { photoJob = JSON.parse(saved); } catch (e) { return; }
    if (!photoJob || Date.now() - photoJob.started > 40 * 60000) { photoJob = null; photoSave(); return; }
    photoJob.awake = setInterval(function () { chrome.runtime.getPlatformInfo(function () {}); }, 20000);
    if (photoJob.current) { photoJob.queue.unshift(photoJob.current); photoJob.current = null; }
    photoNext();
  });
} catch (e) { /* нет session */ }

chrome.runtime.onMessage.addListener(function (msg, sender, reply) {
  if (!msg) { return false; }
  /* Окно программы (кнопка «📷 Проверить фото») или старый адрес #la-photos. */
  if (msg.type === 'photos-start') {
    reply({ started: photosStart(msg.here && sender.tab ? sender.tab.id : null, msg.why || 'кнопка') });
    return false;
  }
  /* Карточка спрашивает: это вкладка проверки и какую машину ждём? */
  if (msg.type === 'photo-job') {
    var j = photoJob;
    var mine = j && sender.tab && sender.tab.id === j.tab && j.current;
    reply(mine ? { stock: j.current.stock, n: j.done + j.failed.length + 1, total: j.total, pass: j.pass } : null);
    return false;
  }
  if (msg.type === 'photo-card' && photoJob && sender.tab && sender.tab.id === photoJob.tab) {
    photoResult(msg);
    return false;
  }
  return false;
});

/* Перепроверка сама: каждые 3 часа с 8 до 20, если есть машины «без фото» с торгами впереди. */
chrome.alarms.get('la-photos', function (a) { if (!a) { chrome.alarms.create('la-photos', { delayInMinutes: 15, periodInMinutes: 180 }); } });
chrome.alarms.onAlarm.addListener(function (a) {
  if (a.name !== 'la-photos') { return; }
  var h = new Date().getHours();
  if (h >= 8 && h < 20 && !photoJob) { photosStart(null, 'авто'); }
});
