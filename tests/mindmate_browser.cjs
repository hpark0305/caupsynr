// Local synthetic preview only. Never point this test at a production service.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
(async () => {
  const browser = await chromium.launch({headless: true, ...(process.env.BROWSER_CHANNEL ? {channel:process.env.BROWSER_CHANNEL} : {})});
  const context = await browser.newContext();
  await context.route('**/*', route => route.request().url().startsWith('http://127.0.0.1:5091/') ? route.continue() : route.abort());
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('http://127.0.0.1:5091/login');
  await page.locator('input[name=email]').fill('admin@example.test');
  await page.locator('input[name=password]').fill('A-long-test-password!');
  await Promise.all([page.waitForURL('**/portal'), page.locator('button[type=submit]').click()]);
  await page.goto('http://127.0.0.1:5091/portal/mindmate');
  assert(!await page.content().then(s => s.includes('PRIVATE_RESPONSE_MARKER')));
  const disclosure = page.locator('.mm-detail-button').first();
  await disclosure.click();
  await page.getByText('PRIVATE_RESPONSE_MARKER', {exact:true}).waitFor();
  assert.equal(await disclosure.getAttribute('aria-expanded'), 'true');
  assert(!await page.content().then(s => s.includes('LEGACY_IDENTITY_MARKER')));
  await disclosure.click();
  assert.equal(await page.getByText('PRIVATE_RESPONSE_MARKER', {exact:true}).count(), 0);
  await disclosure.click();
  await page.getByText('PRIVATE_RESPONSE_MARKER', {exact:true}).waitFor();
  await page.goto('http://127.0.0.1:5091/portal/mindmate?tab=live');
  await page.locator('.mm-detail-button').first().waitFor();
  await page.clock.install();
  await page.locator('.mm-detail-button').first().click();
  await page.getByText('PRIVATE_RESPONSE_MARKER', {exact:true}).waitFor();
  const detailId = await page.locator('.mm-detail-button').first().getAttribute('aria-controls');
  await page.evaluate(id => { window.savedDetail = document.getElementById(id); }, detailId);
  const poll = page.waitForResponse(r => r.url().endsWith('/live/data'));
  await page.clock.runFor(10001);
  await poll;
  assert.equal(await page.locator('.mm-detail-button').first().getAttribute('aria-expanded'), 'true');
  assert(await page.evaluate(id => window.savedDetail === document.getElementById(id), detailId));
  assert(await page.getByText('PRIVATE_RESPONSE_MARKER', {exact:true}).isVisible());
  await page.route('**/live/data', route => route.fulfill({status:503, contentType:'application/json', body:'{"ok":false}'}));
  const failure = page.waitForResponse(r => r.url().endsWith('/live/data'));
  await page.clock.runFor(10001);
  await failure;
  await page.getByText('갱신 실패', {exact:false}).waitFor();
  assert(await page.getByText('PRIVATE_RESPONSE_MARKER', {exact:true}).isVisible());
  await page.locator('.mm-detail-button').first().click();
  await page.route('**/detail/**', route => route.fulfill({status:503, contentType:'application/json', body:'{"ok":false}'}));
  await page.locator('.mm-detail-button').first().click();
  await page.getByText('상세를 불러오지 못했습니다.', {exact:false}).waitFor();
  assert.equal(await page.getByText('PRIVATE_RESPONSE_MARKER', {exact:true}).count(), 0);
  const themes = [];
  for (const theme of ['dark','light']) {
    await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme);
    themes.push(await page.locator('.mm-detail-button').first().evaluate(e => ({
      color:getComputedStyle(e).color, background:getComputedStyle(document.body).backgroundColor,
      width:e.getBoundingClientRect().width,
    })));
  }
  assert.notEqual(themes[0].color, themes[1].color);
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  assert.deepEqual(errors, []);
  const report = {passed:true, checks:['no-preload','audited-detail','no-legacy-identity','close-clears-content',
    'reopen-fetch','poll-keeps-dom-and-disclosure','poll-failure-keeps-open-response','detail-failure-hides-response',
    'light-dark-tokens','mobile-no-horizontal-overflow','no-js-errors'], themes, scope:'Local synthetic SQLite only'};
  if (process.env.EVIDENCE_OUTPUT) fs.writeFileSync(process.env.EVIDENCE_OUTPUT, JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
  await browser.close();
})().catch(error => { console.error(error); process.exit(1); });
