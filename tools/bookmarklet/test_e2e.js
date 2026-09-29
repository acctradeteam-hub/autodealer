/* Проверка закладки в настоящем Chromium: открывает тестовые страницы под адресами аукционов,
   дописывает заметку, нажимает закладку и сохраняет скачанные файлы.
   Запуск: node tools/bookmarklet/test_e2e.js [папка]; затем python3 -m lot_analyzer [папка].
   Нужен Playwright (npm i -g playwright). */
const path = require('path');
const fs = require('fs');
const { chromium } = require(path.join(require('child_process').execSync('npm root -g').toString().trim(), 'playwright'));
const repo = path.resolve(__dirname, '..', '..');
const out = process.argv[2] || require('os').tmpdir();
const url = fs.readFileSync(repo + '/tools/bookmarklet/bookmarklet.txt', 'utf8').trim();
const code = decodeURIComponent(url.slice('javascript:'.length));
const cases = [
  ['https://www.carmaxauctions.com/member/watchlist', 'carmax_watchlist.html', true],
  ['https://app.acvauctions.com/marketplace/auction/16496768', 'acv_kia_sportage_lot.html', false],
  ['https://search.manheim.com/results#/details/JTNC4MBE3M3123227/Simulcast', 'manheim_corolla_lot.html', false],
  ['https://buy.adesa.com/openauction/vehicle/123', 'adesa_civic_lot.html', false],
];
(async () => {
  const browser = await chromium.launch();
  for (const [u, fixture, editNote] of cases) {
    const ctx = await browser.newContext({ acceptDownloads: true });
    const page = await ctx.newPage();
    await page.route('**/*', r => r.request().isNavigationRequest()
      ? r.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: fs.readFileSync(repo + '/tests/fixtures/' + fixture) })
      : r.abort());
    await page.goto(u);
    if (editNote) {
      // как будто покупатель дописал заметку прямо на странице
      await page.evaluate(() => { const t = document.querySelectorAll('main textarea')[0]; t.value = t.value + ' KBB 9,999$ MP 5000 FB 9,500'; });
    }
    const [dl] = await Promise.all([page.waitForEvent('download'), page.evaluate(code)]);
    const file = path.join(out, dl.suggestedFilename());
    await dl.saveAs(file);
    const toast = await page.locator('text=Сохранено:').innerText();
    console.log(fixture, '->', dl.suggestedFilename(), '|', toast);
    await ctx.close();
  }
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
