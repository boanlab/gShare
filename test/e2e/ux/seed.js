#!/usr/bin/env node
// Content for the UX audit to walk: volumes, snapshots, credit and top-up requests, webhooks and
// an organization administrator. Idempotent.
//
// Sessions come from fixture.sql, which covers every lifecycle state; creating them here would
// need a reachable Kubernetes API server.
//
//   API_ORIGIN=http://localhost:8080 node test/e2e/ux/seed.js

const API = (process.env.API_ORIGIN || 'http://localhost:8080') + '/api/v1';
const ADMIN = { email: process.env.UX_ADMIN_EMAIL || 'admin@example.com', password: process.env.UX_ADMIN_PASSWORD || 'GshareUx!2026' };

const log = (...a) => console.log(' ', ...a);

async function login(email, password) {
  for (const pw of [password + 'Ux1', password]) {
    const r = await fetch(`${API}/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password: pw }) });
    if (!r.ok) continue;
    let { access_token } = await r.json();
    const claims = JSON.parse(Buffer.from(access_token.split('.')[1], 'base64url').toString());
    if (claims.must_change_password) {
      const c = await fetch(`${API}/auth/change-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${access_token}` },
        body: JSON.stringify({ new_password: password + 'Ux1' }),
      });
      if (c.ok) access_token = (await c.json()).access_token;
    }
    return access_token;
  }
  throw new Error(`login failed for ${email}`);
}

const api = (token) => async (method, path, body) => {
  const r = await fetch(API + path, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`, 'Idempotency-Key': `ux-${method}-${path}-${JSON.stringify(body || {})}`.slice(0, 120) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  let json; try { json = JSON.parse(text); } catch { json = text; }
  return { ok: r.ok, status: r.status, json };
};

const admin = api(await login(ADMIN.email, ADMIN.password));

// Collections are not uniform: a bare array from some endpoints, {data, pagination} from others.
const list = async (path) => {
  const { json } = await admin('GET', path);
  return Array.isArray(json) ? json : (json?.data ?? json?.items ?? []);
};

// ── reference data ──
const me = (await admin('GET', '/auth/me')).json;
const groups = await list('/projects');
const orgs = await list('/organizations');
const offerings = (await list('/offerings')).filter((o) => o.status !== 'inactive');
const images = await list('/images');
const users = await list('/users');
const group = groups[0];
const org = orgs[0];
log(`groups=${groups.length} orgs=${orgs.length} offerings=${offerings.length} images=${images.length} users=${users.length}`);

// ── volumes, across every access mode so the list has variety ──
console.log('volumes');
const volumeSpecs = [
  { name: 'imagenet-2012', type: 'dataset', access_mode: 'ROX', quota_gb: 512 },
  { name: 'checkpoints', type: 'group', access_mode: 'RWX', quota_gb: 200 },
  { name: 'scratch', type: 'scratch', access_mode: 'RWO', quota_gb: 50 },
  { name: 'coco-val', type: 'dataset', access_mode: 'ROX', quota_gb: 40 },
];
const existingVolumes = await list('/storage/volumes');
for (const v of volumeSpecs) {
  if (existingVolumes.some((e) => e.name === v.name)) { log(`= ${v.name}`); continue; }
  const r = await admin('POST', '/storage/volumes', { scope: group ? 'group' : 'user', scope_id: group ? group.id : me.id, ...v });
  log(`${r.ok ? '+' : '!'} ${v.name} -> ${r.status}${r.ok ? '' : ' ' + JSON.stringify(r.json).slice(0, 140)}`);
}

// ── snapshots and a quota request on the first volume ──
const vols = await list('/storage/volumes');
if (vols[0]) {
  console.log('snapshots + quota requests');
  for (const label of ['before-augmentation', 'weekly-2026-08-10']) {
    const r = await admin('POST', `/storage/volumes/${vols[0].id}/snapshots`, { name: label });
    log(`${r.ok ? '+' : '!'} snapshot ${label} -> ${r.status}`);
  }
  const r = await admin('POST', `/storage/volumes/${vols[0].id}/quota-requests`, { requested_gb: (vols[0].quota_gb || 100) + 256, note: 'Adding the 2017 split doubles the dataset.' });
  log(`${r.ok ? '+' : '!'} quota request -> ${r.status}`);
}

// ── credit requests from members, so the approvals queue is not empty ──
console.log('credit and top-up requests');
const requesters = [
  { email: 'haneul@nexusai.dev', amount: 500, note: 'Fine-tuning run through the weekend.' },
  { email: 'dohyun@nexusai.dev', amount: 1200, note: 'Ablation sweep, 8 configurations.' },
  { email: 'seoyeon@nexusai.dev', amount: 300, note: 'Ran out mid-epoch.' },
];
for (const r of requesters) {
  try {
    const tok = await login(r.email, process.env.UX_USER_PASSWORD || 'Nexus2026!');
    const res = await api(tok)('POST', '/credits/allocation-requests', { amount: r.amount, level: 'user', group_id: group?.id ?? null, note: r.note });
    log(`${res.ok ? '+' : '!'} allocation request ${r.email} ${r.amount}C -> ${res.status}`);
  } catch (e) { log(`! ${r.email}: ${e.message}`); }
}
const topup = await admin('POST', '/credits/topup-requests', { amount: 5000, note: 'Q3 budget top-up for the organization wallet.', org_id: org?.id });
log(`${topup.ok ? '+' : '!'} top-up request -> ${topup.status} ${topup.ok ? '' : JSON.stringify(topup.json).slice(0, 140)}`);

// ── organization administrator ──
// Re-asserted rather than assumed: the probes exercise the removal path.
console.log('organization admin');
if (org) {
  const jieun = users.find((u) => u.email === 'jieun@nexusai.dev');
  if (jieun) {
    const current = await list(`/organizations/${org.id}/admins`);
    if (current.some((a) => a.user_id === jieun.id)) {
      log(`= ${jieun.email} is already an organization admin`);
    } else {
      const r = await admin('POST', `/organizations/${org.id}/admins`, { user_id: jieun.id });
      log(`${r.ok ? '+' : '!'} org_admin ${jieun.email} -> ${r.status}`);
    }
  } else {
    log('! jieun@nexusai.dev not found; run hack/seed_demo.py first');
  }
}

// ── webhooks ──
console.log('webhooks');
const wh = await admin('POST', '/webhooks', {
  url: 'https://hooks.example.com/gshare',
  events: ['session.status_changed', 'wallet.low_balance', 'node_health.event'],
  secret: 'ux-audit-secret-value',
  org_id: org?.id ?? null,
});
log(`${wh.ok ? '+' : '!'} webhook -> ${wh.status} ${wh.ok ? '' : JSON.stringify(wh.json).slice(0, 140)}`);

console.log('\ndone');
