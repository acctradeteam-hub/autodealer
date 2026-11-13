/* Команды с телефона: окно программы на телефоне (через Tailscale) кладёт кнопку в программу на Mac, а расширение
   раз в 30 секунд забирает очередь и выполняет здесь, в Chrome на Mac, — как будто нажали в окне на Mac:
   собрать аукцион, «KBB на неделю», проверка фото, Carfax, запись торгов, KBB-автопилот для выбранных машин.
   Заодно отдаёт программе своё состояние (автосбор, запись торгов, KBB на неделю) — телефон видит то же, что Mac.
   Ставок и покупок расширение не делает. */
var RC_API = 'http://127.0.0.1:8765/api/remote_poll';
var RC_OPEN = /^https:\/\/www\.(kbb|carmaxauctions)\.com\//;

function rcGet(key) { return new Promise(function (ok) { chrome.storage.local.get(key, function (got) { ok((got || {})[key] || {}); }); }); }

/* То же, что окно на Mac получает на «auto-status» / «join-status». */
async function rcStatus() {
  var join = typeof joinGet === 'function' ? await new Promise(function (ok) { joinGet(ok); }) : {};
  if (typeof joinNext === 'function') { var n = joinNext(new Date()); join.next = n ? n.getTime() : 0; }
  return {
    version: chrome.runtime.getManifest().version,
    auto: { state: await rcGet('auto-state'), busy: typeof autoBusy !== 'undefined' && autoBusy, places: typeof AUTO_PLACES !== 'undefined' ? AUTO_PLACES : {} },
    join: { state: join },
    kbbweek: await rcGet('kbbweek-state')
  };
}

function rcRun(cmd) {
  if (cmd.type === 'auto-run' && typeof autoRun === 'function') { autoRun(true, cmd.which === 'mon' || cmd.which === 'tue' ? cmd.which : ''); }
  else if (cmd.type === 'kbbweek-run' && typeof kwTick === 'function') { kwTick(true); }
  else if (cmd.type === 'photos-start' && typeof photosStart === 'function') { photosStart(null, 'с телефона'); }
  else if (cmd.type === 'carfax-order' && typeof laCarfaxOrder === 'function') { laCarfaxOrder(cmd.vin); }
  else if (cmd.type === 'join-run' && typeof joinRun === 'function') { joinRun(true); }
  else if (cmd.type === 'join-enable' && typeof joinGet === 'function') { joinGet(function (state) { state.enabled = !!cmd.on; joinPut(state); }); }
  else if (cmd.type === 'open-bg' && RC_OPEN.test(cmd.url || '')) {
    /* KBB-автопилот для выбранных машин, «Владельцы» (AutoCheck): вкладка в фоне, как из окна на Mac. */
    chrome.tabs.create({ url: cmd.url, active: false }, function (tab) {
      void chrome.runtime.lastError;
      if (!tab) { return; }
      var item = {};
      if (cmd.cars) { item['cars-' + tab.id] = cmd.cars; }
      if (/#la-owners/.test(cmd.url)) { item['owners-' + tab.id] = 1; }
      if (/#la-photos/.test(cmd.url)) { item['photos-' + tab.id] = 1; }
      chrome.storage.session.set(item);
    });
  }
}

var rcBusy = false;
async function rcPoll() {
  if (rcBusy) { return; }
  rcBusy = true;
  try {
    var status = await rcStatus();
    var res = await fetch(RC_API, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LA': '1' }, body: JSON.stringify({ status: status }) })
      .then(function (r) { return r.json(); }).catch(function () { return null; });
    ((res && res.commands) || []).forEach(rcRun);
  } finally {
    rcBusy = false;
  }
}

chrome.alarms.get('la-remote', function (a) { if (!a) { chrome.alarms.create('la-remote', { delayInMinutes: 0.5, periodInMinutes: 0.5 }); } });
chrome.alarms.onAlarm.addListener(function (a) { if (a.name === 'la-remote') { rcPoll(); } });
chrome.runtime.onStartup.addListener(function () { setTimeout(rcPoll, 5000); });
