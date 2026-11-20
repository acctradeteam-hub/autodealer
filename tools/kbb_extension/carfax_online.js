/* Lot Analyzer: Carfax Online (carfaxonline.com — сайт Carfax for Dealers; вход — auth.carfax.com). Отчёт по VIN — /vhr/<VIN>.
   — Вкладку отчёта открывает расширение (кнопка «📄 Carfax» в окне, с телефона или «Carfax сам») — всегда одну за раз, в фоне.
     Вкладка ждёт, пока отчёт дорисуется, отдаёт его текст программе и закрывается (не вышло — тоже закрывается).
   — Сайт просит войти или подтвердить вход по телефону (страницы auth.carfax.com, «Account protection»): заказы Carfax
     на паузе — новых вкладок нет, вкладка входа остаётся одна, программа пишет «Carfax: нужен вход» и работает без Carfax.
     Войдёте в этой вкладке — отчёт откроется сам, дальше заказы пойдут сами.
   — Вы вошли на Carfax Online в любой вкладке (видна ваша страница или отчёт) — программа снимает паузу.
   — Отчёт, открытый вручную, тоже уходит в программу (вкладка остаётся открытой).
   Расширение ничего не нажимает на сайте и ничего не вводит: только открывает адрес отчёта и читает текст страницы. */
(function () {
  var started = Date.now();
  var LIMIT_MS = 3 * 60 * 1000;
  var onAuth = !/(^|\.)(carfaxonline|carfaxfordealers)\.com$/i.test(location.hostname);   // auth.carfax.com — вход и подтверждение
  var job = '';                      // VIN заказа, если вкладку открыло расширение
  var auto = false;                  // заказ «Carfax сам» — не по кнопке
  var sentKey = '', saidLogin = false, saidOpened = false, saidIn = false, inTicks = 0, lastLen = -1, done = false;
  var send = function (msg, cb) { try { chrome.runtime.sendMessage(msg, function (r) { void chrome.runtime.lastError; if (cb) { cb(r); } }); } catch (e) { /* расширение обновили */ } };
  var status = function (s, note) { if (job) { send({ type: 'carfax-status', vin: job, status: s, note: note || '', auto: auto }); } };
  var vinOf = function (text) {
    var all = text.match(/\b[A-HJ-NPR-Z0-9]{17}\b/g) || [];
    var counts = {};
    all.filter(function (v) { return /\d/.test(v) && /[A-Z]/.test(v); }).forEach(function (v) { counts[v] = (counts[v] || 0) + 1; });
    return Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; })[0] || '';
  };
  /* Вход: страницы auth.carfax.com, /login, «Account protection» (подтверждение телефона), поле пароля; короткая страница
     с «Sign in» и без «Log out» — вы не вошли (сайт показал вместо отчёта приглашение войти). */
  var loginPage = function (text) {
    text = text || '';
    return onAuth || /^\/(login|logout|accountprotection|createaccount)(\/|$)/i.test(location.pathname) || !!document.querySelector('input[type="password"]') ||
      (text.length < 3000 && /\b(log|sign) ?in\b/i.test(text) && !/\b(log|sign) ?out\b/i.test(text));
  };
  var verifyPage = function (text) {
    return /mfa|phone|sms|otp|verif|challenge|protection/i.test(location.pathname) ||
      /verification code|verify (your )?(identity|phone|account)|phone number|text message|one[- ]time (pass)?code|we('ve| have)? sent (a|you a) code|enter the code/i.test(text);
  };
  /* Вы на Carfax Online и вошли: ваша страница (отчёт, кабинет, заказы) или есть «Log out». */
  var signedIn = function (text) {
    return !loginPage(text) && (/^\/(vhrs?|dashboard|myorders|account)(\/|$)/i.test(location.pathname) || /\b(log|sign) ?out\b/i.test(text));
  };
  var tick = function () {
    if (done) { return; }
    var text = (document.body && document.body.innerText) || '';
    if (loginPage(text)) {
      if (!saidLogin) {
        saidLogin = true;
        status('login', verifyPage(text) ? 'Carfax просит подтвердить вход по телефону (код) — подтвердите во вкладке Carfax'
          : 'Carfax просит войти — войдите на Carfax Online в Chrome на Mac');
      }
      return;
    }
    if (/^\/auth0\//i.test(location.pathname)) { return; }     // вход завершается (auth0/callback) — не мешаем, сайт уйдёт сам
    if (!saidOpened) { saidOpened = true; status('opened'); }
    if (!saidIn && signedIn(text) && ++inTicks >= 2) { saidIn = true; send({ type: 'carfax-signed-in' }); }
    /* Вошли после страницы входа — сайт мог открыть главную: идём к отчёту заказанной машины (не больше двух раз). */
    if (job && !/\/vhrs?\//i.test(location.pathname)) {
      if (Date.now() - started < 4000) { return; }              // страница ещё устраивается после входа
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
        status(r && r.ok ? 'done' : 'error', r && r.ok ? '' : 'программа не прочитала отчёт');   // вкладку закрывает расширение
      });
      return;
    }
    lastLen = report ? text.length : -1;
    if (job && Date.now() - started > LIMIT_MS) {
      done = true;
      status('error', /no (records|results)|not (a )?valid vin|unable to/i.test(text) ? 'Carfax не нашёл отчёт по этому VIN' : 'отчёт не открылся за 3 минуты');
    }
  };
  send({ type: 'cfo-job' }, function (r) {
    job = (r && r.vin) || '';
    auto = !!(r && r.auto);
    setInterval(tick, 2000);
    tick();
  });
})();
