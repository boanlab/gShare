import { chromium } from 'playwright';

const BASE = 'http://localhost:8000';
const PW = process.env.PW || 'Nexus2026!';
const E = process.env; // ids
const OUT = '/work/screenshots';

let N = 0;
const pad = (n) => String(n).padStart(2, '0');

async function shoot(page, role, slug, route) {
  await page.goto(BASE + route, { waitUntil: 'domcontentloaded' }).catch(() => {});
  await page.waitForTimeout(2800); // let data / SSE-driven content render
  N += 1;
  const file = `${OUT}/${pad(N)}-${role}-${slug}.png`;
  await page.screenshot({ path: file, fullPage: true }).catch((e) => console.log('  shot fail', slug, e.message));
  console.log(`  [${pad(N)}] ${role} ${slug}  (${route})`);
}

async function login(page, email) {
  await page.goto(BASE + '/login', { waitUntil: 'domcontentloaded' });
  // The console defaults to English, so the selectors below match the `en` bundle.
  await page.getByPlaceholder('Email').fill(email);
  await page.getByPlaceholder('Password').fill(PW);
  await page.getByRole('button', { name: /Sign in/i }).click();
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(1500);
}

const userRoutes = [
  ['dashboard', '/'],
  ['sessions', '/sessions'],
  ['session-new', '/sessions/new'],
  ['session-detail', `/sessions/${E.S_VIT}`],
  ['session-connect', `/sessions/${E.S_VIT}/connect`],
  ['queue', '/queue'],
  ['wallet', '/wallet'],
  ['wallet-request', '/wallet/request'],
  ['volumes', '/data'],
  ['volume-new', '/data/new'],
  ['volume-share', `/data/${E.SV}/share`],
  ['volume-quota', `/data/${E.SV}/quota`],
  ['volume-snapshots', `/data/${E.SV}/snapshots`],
  ['account', '/account'],
];

const superAdminRoutes = [
  ['admin-dashboard', '/admin'],
  ['admin-orgs', '/admin/orgs'],
  ['admin-org-new', '/admin/orgs/new'],
  ['admin-org-admins', `/admin/orgs/${E.ORG}/admins`],
  ['admin-users', '/admin/users'],
  ['admin-user-new', '/admin/users/new'],
  ['admin-user-edit', `/admin/users/${E.U_SEOYEON}/edit`],
  ['admin-groups', '/admin/groups'],
  ['admin-group-new', '/admin/groups/new'],
  ['admin-group-admins', `/admin/groups/${E.DEPT_V}/admins`],
  ['admin-resources', '/admin/resources'],
  ['admin-offering-new', '/admin/resources/offerings/new'],
  ['admin-offering-edit', `/admin/resources/offerings/${E.OFF}/edit`],
  ['admin-policy-new', '/admin/resources/policies/new'],
  ['admin-clusters', '/admin/clusters'],
  ['admin-cluster-new', '/admin/clusters/new'],
  ['admin-nodes', '/admin/nodes'],
  ['admin-node-devices', `/admin/nodes/${E.NODE_ID}/devices`],
  ['admin-allocations', '/admin/allocations'],
  ['admin-monitor', '/admin/monitor'],
  ['admin-audit', '/admin/audit'],
  ['admin-images', '/admin/images'],
  ['admin-image-import', '/admin/images/import'],
];

const orgAdminRoutes = [
  ['orgadmin-dashboard', '/admin'],
  ['orgadmin-users', '/admin/users'],
  ['orgadmin-groups', '/admin/groups'],
  ['orgadmin-allocations', '/admin/allocations'],
  ['orgadmin-monitor', '/admin/monitor'],
  ['orgadmin-audit', '/admin/audit'],
];

const groupAdminRoutes = [
  ['groupadmin-dashboard', '/admin'],
  ['groupadmin-users', '/admin/users'],
  ['groupadmin-groups', '/admin/groups'],
  ['groupadmin-monitor', '/admin/monitor'],
];

const run = async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'ko-KR' });
  const page = await ctx.newPage();

  // 0) unauthenticated login page
  await page.goto(BASE + '/login', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1500);
  N += 1;
  await page.screenshot({ path: `${OUT}/${pad(N)}-public-login.png`, fullPage: true });
  console.log(`  [${pad(N)}] public login`);

  console.log('== USER Seoyeon Kim ==');
  await login(page, 'seoyeon@nexusai.dev');
  for (const [slug, route] of userRoutes) await shoot(page, 'user-seoyeon', slug, route);

  console.log('== SUPER ADMIN ==');
  const ctx2 = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'ko-KR' });
  const p2 = await ctx2.newPage();
  await login(p2, 'admin@kloud.zone');
  for (const [slug, route] of superAdminRoutes) await shoot(p2, 'superadmin', slug, route);

  console.log('== ORG ADMIN Jieun Lee ==');
  const ctx3 = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'ko-KR' });
  const p3 = await ctx3.newPage();
  await login(p3, 'jieun@nexusai.dev');
  for (const [slug, route] of orgAdminRoutes) await shoot(p3, 'orgadmin-jieun', slug, route);

  console.log('== GROUP ADMIN Minjun Park ==');
  const ctx4 = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'ko-KR' });
  const p4 = await ctx4.newPage();
  await login(p4, 'minjun@nexusai.dev');
  for (const [slug, route] of groupAdminRoutes) await shoot(p4, 'groupadmin-minjun', slug, route);

  await browser.close();
  console.log(`DONE: ${N} screenshots`);
};
run().catch((e) => { console.error(e); process.exit(1); });
