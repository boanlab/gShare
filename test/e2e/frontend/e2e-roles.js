// Role-based UI end-to-end checks for super_admin, org_admin, group_admin, and member.
// Playwright (chromium) signs in to the console on :8000 as each role and verifies navigation
// visibility, the admin-mode toggle, and the route guards (403), capturing screenshots as it goes.
// Run it from the Playwright container with --network host and ORIGIN=http://localhost:8000.
//
// The console defaults to English, so the labels below match the `en` bundle.
const { chromium } = require('playwright');

const ORIGIN = process.env.ORIGIN || 'http://localhost:8000';
const OUT = process.env.OUT || '/work/test/e2e/frontend/out';
const PW = 'Passw0rd!';

// Admin sidebar labels, matching frontend/src/components/Layout.tsx and the `en` bundle.
const ADMIN_LABELS = ['Organizations','Groups','Users','Clusters','Nodes','Resources','Images','Credits','Session monitoring','Audit log'];

// Per role: whether the admin toggle appears, which labels must be shown and which hidden, and the
// route guards as path-to-blocked pairs.
const EXPECT = {
  super: { email:'super@e2e.test', adminBtn:true,
    show:['Organizations','Groups','Users','Clusters','Nodes','Resources','Images','Credits','Session monitoring','Audit log'],
    hide:[], guards:{ '/admin/orgs':false, '/admin/clusters':false, '/admin/users':false } },
  org: { email:'org@e2e.test', adminBtn:true,
    show:['Groups','Users','Credits','Session monitoring','Audit log'],
    hide:['Organizations','Clusters','Nodes','Resources','Images'],
    guards:{ '/admin/users':false, '/admin/groups':false, '/admin/orgs':true, '/admin/clusters':true } },
  grp: { email:'grp@e2e.test', adminBtn:true,
    show:['Groups','Credits','Session monitoring','Audit log'],
    hide:['Users','Organizations','Clusters','Nodes','Resources','Images'],
    guards:{ '/admin/groups':false, '/admin/users':true, '/admin/orgs':true } },
  mem: { email:'member@e2e.test', adminBtn:false,
    show:[], hide:ADMIN_LABELS, guards:{ '/admin':true, '/admin/groups':true } },
};

let fails = 0;
const log = (m) => console.log(m);
function check(cond, label) { log(`   ${cond?'✓':'✗'} ${label}`); if (!cond) fails++; }

async function login(page, email) {
  await page.goto(ORIGIN + '/login', { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Email').fill(email);
  await page.getByLabel('Password', { exact: true }).fill(PW);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  // The dashboard holds SSE streams open, so networkidle is never reached; wait for the URL instead.
  await page.waitForURL(u => !String(u).includes('/login'), { timeout: 15000 }).catch(()=>{});
  await page.waitForTimeout(1200);
}

async function is403(page, path) {
  await page.goto(ORIGIN + path, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(800);
  const url = page.url();
  let body = '';
  try { body = (await page.locator('body').innerText()).replace(/\s+/g,' '); } catch {}
  return /\/403/.test(url) || /403|do not have access|forbidden/i.test(body);
}

(async () => {
  const browser = await chromium.launch();
  for (const [role, e] of Object.entries(EXPECT)) {
    log(`\n=== [${role}] ${e.email} ===`);
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    page.setDefaultNavigationTimeout(20000);
    page.setDefaultTimeout(15000);
    try {
      await login(page, e.email);
      check(!/\/login/.test(page.url()), `signed in (URL: ${page.url().replace(ORIGIN,'')})`);

      // Is the admin-mode toggle present in the user layout?
      const adminBtnCount = await page.getByRole('button', { name: /admin mode/i }).count();
      check((adminBtnCount>0) === e.adminBtn, `admin-mode button ${e.adminBtn?'shown':'hidden'} (found=${adminBtnCount})`);

      // Admin navigation labels, collected from the links after entering /admin.
      let labels = [];
      if (e.adminBtn) {
        await page.goto(ORIGIN + '/admin', { waitUntil:'domcontentloaded' });
        await page.waitForTimeout(800);
        labels = (await page.getByRole('link').allInnerTexts()).map(s=>s.replace(/\s+/g,' ').trim());
      }
      const joined = labels.join(' | ');
      for (const s of e.show) check(labels.some(l=>l.includes(s)), `nav shows '${s}'`);
      for (const s of e.hide) check(!labels.some(l=>l.includes(s)), `nav hides '${s}'`);

      // Route guards (403).
      for (const [path, blocked] of Object.entries(e.guards)) {
        const got = await is403(page, path);
        check(got === blocked, `route ${path} ${blocked?'blocked (403)':'allowed'} (actual: ${got?'blocked':'allowed'})`);
      }

      await page.goto(ORIGIN + (e.adminBtn ? '/admin' : '/'), { waitUntil:'domcontentloaded' });
      await page.waitForTimeout(600);
      await page.screenshot({ path: `${OUT}/role-${role}.png`, fullPage: true });
      log(`   📸 ${OUT}/role-${role}.png`);
    } catch (err) {
      log(`   ✗ exception: ${err.message}`); fails++;
    } finally {
      await ctx.close();
    }
  }
  await browser.close();
  log(`\n== UI summary: ${fails===0?'ALL PASS':fails+' FAIL'} ==`);
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error('FATAL', e); process.exit(2); });
