// /metrics/billing-report through the console: sign in as super_admin, run the settlement report
// and verify it renders. The report is a section of the credit allocation screen.
//
// Run as e2e-roles.js: the Playwright container, ORIGIN=http://localhost:8000, seed-roles.sql
// applied. Selectors match the `en` bundle.
const { chromium } = require('playwright');
const ORIGIN = process.env.ORIGIN || 'http://localhost:8000';
const OUT = process.env.OUT || '/work/test/e2e/frontend/out';
const PW = 'Passw0rd!';
let fails = 0;
const check = (c, l) => { console.log(`   ${c ? '✓' : '✗'} ${l}`); if (!c) fails++; };

(async () => {
  const browser = await chromium.launch();
  const page = await (await browser.newContext()).newPage();
  page.setDefaultTimeout(15000);

  await page.goto(ORIGIN + '/login', { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Email').fill('super@e2e.test');
  await page.getByLabel('Password', { exact: true }).fill(PW);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.waitForURL(u => !String(u).includes('/login'), { timeout: 15000 }).catch(() => {});
  console.log('=== [super] settlement report UI ===');

  await page.goto(ORIGIN + '/admin/allocations', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1200);
  check(!/\/403/.test(page.url()), 'the settlement report screen is reachable');

  await page.waitForTimeout(600);
  const inputs = page.locator('input[type="datetime-local"]');
  await inputs.nth(0).fill('2026-05-01T00:00');
  await inputs.nth(1).fill('2026-07-01T00:00');
  await page.getByRole('button', { name: 'Run' }).click();
  await page.waitForTimeout(1800);

  const body = (await page.locator('body').innerText()).replace(/\s+/g, ' ');
  // On a 404 or an error, humanizeError renders a danger message. On success there are totals
  // (consumed and topped up) or the empty state.
  check(!/not found|server ran into an error|went wrong/i.test(body), 'report loaded without an error or 404');
  check(/Consumed|Top-up|report is empty/i.test(body), 'report panel rendered: totals or the empty state');

  await page.screenshot({ path: OUT + '/billing-report.png', fullPage: true });
  console.log('   📸 ' + OUT + '/billing-report.png');
  console.log(fails ? `\n== billing-report UI: ${fails} FAIL ==` : '\n== billing-report UI: ALL PASS ==');
  await browser.close();
  process.exit(fails ? 1 : 0);
})();
