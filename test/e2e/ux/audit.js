#!/usr/bin/env node
// Persona-driven UX audit of the console: signs in as each persona, walks every screen they can
// reach at their viewport and language, and runs the DOM audits and interaction probes against it.
// Needs the control plane only — no GPU, no Kubernetes cluster.
//
//   ORIGIN=http://localhost:8000 node test/e2e/ux/audit.js
//
// Writes out/findings.json and out/findings.md.

import { chromium } from 'playwright';
import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { PERSONAS, VIEWPORTS } from './personas.js';
import { routesFor } from './routes.js';
import { AUDITS, installHelpers } from './audits.js';
import { FLOWS } from './flows.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const ORIGIN = process.env.ORIGIN || 'http://localhost:8000';
const API = process.env.API_ORIGIN || ORIGIN;
const OUT = process.env.UX_OUT || join(HERE, 'out');
const ONLY = process.env.UX_PERSONA;          // audit a single persona
const SETTLE = Number(process.env.UX_SETTLE || 1200);

const findings = [];
const record = (ctx, audit, list) => {
  for (const f of list) {
    findings.push({
      ...f,
      audit,
      persona: ctx.persona.id,
      role: ctx.persona.role,
      route: ctx.route.id,
      path: ctx.route.path,
      viewport: ctx.persona.viewport,
      locale: ctx.locale,
    });
  }
};

/**
 * Sign in over the API and return the bearer. A seeded account carries must_change_password, which
 * redirects every route until cleared; it is cleared once to a derived password and reused.
 */
async function tokenFor(persona) {
  const settled = persona.password + 'Ux1';
  const login = async (password) => {
    const r = await fetch(`${API}/api/v1/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: persona.email, password }),
    });
    return r.ok ? (await r.json()).access_token : null;
  };
  const mustChange = (tok) => {
    try { return !!JSON.parse(Buffer.from(tok.split('.')[1], 'base64url').toString()).must_change_password; }
    catch { return false; }
  };

  let tok = (await login(settled)) || (await login(persona.password));
  if (!tok) throw new Error(`cannot sign in as ${persona.email}`);
  if (mustChange(tok)) {
    const r = await fetch(`${API}/api/v1/auth/change-password`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tok}` },
      body: JSON.stringify({ new_password: settled }),
    });
    if (!r.ok) throw new Error(`cannot clear must_change_password for ${persona.email}: ${r.status}`);
    tok = (await r.json()).access_token;
  }
  return tok;
}

/** Live identifiers, so detail screens are audited with real content. */
async function discoverIds(token) {
  // Collections are not uniform: a bare array from some endpoints, {data, pagination} from others.
  const get = async (p) => {
    try {
      const r = await fetch(`${API}/api/v1${p}`, { headers: { Authorization: `Bearer ${token}` } });
      if (!r.ok) return null;
      const j = await r.json();
      return Array.isArray(j) ? j : (j.data ?? j.items ?? null);
    } catch { return null; }
  };
  const first = (arr) => (Array.isArray(arr) && arr.length ? arr[0].id : '');
  const [sessions, volumes, users, groups, orgs, offerings] = await Promise.all([
    get('/sessions'), get('/storage/volumes'), get('/users?size=100'), get('/projects?size=100'),
    get('/organizations?size=100'), get('/offerings?size=100'),
  ]);
  // Record values — typed names, hardware strings — which the i18n audits treat as content rather
  // than as untranslated interface text.
  const names = [...(users ?? []), ...(groups ?? []), ...(orgs ?? []), ...(volumes ?? []), ...(sessions ?? []), ...(offerings ?? [])]
    .flatMap((r) => [r.name, r.hostname, r.email, r.gpu_model, r.model])
    .filter((n) => typeof n === 'string' && n.length > 2);

  return {
    ids: {
      sessionId: first(sessions),
      volumeId: first(volumes),
      userId: first(users),
      groupId: first(groups),
      orgId: first(orgs),
      offeringId: first(offerings),
    },
    userData: [...new Set(names)],
  };
}

async function auditPersona(browser, persona) {
  const token = await tokenFor(persona);
  const { ids, userData } = await discoverIds(token);
  const viewport = VIEWPORTS[persona.viewport];

  const context = await browser.newContext({
    viewport,
    locale: persona.locale === 'ko' ? 'ko-KR' : 'en-US',
    deviceScaleFactor: 1,
    hasTouch: persona.viewport === 'phone' || persona.viewport === 'tablet',
  });
  // Persisted auth store and language, so every screen loads signed in.
  await context.addInitScript(([tok, lang]) => {
    const claims = JSON.parse(decodeURIComponent(escape(atob(tok.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')))));
    window.localStorage.setItem('gshare.lang', lang);
    window.localStorage.setItem('gshare-auth', JSON.stringify({
      state: { accessToken: tok, isAuthed: true, claims, memberships: [], orgAdminOrgs: [] },
      version: 0,
    }));
  }, [token, persona.locale]);

  const page = await context.newPage();
  const consoleErrors = [];
  const netErrors = [];
  let currentRoute = null;
  let probing = false;
  page.on('console', (m) => { if (m.type() === 'error' && !probing) consoleErrors.push({ route: currentRoute?.id, text: m.text().slice(0, 300) }); });
  // An uncaught exception counts whether or not a probe caused it.
  page.on('pageerror', (e) => consoleErrors.push({ route: currentRoute?.id, text: `uncaught: ${String(e).slice(0, 300)}` }));
  page.on('response', (r) => {
    if (!probing && r.status() >= 400 && r.url().includes('/api/')) netErrors.push({ route: currentRoute?.id, status: r.status(), url: new URL(r.url()).pathname });
  });

  const routes = routesFor(persona, ids);
  process.stdout.write(`\n[${persona.id}] ${persona.role} · ${persona.viewport} · ${persona.locale} · ${routes.length} screens\n`);

  for (const route of routes) {
    currentRoute = route;
    const ctx = { persona, route, viewport, locale: persona.locale, userData };
    try {
      await page.goto(ORIGIN + route.path, { waitUntil: 'domcontentloaded', timeout: 20000 });
      await page.waitForTimeout(SETTLE);

      // A guard may have redirected this persona. Auditing what landed instead would file another
      // screen's findings under this route, so the redirect is recorded and the screen skipped.
      const landed = new URL(page.url()).pathname;
      if (landed !== route.path.split('?')[0]) {
        record(ctx, 'load', [{
          rule: 'route.redirected',
          severity: 'polish',
          message: `${persona.id} (${persona.role}) asked for ${route.path} and was sent to ${landed}; the screen was not audited.`,
          selector: 'body',
          evidence: { from: route.path, to: landed },
        }]);
        process.stdout.write('>');
        continue;
      }

      await installHelpers(page);
      for (const [name, fn] of AUDITS) {
        try {
          record(ctx, name, await fn(page, ctx));
        } catch (e) {
          record(ctx, name, [{ rule: 'audit.crashed', severity: 'minor', message: `The ${name} audit threw: ${String(e).slice(0, 160)}`, selector: 'body' }]);
        }
      }
      // Probes run last: they type and click, leaving a DOM the static audits could not read.
      if (!process.env.UX_SKIP_FLOWS) {
        probing = true;
        for (const [name, fn] of FLOWS) {
          try {
            record(ctx, name, await fn(page, ctx, ORIGIN));
          } catch (e) {
            record(ctx, name, [{ rule: 'audit.crashed', severity: 'polish', message: `The ${name} probe threw: ${String(e).slice(0, 160)}`, selector: 'body' }]);
          }
        }
        probing = false;
      }
      process.stdout.write('.');
    } catch (e) {
      record(ctx, 'load', [{ rule: 'page.loadFailed', severity: 'blocker', message: `The screen did not load: ${String(e).slice(0, 160)}`, selector: 'body' }]);
      process.stdout.write('x');
    }
  }

  for (const e of consoleErrors) {
    findings.push({ rule: 'runtime.consoleError', severity: 'major', message: `The browser console reports an error on this screen: ${e.text}`, selector: 'body', audit: 'runtime', persona: persona.id, role: persona.role, route: e.route || 'unknown', path: '', viewport: persona.viewport, locale: persona.locale });
  }
  for (const e of netErrors) {
    findings.push({ rule: 'runtime.apiError', severity: e.status >= 500 ? 'blocker' : 'major', message: `${e.status} from ${e.url} while rendering this screen.`, selector: 'body', audit: 'runtime', persona: persona.id, role: persona.role, route: e.route || 'unknown', path: e.url, viewport: persona.viewport, locale: persona.locale });
  }

  await context.close();
}

// ── report ──
const SEV_ORDER = ['blocker', 'major', 'minor', 'polish'];

function report() {
  mkdirSync(OUT, { recursive: true });

  // One job per (rule, route, selector), however many personas hit it.
  const jobs = new Map();
  for (const f of findings) {
    const key = `${f.rule}::${f.route}::${f.selector || ''}`;
    const j = jobs.get(key) || { ...f, personas: new Set(), viewports: new Set(), locales: new Set(), count: 0 };
    j.personas.add(f.persona); j.viewports.add(f.viewport); j.locales.add(f.locale); j.count++;
    jobs.set(key, j);
  }
  const list = [...jobs.values()]
    .map((j) => ({ ...j, personas: [...j.personas], viewports: [...j.viewports], locales: [...j.locales] }))
    .sort((a, b) => SEV_ORDER.indexOf(a.severity) - SEV_ORDER.indexOf(b.severity) || b.personas.length - a.personas.length);

  writeFileSync(join(OUT, 'findings.json'), JSON.stringify({ origin: ORIGIN, total: findings.length, deduped: list.length, findings: list }, null, 2));

  const byRule = {};
  for (const j of list) (byRule[j.rule] ||= []).push(j);
  const bySev = {};
  for (const j of list) bySev[j.severity] = (bySev[j.severity] || 0) + 1;

  const md = [];
  md.push('# Console UX audit\n');
  md.push(`${findings.length} observations across ${new Set(findings.map((f) => f.persona)).size} personas and ${new Set(findings.map((f) => f.route)).size} screens, deduplicated to **${list.length} distinct jobs**.\n`);
  md.push('| Severity | Jobs |\n|---|---|');
  for (const s of SEV_ORDER) md.push(`| ${s} | ${bySev[s] || 0} |`);
  md.push('\n## By rule\n');
  md.push('| Rule | Jobs | Screens | Severity |\n|---|---|---|---|');
  for (const [rule, js] of Object.entries(byRule).sort((a, b) => b[1].length - a[1].length)) {
    md.push(`| \`${rule}\` | ${js.length} | ${new Set(js.map((j) => j.route)).size} | ${js[0].severity} |`);
  }
  md.push('\n## Jobs\n');
  for (const s of SEV_ORDER) {
    const group = list.filter((j) => j.severity === s);
    if (!group.length) continue;
    md.push(`\n### ${s} (${group.length})\n`);
    for (const j of group) {
      md.push(`- **${j.route}** \`${j.rule}\` — ${j.message}`);
      md.push(`  <br/>_${j.selector}_ · personas: ${j.personas.join(', ')} · ${j.viewports.join('/')} · ${j.locales.join('/')}`);
    }
  }
  writeFileSync(join(OUT, 'findings.md'), md.join('\n'));

  process.stdout.write(`\n\n${findings.length} observations → ${list.length} distinct jobs\n`);
  for (const s of SEV_ORDER) process.stdout.write(`  ${s.padEnd(8)} ${bySev[s] || 0}\n`);
  process.stdout.write(`\nwrote ${join(OUT, 'findings.json')}\n      ${join(OUT, 'findings.md')}\n`);
}

const browser = await chromium.launch();
try {
  for (const p of PERSONAS) {
    if (ONLY && p.id !== ONLY) continue;
    try { await auditPersona(browser, p); }
    catch (e) { process.stdout.write(`\n[${p.id}] skipped: ${String(e).slice(0, 120)}\n`); }
  }
} finally {
  await browser.close();
}
report();
