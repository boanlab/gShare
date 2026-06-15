// End-to-end check of rejecting a request with a mandatory reason: sign in as super_admin, open the
// credit requests list, reject one (answering the window.prompt), and confirm it leaves the list.
// Requires seed-roles.sql plus a pending request for 777. Run with ORIGIN=http://localhost:8000.
//
// The console defaults to English, so the selectors below match the `en` bundle.
const { chromium } = require('playwright');
const ORIGIN = process.env.ORIGIN || 'http://localhost:8000';
const OUT = process.env.OUT || '/work/test/e2e/frontend/out';
const PW = 'Passw0rd!';
const AMOUNT = process.env.AMOUNT || '777';
let fails = 0;
const check = (c, l) => { console.log(`   ${c ? '✓' : '✗'} ${l}`); if (!c) fails++; };

(async () => {
  const browser = await chromium.launch();
  const page = await (await browser.newContext()).newPage();
  page.setDefaultTimeout(15000);
  // Answer the window.prompt asking for a rejection reason.
  page.on('dialog', async (d) => { await d.accept('e2e rejection reason'); });

  await page.goto(ORIGIN + '/login', { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Email').fill('super@e2e.test');
  await page.getByLabel('Password', { exact: true }).fill(PW);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.waitForURL(u => !String(u).includes('/login'), { timeout: 15000 }).catch(() => {});
  console.log('=== [super] rejecting a request, with a reason ===');

  await page.goto(ORIGIN + '/admin/allocations', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1000);
  // Incoming requests are listed on the credit allocation screen; there is no tab to click.
  await page.waitForTimeout(1000);

  const bodyBefore = (await page.locator('body').innerText()).replace(/\s+/g, ' ');
  check(bodyBefore.includes(AMOUNT), `before rejection: the request for ${AMOUNT} is listed`);

  await page.getByRole('button', { name: 'Reject', exact: true }).first().click();
  await page.waitForTimeout(2000); // mutate + invalidate + refetch

  const bodyAfter = (await page.locator('body').innerText()).replace(/\s+/g, ' ');
  check(!bodyAfter.includes(AMOUNT) || /No pending requests/i.test(bodyAfter),
    `after rejection: the request for ${AMOUNT} is gone from the pending list`);

  await page.screenshot({ path: OUT + '/topup-reject.png', fullPage: true });
  console.log('   📸 ' + OUT + '/topup-reject.png');
  console.log(fails ? `\n== topup-reject UI: ${fails} FAIL ==` : '\n== topup-reject UI: ALL PASS ==');
  await browser.close();
  process.exit(fails ? 1 : 0);
})();
