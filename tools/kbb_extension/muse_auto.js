/* Lot Analyzer: Muse сам (Facebook Marketplace). Раз в минуту (и сразу по кнопке «🤖 Muse сам» в окне) спрашиваем
   программу (/api/agent_next): есть задание — открываем muse.ai во вкладке в фоне; muse_page.js вставляет текст задания,
   отправляет его, ждёт таблицу (CSV) и отдаёт сюда → программе (/api/agent_report). Ответ загружен — вкладка закрывается;
   не вышло (нужен вход, нет таблицы, долго нет ответа) — вкладка остаётся, причина видна в окне программы.
   Одно задание за раз. Когда и о чём спрашивать (раз в день, только группы без свежих цен) — решает программа. */
var LA_MUSE_ALARM = 'la-muse-auto';
var LA_MUSE_KEY = 'muse-job';
var LA_MUSE_SITE = /^https:\/\/([a-z0-9-]+\.)*muse\.ai\//;
var laMuseBusy = false;
function laMuseGet() { return chrome.storage.local.get(LA_MUSE_KEY).then(function (o) { return o[LA_MUSE_KEY] || null; }); }
function laMuseSet(job) { var o = {}; o[LA_MUSE_KEY] = job; return chrome.storage.local.set(o); }
function laMusePost(path, body) {
  return fetch('http://127.0.0.1:8765' + path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LA': '1' }, body: JSON.stringify(body) })
    .then(function (r) { return r.json(); });
}
function laMuseReport(job, state, note, text) {
  return laMusePost('/api/agent_report', { src: 'fb', id: job.id, state: state, note: note || '', text: text || '' }).catch(function () { return {}; });
}
function laMuseTab(id) { return new Promise(function (res) { chrome.tabs.get(id, function (t) { void chrome.runtime.lastError; res(t || null); }); }); }
function laMuseShow(id) { chrome.tabs.update(id, { active: true }, function () { void chrome.runtime.lastError; }); }
/* Не вышло: в программу — причина; вкладку оставляем (show — показать её: нужен вход). */
function laMuseFail(job, note, show) {
  return laMuseReport(job, 'error', note).then(function () { if (show) { laMuseShow(job.tab); } return laMuseSet(null); });
}
function laMuseTick() {
  if (laMuseBusy) { return; }
  laMuseBusy = true;
  laMuseGet()
    .then(function (job) {
      if (job) {                                                   // задание идёт: вкладку закрыли? нет ответа?
        return laMuseTab(job.tab).then(function (tab) {
          var age = Date.now() - job.at;
          if (!tab) { return laMuseFail(job, 'вкладку muse.ai закрыли до ответа — нажмите «🤖 Muse сам» ещё раз', false); }
          if (job.state === 'open' && age > 3 * 60000) {           // страница Muse так и не ответила — похоже, вход
            return laMuseFail(job, 'muse.ai не открылся для задания — похоже, нужно войти: вкладка показана, войдите и нажмите «🤖 Muse сам» ещё раз', true);
          }
          if (age > 40 * 60000) { return laMuseFail(job, 'Muse не дал таблицу за 40 минут — вкладка muse.ai открыта, посмотрите ответ', false); }
          return null;
        });
      }
      return laMusePost('/api/agent_next', { src: 'fb' }).then(function (d) {
        if (!d || !d.id || !d.text) { return null; }
        var url = LA_MUSE_SITE.test(d.url || '') ? d.url : 'https://muse.ai/';
        return new Promise(function (res) {
          chrome.tabs.create({ url: url, active: false }, function (tab) {
            void chrome.runtime.lastError;
            if (!tab) { res(laMuseReport(d, 'error', 'не открылась вкладка muse.ai')); return; }
            res(laMuseSet({ id: d.id, text: d.text, tab: tab.id, at: Date.now(), state: 'open' }));
          });
        });
      });
    })
    .catch(function () { /* программа не запущена — спросим через минуту */ })
    .then(function () { laMuseBusy = false; });
}
chrome.runtime.onMessage.addListener(function (msg, sender, reply) {
  if (!msg) { return; }
  if (msg.type === 'agent-tick') { laMuseTick(); return; }
  if (!sender.tab || !LA_MUSE_SITE.test(sender.url || sender.tab.url || '')) { return; }
  if (msg.type === 'muse-hello') {                                 // страница muse.ai: это вкладка задания? что делать?
    laMuseGet().then(function (job) {
      if (!job || job.tab !== sender.tab.id) { reply(null); return; }
      reply({ id: job.id, text: job.text, state: job.state, at: job.at });
      if (job.state === 'open') { job.state = 'typing'; laMuseSet(job); }
    });
    return true;
  }
  if (msg.type === 'muse-sent') {                                  // задание отправлено — ждём таблицу
    laMuseGet().then(function (job) {
      if (!job || job.tab !== sender.tab.id || job.id !== msg.id) { return; }
      job.state = 'sent';
      laMuseSet(job);
      laMuseReport(job, 'sent', '');
    });
    return;
  }
  if (msg.type === 'muse-show') { laMuseShow(sender.tab.id); return; }
  if (msg.type === 'muse-done' || msg.type === 'muse-fail') {
    laMuseGet().then(function (job) {
      if (!job || job.tab !== sender.tab.id || job.id !== msg.id) { reply({}); return; }
      if (msg.type === 'muse-fail') { laMuseFail(job, String(msg.note || 'не вышло'), !!msg.show).then(function () { reply({}); }); return; }
      laMuseReport(job, 'done', '', String(msg.text || '').slice(0, 400000)).then(function (a) {
        laMuseSet(null);
        if (a && a.close) { chrome.tabs.remove(sender.tab.id, function () { void chrome.runtime.lastError; }); }
        reply(a || {});
      });
    });
    return true;
  }
});
chrome.alarms.get(LA_MUSE_ALARM, function (a) { if (!a) { chrome.alarms.create(LA_MUSE_ALARM, { delayInMinutes: 1, periodInMinutes: 1 }); } });
chrome.alarms.onAlarm.addListener(function (a) { if (a.name === LA_MUSE_ALARM) { laMuseTick(); } });
