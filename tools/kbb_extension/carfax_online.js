/* Lot Analyzer: Carfax Online (carfaxonline.com — сайт Carfax for Dealers). Отчёт по VIN — страница /vhr/<VIN>.
   — Кнопка «📄 Carfax» в окне (на Mac или с телефона): расширение открывает /vhr/<VIN> во вкладке в фоне; эта
     вкладка ждёт, пока отчёт дорисуется, отдаёт его текст программе (владельцы, ДТП, титул, пробег, штаты) и закрывается.
   — Не вошли на Carfax Online: программа покажет «войдите на carfaxonline.com»; войдёте — отчёт откроется сам.
   — Отчёт, открытый вручную, тоже уходит в программу (вкладка остаётся открытой).
   — «Carfax сам» (машинам с KBB, carfax_auto.js): то же, но если отчёт не вышел — вкладка закрывается (не копятся).
   Расширение ничего не нажимает на сайте: только открывает адрес отчёта и читает текст страницы. */
(function () {
  var started = Date.now();
  var LIMIT_MS = 3 * 60 * 1000;
  var job = '';                      // VIN заказа, если вкладку открыло расширение по кнопке «📄 Carfax»
  var auto = false;                  // заказ «Carfax сам» — не по кнопке
  var sentKey = '', saidLogin = false, lastLen = -1, done = false;
  var send = function (msg, cb) { try { chrome.runtime.sendMessage(msg, function (r) { void chrome.runtime.lastError; if (cb) { cb(r); } }); } catch (e) { /* расширение обновили */ } };
  var status = function (s, note) { if (job) { send({ type: 'carfax-status', vin: job, status: s, note: note || '', auto: auto }); } };
  var closeIfAuto = function () { if (auto) { setTimeout(function () { send({ type: 'carfax-close' }); }, 1500); } };
  var vinOf = function (text) {
    var all = text.match(/\b[A-HJ-NPR-Z0-9]{17}\b/g) || [];
    var counts = {};
    all.filter(function (v) { return /\d/.test(v) && /[A-Z]/.test(v); }).forEach(function (v) { counts[v] = (counts[v] || 0) + 1; });
    return Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; })[0] || '';
  };
  var loginPage = function () {
    return /^\/login/i.test(location.pathname) || !!document.querySelector('input[type="password"]');
  };
  var tick = function () {
    if (done) { return; }
    var text = (document.body && document.body.innerText) || '';
    if (loginPage()) {
      if (!saidLogin) { saidLogin = true; status('login', 'войдите на carfaxonline.com в Chrome на Mac — отчёт откроется сам'); }
      return;
    }
    /* Вошли после страницы входа — сайт мог открыть главную: идём к отчёту заказанной машины (не больше двух раз). */
    if (job && !/\/vhrs?\//i.test(location.pathname)) {
      var tries = 0;
      try { tries = Number(sessionStorage.getItem('la-cfo-nav-' + job) || 0); sessionStorage.setItem('la-cfo-nav-' + job, String(tries + 1)); } catch (e) { tries = 9; }
      if (tries < 2) { location.href = '/vhr/' + job; return; }
    }
    var vin = job && text.toUpperCase().indexOf(job) >= 0 ? job : vinOf(text);
    var report = text.length >= 1500 && /vehicle history|carfax/i.test(text) && vin && (!job || vin === job);
    if (report && text.length === lastLen) {            // отчёт дорисовался: две проверки подряд — тот же текст
      var key = vin + '|' + text.length;
      if (key === sentKey) { return; }
      sentKey = key;
      send({ type: 'carfax', vin: vin, url: location.href, text: text.slice(0, 300000) }, function (r) {
        if (!job) { return; }
        done = true;
        status(r && r.ok ? 'done' : 'error', r && r.ok ? '' : 'программа не прочитала отчёт');
        if (r && r.ok) { setTimeout(function () { send({ type: 'carfax-close' }); }, 1500); } else { closeIfAuto(); }
      });
      return;
    }
    lastLen = report ? text.length : -1;
    if (job && Date.now() - started > LIMIT_MS) {
      done = true;
      status('error', /no (records|results)|not (a )?valid vin|unable to/i.test(text) ? 'Carfax не нашёл отчёт по этому VIN'
        : auto ? 'отчёт не открылся за 3 минуты' : 'отчёт не открылся за 3 минуты — вкладка Carfax Online оставлена открытой');
      closeIfAuto();
    }
  };
  send({ type: 'cfo-job' }, function (r) {
    job = (r && r.vin) || '';
    auto = !!(r && r.auto);
    if (job) { status('opened'); }
    setInterval(tick, 2000);
    tick();
  });
})();
