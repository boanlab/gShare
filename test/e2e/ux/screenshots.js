#!/usr/bin/env node
// Console screenshots for the manuals, one per screen and role, against the seeded demo scenario.
//
//   docker compose up -d
//   node test/e2e/ux/seed.js
//   docker compose exec -T postgres psql -U gshare -d gshare < test/e2e/ux/fixture.sql
//   ORIGIN=http://localhost:8000 node test/e2e/ux/screenshots.js
//
// Writes docs/screenshots/NN-role-screen.png at 1440x900. Re-run after a change to the console
// so the manuals show what the reader will see.

import { chromium } from 'playwright';
import { mkdirSync, readdirSync, unlinkSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { personaById } from './personas.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const ORIGIN = process.env.ORIGIN || 'http://localhost:8000';
const API = process.env.API_ORIGIN || ORIGIN;
const OUT = process.env.SHOTS_OUT || join(HERE, '../../../docs/screenshots');
const VIEWPORT = { width: 1440, height: 900 };
const SETTLE = Number(process.env.SHOTS_SETTLE || 1500);

/** Screens to capture, in the order the manuals walk them. `{param}` is filled from live data. */
const SHOTS = [
  ['public', null, '/login', 'login'],

  ['user', 'researcher', '/', 'dashboard'],
  ['user', 'researcher', '/sessions', 'sessions'],
  ['user', 'researcher', '/sessions/new', 'session-new'],
  ['user', 'researcher', '/sessions/{sessionId}', 'session-detail'],
  ['user', 'researcher', '/sessions/{sessionId}/connect', 'session-connect'],
  ['user', 'researcher', '/queue', 'queue'],
  ['user', 'researcher', '/wallet', 'wallet'],
  ['user', 'researcher', '/wallet/request', 'wallet-request'],
  ['user', 'researcher', '/data', 'volumes'],
  ['user', 'researcher', '/data/new', 'volume-new'],
  ['user', 'researcher', '/data/{volumeId}/share', 'volume-share'],
  ['user', 'researcher', '/data/{volumeId}/quota', 'volume-quota'],
  ['user', 'researcher', '/data/{volumeId}/snapshots', 'volume-snapshots'],
  ['user', 'researcher', '/account', 'account'],

  ['superadmin', 'platform-admin', '/admin', 'admin-dashboard'],
  ['superadmin', 'platform-admin', '/admin/orgs', 'admin-orgs'],
  ['superadmin', 'platform-admin', '/admin/orgs/new', 'admin-org-new'],
  ['superadmin', 'platform-admin', '/admin/orgs/{orgId}/admins', 'admin-org-admins'],
  ['superadmin', 'platform-admin', '/admin/users', 'admin-users'],
  ['superadmin', 'platform-admin', '/admin/users/new', 'admin-user-new'],
  ['superadmin', 'platform-admin', '/admin/users/{userId}/edit', 'admin-user-edit'],
  ['superadmin', 'platform-admin', '/admin/groups', 'admin-groups'],
  ['superadmin', 'platform-admin', '/admin/groups/new', 'admin-group-new'],
  ['superadmin', 'platform-admin', '/admin/groups/{groupId}/admins', 'admin-group-admins'],
  ['superadmin', 'platform-admin', '/admin/resources', 'admin-resources'],
  ['superadmin', 'platform-admin', '/admin/resources/offerings/new', 'admin-offering-new'],
  ['superadmin', 'platform-admin', '/admin/resources/offerings/{offeringId}/edit', 'admin-offering-edit'],
  ['superadmin', 'platform-admin', '/admin/resources/policies/new', 'admin-policy-new'],
  ['superadmin', 'platform-admin', '/admin/clusters', 'admin-clusters'],
  ['superadmin', 'platform-admin', '/admin/clusters/new', 'admin-cluster-new'],
  ['superadmin', 'platform-admin', '/admin/nodes', 'admin-nodes'],
  ['superadmin', 'platform-admin', '/admin/nodes/{nodeId}/devices', 'admin-node-devices'],
  ['superadmin', 'platform-admin', '/admin/allocations', 'admin-allocations'],
  ['superadmin', 'platform-admin', '/admin/monitor', 'admin-monitor'],
  ['superadmin', 'platform-admin', '/admin/audit', 'admin-audit'],
  ['superadmin', 'platform-admin', '/admin/images', 'admin-images'],
  ['superadmin', 'platform-admin', '/admin/images/import', 'admin-image-import'],

  ['orgadmin', 'org-admin', '/admin', 'dashboard'],
  ['orgadmin', 'org-admin', '/admin/users', 'users'],
  ['orgadmin', 'org-admin', '/admin/groups', 'groups'],
  ['orgadmin', 'org-admin', '/admin/allocations', 'allocations'],
  ['orgadmin', 'org-admin', '/admin/monitor', 'monitor'],
  ['orgadmin', 'org-admin', '/admin/audit', 'audit'],

  ['groupadmin', 'team-lead', '/admin', 'dashboard'],
  ['groupadmin', 'team-lead', '/admin/users', 'users'],
  ['groupadmin', 'team-lead', '/admin/groups', 'groups'],
  ['groupadmin', 'team-lead', '/admin/monitor', 'monitor'],
];

async function token(persona) {
  for (const pw of [persona.password + 'Ux1', persona.password]) {
    const r = await fetch(`${API}/api/v1/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: persona.email, password: pw }),
    });
    if (r.ok) return (await r.json()).access_token;
  }
  throw new Error(`cannot sign in as ${persona.email}`);
}

async function ids(tok) {
  const get = async (p) => {
    const r = await fetch(`${API}/api/v1${p}`, { headers: { Authorization: `Bearer ${tok}` } });
    if (!r.ok) return [];
    const j = await r.json();
    return Array.isArray(j) ? j : (j.data ?? []);
  };
  const first = (a) => (a.length ? a[0].id : '');
  const [sessions, volumes, users, groups, orgs, offerings, nodes] = await Promise.all([
    get('/sessions'), get('/storage/volumes'), get('/users?size=100'), get('/projects?size=100'),
    get('/organizations?size=100'), get('/offerings?size=100'), get('/nodes'),
  ]);
  return {
    // A running session shows the connect screen with live tokens.
    sessionId: (sessions.find((s) => s.status === 'running') ?? sessions[0])?.id ?? '',
    volumeId: first(volumes),
    userId: first(users),
    groupId: first(groups),
    orgId: first(orgs),
    offeringId: first(offerings),
    nodeId: first(nodes),
  };
}

// Resolved once with the platform administrator's token: the other personas cannot list the
// clusters, nodes and organizations the parameterised routes need.
const ID_CACHE = await ids(await token(personaById('platform-admin')));

mkdirSync(OUT, { recursive: true });
for (const f of readdirSync(OUT)) if (f.endsWith('.png')) unlinkSync(join(OUT, f));

const browser = await chromium.launch();
let n = 0;
let context = null;
let currentRole = null;

for (const [role, personaId, path, name] of SHOTS) {
  if (role !== currentRole) {
    await context?.close();
    const persona = personaId ? personaById(personaId) : null;
    context = await browser.newContext({ viewport: VIEWPORT, locale: 'en-US', deviceScaleFactor: 2 });
    if (persona) {
      const tok = await token(persona);
      await context.addInitScript((t) => {
        const claims = JSON.parse(atob(t.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
        localStorage.setItem('gshare.lang', 'en');
        localStorage.setItem('gshare-auth', JSON.stringify({
          state: { accessToken: t, isAuthed: true, claims, memberships: [], orgAdminOrgs: [] },
          version: 0,
        }));
      }, tok);
    }
    currentRole = role;
  }

  const url = path.replace(/\{(\w+)\}/g, (_, k) => ID_CACHE[k] ?? '');
  const page = await context.newPage();
  await page.goto(ORIGIN + url, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(SETTLE);

  n += 1;
  const file = join(OUT, `${String(n).padStart(2, '0')}-${role}-${name}.png`);
  await page.screenshot({ path: file, fullPage: false });
  process.stdout.write(`${String(n).padStart(2, '0')} ${role.padEnd(11)} ${url}\n`);
  await page.close();
}

await context?.close();
await browser.close();
console.log(`\n${n} screenshots in ${OUT}`);
