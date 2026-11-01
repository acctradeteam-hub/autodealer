/* Lot Analyzer: есть ли уже фото у машин «без фото» — по карточкам лотов CarMax (/vehicledetail/<номер>).
   Список поиска CarMax ещё пишет «Check back for images», а на карточке лота фото уже есть. Вкладка CarMax с #la-photos
   (кнопка окна «📷 Проверить фото» или сама — после автосбора аукциона) по очереди открывает карточки невидимым окошком
   (с вашим входом), считает фото и сообщает программе; в конце вкладка закрывается сама. */
(function () {
  if (window.top !== window) { return; }
  var pause = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
  var ask = function (msg) {
    return new Promise(function (r) {
      try { chrome.runtime.sendMessage(msg, function (a) { void chrome.runtime.lastError; r(a); }); } catch (e) { r(null); }
    });
  };
  var isPhotosTab = function () {
    if (/la-photos/.test(location.hash)) { return Promise.resolve(true); }
    return ask({ type: 'photos-tab' }).then(function (a) { return !!(a && a.yes); });
  };
  var box = document.createElement('div');
  box.setAttribute('style', 'position:fixed;z-index:2147483647;right:16px;bottom:16px;max-width:420px;padding:10px 14px;background:#1f6f43;color:#fff;font:13px/1.4 system-ui,sans-serif;border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.3)');
  var say = function (s) { box.textContent = s; if (!box.isConnected) { document.body.appendChild(box); } };
  /* Фото карточки: картинки img2.carmax.com/assets/<номер>/image/…; «Check back for images» — фото нет (0); не дорисовалась — -1. */
  var count = function (doc, stock) {
    var names = Array.prototype.map.call(doc.querySelectorAll('img'), function (i) { return i.currentSrc || i.src || ''; })
      .filter(function (u) { return u.indexOf('/assets/' + stock + '/image/') >= 0; })
      .map(function (u) { return u.split('/image/')[1].split('?')[0]; })
      .filter(function (n, i, all) { return all.indexOf(n) === i; });
    if (names.length) { return { count: names.length, photo: 'https://img2.carmax.com/assets/' + stock + '/image/' + names[0] }; }
    return /check back for images|no images available|images coming soon/i.test((doc.body && doc.body.innerText) || '') ? { count: 0, photo: '' } : null;
  };
  var one = async function (car) {
    var frame = document.createElement('iframe');
    frame.setAttribute('style', 'position:fixed;left:-10000px;top:0;width:1200px;height:900px;opacity:0');
    frame.src = '/vehicledetail/' + car.stock;
    document.body.appendChild(frame);
    var got = null;
    for (var w = 0; w < 20 && !(got && got.count > 0); w++) {
      await pause(1000);
      try { got = count(frame.contentDocument, car.stock) || got; } catch (e) { break; }
    }
    frame.remove();
    return got ? { stock: car.stock, vin: car.vin || '', count: got.count, photo: got.photo } : null;
  };
  (async function () {
    if (!(await isPhotosTab())) { return; }
    say('Lot Analyzer: спрашиваю у программы, у каких машин нет фото…');
    var answer = await ask({ type: 'photos-needed' });
    var list = (answer && answer.cars) || [];
    var found = 0;
    for (var i = 0; i < list.length; i++) {
      say('Lot Analyzer: фото — карточка ' + (i + 1) + ' из ' + list.length + ', фото появились у ' + found + '…');
      var res = await one(list[i]);
      if (res) {
        if (res.count > 0) { found++; }
        await ask({ type: 'photos', cars: [res] });
      }
      await pause(500);
    }
    say(list.length ? 'Lot Analyzer: готово — фото появились у ' + found + ' из ' + list.length + ' машин. Вкладка закроется.' : 'Lot Analyzer: машин без фото для проверки нет.');
    await pause(3000);
    try { chrome.runtime.sendMessage({ type: 'close' }); } catch (e) { /* расширение обновили */ }
  })();
})();
