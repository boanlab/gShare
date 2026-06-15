// DOM audits. Each takes a loaded page plus its context and returns findings:
//
//   { rule, severity, message, selector?, evidence? }
//
// Rules read the rendered DOM and stay narrow — one element, one defect — so the output is a work
// list rather than a score.

export const SEV = { blocker: 'blocker', major: 'major', minor: 'minor', polish: 'polish' };

/**
 * Depth below the console a screen belongs to. Both consoles are sidebar-rooted, so `/admin/users`
 * is top level, as `/sessions` is; only what sits below that owes a trail back.
 */
function consoleDepth(path) {
  const parts = path.split('/').filter(Boolean);
  if (parts[0] === 'admin') parts.shift();
  return parts.length;
}

// ── shared browser-side helpers, injected once per page ──
const HELPERS = `
  window.__ux = {
    visible(el) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) return false;
      const s = getComputedStyle(el);
      return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
    },
    label(el) {
      const aria = el.getAttribute('aria-label');
      if (aria && aria.trim()) return aria.trim();
      const by = el.getAttribute('aria-labelledby');
      if (by) {
        const t = by.split(/\\s+/).map((id) => document.getElementById(id)?.textContent || '').join(' ').trim();
        if (t) return t;
      }
      if (el.id) {
        const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
        if (l && l.textContent.trim()) return l.textContent.trim();
      }
      const wrap = el.closest('label');
      if (wrap && wrap.textContent.trim()) return wrap.textContent.trim();
      const title = el.getAttribute('title');
      if (title && title.trim()) return title.trim();
      return '';
    },
    path(el) {
      const parts = [];
      for (let n = el; n && n.nodeType === 1 && parts.length < 5; n = n.parentElement) {
        let s = n.tagName.toLowerCase();
        if (n.id) { parts.unshift(s + '#' + n.id); break; }
        const cls = (n.getAttribute('class') || '').split(/\\s+/).filter(Boolean).slice(0, 2).join('.');
        if (cls) s += '.' + cls;
        parts.unshift(s);
      }
      return parts.join(' > ');
    },
    text(el) { return (el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 80); },
  };
`;

export async function installHelpers(page) {
  await page.evaluate(HELPERS);
}

// ── 1. page semantics: title, landmarks, headings ──
export async function auditSemantics(page, ctx) {
  return page.evaluate(({ routeTitle }) => {
    const f = [];
    const add = (rule, severity, message, selector, evidence) => f.push({ rule, severity, message, selector, evidence });

    const title = document.title.trim();
    if (!title) add('page.title.missing', 'major', 'The document has no <title>, so browser tabs, history and bookmarks are unlabelled.', 'head > title');
    else if (/^GShare$/i.test(title)) {
      add('page.title.notPerScreen', 'major',
        `Every screen shares the title "${title}". A user with several tabs open cannot tell which is which, and history entries are indistinguishable.`,
        'head > title', { expected: `${routeTitle} · GShare` });
    }

    if (!document.querySelector('main')) add('page.landmark.main', 'major', 'No <main> landmark, so screen readers and skip links have no target for the primary content.', 'body');
    // The signed-out and error screens carry no console chrome.
    const hasShell = !!document.querySelector('.gs-shell');
    if (hasShell && !document.querySelector('header, [role="banner"]')) add('page.landmark.banner', 'minor', 'No banner landmark around the top bar.', 'body');
    if (hasShell && !document.querySelector('nav, [role="navigation"]')) add('page.landmark.nav', 'minor', 'Sidebar navigation is not marked up as <nav>.', 'body');

    const h1 = [...document.querySelectorAll('h1')].filter((e) => window.__ux.visible(e));
    if (h1.length === 0) add('page.heading.noH1', 'major', 'No visible <h1>: the screen has no programmatic name.', 'body');
    if (h1.length > 1) add('page.heading.multipleH1', 'minor', `${h1.length} <h1> elements compete to name the screen.`, 'body', { texts: h1.map((e) => window.__ux.text(e)) });

    const levels = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].filter((e) => window.__ux.visible(e));
    let prev = 0;
    for (const h of levels) {
      const lvl = Number(h.tagName[1]);
      if (prev && lvl > prev + 1) {
        add('page.heading.levelSkip', 'minor', `Heading jumps from h${prev} to h${lvl}, breaking the outline a screen reader announces.`, window.__ux.path(h), { text: window.__ux.text(h) });
      }
      prev = lvl;
    }

    if (hasShell && !document.querySelector('a[href^="#"].gs-skip-link, a[href="#main"], a.skip-link, [data-skip-link]')) {
      add('page.skipLink.missing', 'minor', 'No skip-to-content link, so a keyboard user tabs through the whole sidebar on every screen.', 'body');
    }

    const html = document.documentElement;
    if (!html.getAttribute('lang')) add('page.lang.missing', 'major', '<html> has no lang attribute; screen readers pick the wrong voice.', 'html');
    return f;
  }, { routeTitle: ctx.route.title });
}

// ── 2. forms ──
export async function auditForms(page) {
  return page.evaluate(() => {
    const f = [];
    const add = (rule, severity, message, selector, evidence) => f.push({ rule, severity, message, selector, evidence });
    const fields = [...document.querySelectorAll('input, select, textarea')].filter((e) => window.__ux.visible(e) && e.type !== 'hidden');

    for (const el of fields) {
      const sel = window.__ux.path(el);
      const name = window.__ux.label(el);
      const ph = el.getAttribute('placeholder') || '';
      const type = (el.getAttribute('type') || el.tagName).toLowerCase();

      if (!name && !ph) add('form.field.noName', 'blocker', `${type} field has no label, aria-label or placeholder — nothing announces what it is.`, sel);
      else if (!name && ph) add('form.field.placeholderAsLabel', 'major', `"${ph}" is a placeholder standing in for a label: it disappears the moment the user types, so they lose the field's name mid-entry.`, sel, { placeholder: ph });

      if (el.required && !el.getAttribute('aria-required')) add('form.field.requiredNotAnnounced', 'minor', `Required field "${name || ph}" is not marked aria-required.`, sel);
      if (el.required) {
        const labelled = el.id ? document.querySelector('label[for="' + CSS.escape(el.id) + '"]') : null;
        const wrap = el.closest('label') || labelled || el.parentElement;
        const marked = /\*|required|필수/i.test((wrap?.textContent || '') + ' ' + name);
        if (!marked) add('form.field.requiredNoMarker', 'major', `Required field "${name || ph}" carries no visible required marker, so the user only discovers it on submit.`, sel);
      }

      if (type !== 'search' && /email|password|username|tel|name/.test(type + ' ' + (el.name || '') + ' ' + name.toLowerCase()) && !el.getAttribute('autocomplete')) {
        add('form.field.noAutocomplete', 'minor', `"${name || ph}" has no autocomplete attribute, so password managers and browser autofill skip it.`, sel);
      }

      if (type === 'number') {
        if (el.min === '' ) add('form.number.noMin', 'minor', `Numeric field "${name || ph}" has no min, so a negative value reaches the API before it is rejected.`, sel);
        if (el.max === '') add('form.number.noMax', 'minor', `Numeric field "${name || ph}" has no max, so a typo of an extra digit is only caught server-side.`, sel);
        if (!el.getAttribute('step')) add('form.number.noStep', 'polish', `Numeric field "${name || ph}" has no step, so the spinner increments by 1 regardless of the unit.`, sel);
        if (!el.getAttribute('inputmode')) add('form.number.noInputmode', 'minor', `Numeric field "${name || ph}" has no inputmode, so phones show the alphabetic keyboard.`, sel);
      }

      if (el.tagName === 'TEXTAREA' && !el.getAttribute('maxlength')) {
        add('form.textarea.noLimit', 'polish', `Textarea "${name || ph}" has no maxlength, so an over-long value is only rejected after the round trip.`, sel);
      }
      if (el.tagName === 'SELECT' && el.options.length > 12 && !el.getAttribute('data-searchable')) {
        add('form.select.longList', 'minor', `Select "${name || ph}" has ${el.options.length} options and no type-ahead filter.`, sel);
      }
      if (el.getAttribute('aria-invalid') === null && el.required) {
        add('form.field.noInvalidState', 'polish', `Required field "${name || ph}" never sets aria-invalid, so an error is visual only.`, sel);
      }
    }

    // Forms as a whole
    const forms = [...document.querySelectorAll('form')];
    const submitters = [...document.querySelectorAll('button')].filter((b) => window.__ux.visible(b) && /save|create|submit|register|request|apply|launch|저장|생성|등록|신청/i.test(b.textContent || ''));
    // Filters and row checkboxes are not composed input.
    const entryFields = fields.filter((e) => !['search', 'checkbox', 'radio'].includes((e.getAttribute('type') || '').toLowerCase()))
      .filter((e) => !e.closest('[data-url-state]'));
    if (entryFields.length >= 2 && forms.length === 0 && submitters.length > 0) {
      add('form.notAForm', 'major', `${entryFields.length} fields and a submit button, but no <form> element: pressing Enter in a field does nothing, which is the fastest path for a keyboard user.`, 'body');
    }
    for (const form of forms) {
      if (!form.querySelector('[role="alert"], [aria-live]')) {
        add('form.noLiveRegion', 'minor', 'Form has no aria-live region, so validation errors are announced to nobody.', window.__ux.path(form));
      }
    }
    const dataFields = fields.filter((e) => !['search', 'checkbox', 'radio', 'button', 'submit'].includes((e.getAttribute('type') || '').toLowerCase()))
      .filter((e) => !e.closest('[data-url-state]'));
    const signInScreen = /\/(login|change-password)$/.test(location.pathname);
    if (!signInScreen && dataFields.length >= 3 && !document.querySelector('[data-unsaved-guard]')) {
      add('form.noUnsavedGuard', 'major', `A ${dataFields.length}-field form with no unsaved-changes guard: navigating away silently discards everything typed.`, 'body');
    }
    return f;
  });
}

// ── 3. lists and tables ──
export async function auditTables(page) {
  return page.evaluate(() => {
    const f = [];
    const add = (rule, severity, message, selector, evidence) => f.push({ rule, severity, message, selector, evidence });
    const tables = [...document.querySelectorAll('table')].filter((e) => window.__ux.visible(e));

    for (const t of tables) {
      if (t.hasAttribute('data-preview')) continue;
      const sel = window.__ux.path(t);
      const rows = t.querySelectorAll('tbody tr').length;
      const heads = [...t.querySelectorAll('thead th')];

      if (!t.querySelector('caption') && !t.getAttribute('aria-label') && !t.getAttribute('aria-labelledby')) {
        add('table.noAccessibleName', 'minor', 'Table has no caption or aria-label, so it is announced only as "table".', sel);
      }
      const sortable = heads.filter((h) => h.querySelector('button, [role="button"]') || h.getAttribute('aria-sort'));
      if (heads.length >= 3 && sortable.length === 0) {
        add('table.noSort', 'major', `A ${heads.length}-column table with no sortable header: the user cannot order by cost, age or status, which is the first thing anyone wants.`, sel, { columns: heads.map((h) => window.__ux.text(h)) });
      }
      if (rows > 25 && !document.querySelector('[data-pagination], nav[aria-label*="agin"]')) {
        add('table.noPagination', 'major', `${rows} rows render with no pagination or virtualisation.`, sel);
      }
      if (rows >= 5) {
        const filter = document.querySelector('input[type="search"], input[placeholder*="earch"], input[placeholder*="ilter"], input[placeholder*="검색"]');
        if (!filter) add('table.noFilter', 'major', `${rows} rows with no search or filter box: finding one item means reading the whole list.`, sel);
      }
      // Only where a per-row action exists to batch.
      const rowActions = t.querySelectorAll('tbody tr button, tbody tr a[class*="gs-btn"]').length;
      if (rows >= 5 && rowActions >= rows && !t.querySelector('input[type="checkbox"]')) {
        add('table.noBulkSelect', 'minor', `${rows} rows each carry their own action button, but there is no way to select several and act once.`, sel);
      }
      if (t.scrollWidth > t.clientWidth + 4) {
        add('table.horizontalOverflow', 'major', `Table overflows its container by ${t.scrollWidth - t.clientWidth}px and is cut off rather than scrollable.`, sel);
      }
      // sticky header on a long table
      if (rows >= 15) {
        const th = heads[0];
        if (th && getComputedStyle(th).position !== 'sticky') {
          add('table.headerNotSticky', 'minor', `${rows} rows scroll their header out of view, so columns become unidentifiable.`, sel);
        }
      }
    }

    // Empty state quality
    const bodyText = document.body.innerText || '';
    const emptyish = /no .* yet|nothing|empty|없습니다|없음|아직/i.test(bodyText);
    if (tables.length && [...tables].every((t) => t.querySelectorAll('tbody tr').length === 0)) {
      if (!emptyish) add('list.emptyStateMissing', 'major', 'The list is empty and says nothing: the user cannot tell a broken screen from one with no data.', 'body');
      else {
        const hasCta = [...document.querySelectorAll('a, button')].some((b) => window.__ux.visible(b) && /create|new|add|start|launch|만들|추가|생성/i.test(b.textContent || ''));
        if (!hasCta) add('list.emptyStateNoAction', 'major', 'The empty state explains that there is nothing here but offers no way to create the first one.', 'body');
      }
    }
    return f;
  });
}

// ── 4. affordances and conveniences ──
export async function auditAffordance(page, ctx) {
  return page.evaluate(({ depth, live }) => {
    const f = [];
    const add = (rule, severity, message, selector, evidence) => f.push({ rule, severity, message, selector, evidence });
    const all = [...document.querySelectorAll('*')].filter((e) => e.children.length === 0 && window.__ux.visible(e));

    // Identifiers the user is expected to quote back (session ids, cluster ids, tokens)
    const idRe = /\b(ses|clu|usr|grp|org|wal|off|img|vol|nod|pol|pre)_[0-9A-Za-z]{8,}\b/;
    const seen = new Set();
    for (const el of all) {
      const txt = (el.textContent || '').trim();
      const m = txt.match(idRe);
      if (!m || seen.has(m[0])) continue;
      seen.add(m[0]);
      // A row that opens a detail view is navigation; the identifier is copyable there.
      if (el.closest('a[href], button')) continue;
      const near = el.closest('td, div, span, p, li') || el;
      const hasCopy = !!near.querySelector('button[aria-label*="opy"], button[title*="opy"], [data-copy]')
        || !!near.parentElement?.querySelector('button[aria-label*="opy"], button[title*="opy"], [data-copy]');
      if (!hasCopy) {
        add('affordance.noCopyForId', 'major', `Identifier ${m[0]} is displayed but cannot be copied with one click — the user has to select it by hand to paste it into a ticket or a command.`, window.__ux.path(el), { id: m[0] });
      }
    }

    // Timestamps: absolute-only or relative-only, without the other on hover
    const tsRe = /\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}|\b\d{1,2}\/\d{1,2}\/\d{2,4}\b/;
    const relRe = /\b\d+\s*(s|m|h|d|초|분|시간|일)\s*(ago|전)\b|just now|방금/i;
    let tsChecked = 0;
    for (const el of all) {
      const txt = (el.textContent || '').trim();
      if (tsChecked > 6) break;
      const abs = tsRe.test(txt), rel = relRe.test(txt);
      if (!abs && !rel) continue;
      tsChecked++;
      const t = el.getAttribute('title') || el.closest('[title]')?.getAttribute('title') || '';
      if (el.tagName !== 'TIME') {
        add('affordance.timestampNotSemantic', 'polish', `Timestamp "${txt.slice(0, 40)}" is plain text rather than <time datetime>, so it cannot be localised or machine-read.`, window.__ux.path(el));
      }
      if (abs && !rel && !t) add('affordance.absoluteTimeOnly', 'minor', `"${txt.slice(0, 40)}" gives an absolute time with no "how long ago", which is what the user is actually asking.`, window.__ux.path(el));
      if (rel && !abs && !t) add('affordance.relativeTimeNoExact', 'minor', `"${txt.slice(0, 40)}" is relative only, with no exact timestamp on hover — impossible to correlate with a log.`, window.__ux.path(el));
    }

    // Truncation without the full value available
    for (const el of all) {
      const s = getComputedStyle(el);
      if (s.textOverflow === 'ellipsis' && el.scrollWidth > el.clientWidth + 1 && !el.getAttribute('title')) {
        add('affordance.truncatedNoTitle', 'minor', `"${window.__ux.text(el)}" is visually truncated with no title attribute, so the full value is unreachable.`, window.__ux.path(el));
      }
    }

    // External links
    for (const a of document.querySelectorAll('a[href^="http"]')) {
      if (!window.__ux.visible(a)) continue;
      const ext = !a.href.startsWith(location.origin);
      if (ext && a.target === '_blank' && !/noopener/.test(a.rel || '')) {
        add('affordance.blankNoOpener', 'major', 'Link opens in a new tab without rel="noopener", handing the opened page a reference to this one.', window.__ux.path(a));
      }
      if (ext && a.target === '_blank' && !a.querySelector('svg, [aria-label*="new"]') && !/↗|external/i.test(a.textContent || '')) {
        add('affordance.blankUnannounced', 'polish', 'Link opens a new tab with no visual or announced hint that it will.', window.__ux.path(a));
      }
    }

    // Breadcrumbs on nested screens
    if (depth >= 2) {
      const crumbs = document.querySelector('nav[aria-label*="readcrumb"], [data-breadcrumb], ol.breadcrumb');
      if (!crumbs) add('nav.noBreadcrumb', 'major', `A ${depth}-level-deep screen with no breadcrumb: the user cannot see where they are or climb one level without the browser Back button.`, 'body');
      const back = [...document.querySelectorAll('a, button')].some((b) => window.__ux.visible(b) && /back|cancel|취소|돌아/i.test(b.textContent || ''));
      if (!back && !crumbs) add('nav.noWayBack', 'major', 'A nested screen with neither a breadcrumb nor a back or cancel control.', 'body');
    }

    // Destructive controls
    for (const b of document.querySelectorAll('button, a')) {
      if (!window.__ux.visible(b)) continue;
      if (b.getAttribute('role') === 'tab' || b.closest('[role="tablist"]')) continue;
      const t = (b.textContent || '').trim();
      // A log entry describes an action; it does not perform one.
      if (t.length > 60) continue;
      if (!/delete|remove|terminate|drain|revoke|삭제|제거|종료|해제/i.test(t)) continue;
      const styled = /danger|destructive|red/i.test(b.className || '') || /rgb\(2[0-5][0-9]|#e|#f/i.test(getComputedStyle(b).color);
      if (!styled) add('destructive.notVisuallyDistinct', 'minor', `"${t}" destroys data but is styled like every other button.`, window.__ux.path(b));
    }

    // Freshness applies to screens that show live data, not to forms.
    if (live && /status|phase|running|pending|실행|대기/i.test(document.body.innerText || '')) {
      const refresh = [...document.querySelectorAll('button')].some((b) => window.__ux.visible(b) && /refresh|reload|새로고침/i.test(b.textContent + ' ' + (b.getAttribute('aria-label') || '')));
      const stamp = /updated|as of|기준|갱신/i.test(document.body.innerText || '');
      if (!refresh && !stamp) {
        add('affordance.noFreshnessSignal', 'major', 'A screen showing live status with neither a "last updated" stamp nor a manual refresh: the user cannot tell stale data from a stalled job.', 'body');
      }
    }

    // Numbers without units
    for (const el of all) {
      const txt = (el.textContent || '').trim();
      if (el.closest('[aria-hidden="true"]')) continue;
      if (/^\d{1,3}(,\d{3})*(\.\d+)?$/.test(txt) && Number(txt.replace(/,/g, '')) > 0) {
        // Any words in the figure's own block are what it counts.
        const block = el.closest('.gs-card, .gs-stat, dl, li, td, th, button, [role="tab"]') || el.parentElement;
        let context = block?.textContent || '';
        const cell = el.closest('td');
        if (cell) {
          // The column header is the label for every figure beneath it.
          const table = cell.closest('table');
          const idx = [...(cell.parentElement?.children || [])].indexOf(cell);
          context += ' ' + (table?.querySelectorAll('thead th')[idx]?.textContent || '');
        }
        const words = context.replace(/[\d\s,.\-/]+/g, '');
        if (words.length < 2) {
          add('affordance.bareNumber', 'polish', `"${txt}" is rendered with no unit anywhere near it.`, window.__ux.path(el));
        }
      }
    }
    return f;
  }, { depth: consoleDepth(ctx.route.path), live: ['list', 'dashboard', 'detail'].includes(ctx.route.kind) });
}

// ── 5. keyboard and focus ──
export async function auditKeyboard(page) {
  return page.evaluate(() => {
    const f = [];
    const add = (rule, severity, message, selector, evidence) => f.push({ rule, severity, message, selector, evidence });
    const focusables = [...document.querySelectorAll('a[href], button, input, select, textarea, [tabindex]')]
      .filter((e) => window.__ux.visible(e) && !e.disabled && e.getAttribute('tabindex') !== '-1');

    // :focus-visible is only observable by focusing; sampled one control per class signature.
    const active = document.activeElement;
    const ringSeen = new Set();
    for (const el of focusables.slice(0, 40)) {
      const sig = el.tagName + '|' + (el.className || '');
      if (ringSeen.has(sig)) continue;
      ringSeen.add(sig);
      const before = getComputedStyle(el);
      const b = { outline: before.outlineStyle + before.outlineWidth, shadow: before.boxShadow, border: before.borderColor };
      el.focus({ preventScroll: true });
      const after = getComputedStyle(el);
      const changed = after.outlineStyle + after.outlineWidth !== b.outline || after.boxShadow !== b.shadow || after.borderColor !== b.border;
      const visibleOutline = after.outlineStyle !== 'none' && parseFloat(after.outlineWidth) > 0;
      if (!changed && !visibleOutline) {
        add('keyboard.noFocusRing', 'major',
          `"${window.__ux.text(el) || el.tagName}" looks identical focused and unfocused, so a keyboard user cannot see where they are.`,
          window.__ux.path(el));
      }
      if (el.tagName === 'DIV' || el.tagName === 'SPAN') {
        if (!el.getAttribute('role')) add('keyboard.divAsControl', 'major', `A ${el.tagName} is focusable but has no role, so it is announced as plain text.`, window.__ux.path(el));
      }
    }
    if (active instanceof HTMLElement) active.focus({ preventScroll: true }); else document.activeElement?.blur?.();

    // Click handlers on non-interactive elements are unreachable by keyboard
    for (const el of document.querySelectorAll('[onclick]')) {
      if (!/^(A|BUTTON|INPUT|SELECT|TEXTAREA)$/.test(el.tagName) && el.tabIndex < 0) {
        add('keyboard.clickOnlyControl', 'blocker', `${el.tagName} responds to click but cannot be focused, so it does not exist for a keyboard user.`, window.__ux.path(el));
      }
    }

    // Positive tabindex reorders the document unpredictably
    for (const el of document.querySelectorAll('[tabindex]')) {
      if (Number(el.getAttribute('tabindex')) > 0) {
        add('keyboard.positiveTabindex', 'major', `tabindex="${el.getAttribute('tabindex')}" forces this control out of document order.`, window.__ux.path(el));
      }
    }

    // Icon-only buttons
    for (const b of document.querySelectorAll('button')) {
      if (!window.__ux.visible(b)) continue;
      const text = (b.textContent || '').trim();
      const hasIcon = b.querySelector('svg, img') || /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(text);
      if (hasIcon && text.replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu, '').trim().length === 0) {
        if (!window.__ux.label(b)) add('keyboard.iconButtonUnnamed', 'blocker', 'Icon-only button with no accessible name: announced as "button".', window.__ux.path(b));
        if (!b.getAttribute('title')) add('affordance.iconButtonNoTooltip', 'minor', 'Icon-only button with no tooltip, so its meaning has to be guessed or discovered by clicking.', window.__ux.path(b));
      }
    }
    return f;
  });
}

// ── 6. responsive ──
export async function auditResponsive(page, ctx) {
  const vw = ctx.viewport.width;
  return page.evaluate(({ vw, isPhone }) => {
    const f = [];
    const add = (rule, severity, message, selector, evidence) => f.push({ rule, severity, message, selector, evidence });

    if (document.documentElement.scrollWidth > vw + 2) {
      const wide = [...document.querySelectorAll('*')].filter((e) => {
        const r = e.getBoundingClientRect();
        return r.width > vw + 2 && window.__ux.visible(e) && e.children.length < 30;
      }).slice(0, 5);
      add('responsive.horizontalScroll', 'major',
        `The page is ${document.documentElement.scrollWidth}px wide in a ${vw}px viewport, so the whole layout scrolls sideways.`,
        'body', { widest: wide.map((e) => ({ sel: window.__ux.path(e), w: Math.round(e.getBoundingClientRect().width) })) });
    }

    if (isPhone) {
      // Controls only: a link inside a sentence is text, and sr-only helpers are 1x1 by design.
      const targets = [...document.querySelectorAll('a[href], button, input[type="checkbox"], input[type="radio"], select')]
        .filter((e) => window.__ux.visible(e))
        .filter((e) => !e.closest('.gs-sr-only') && !e.classList.contains('gs-sr-only') && !e.classList.contains('gs-skip-link'))
        .filter((e) => {
          if (e.tagName !== 'A') return true;
          const cls = e.className || '';
          if (/\bgs-btn\b/.test(cls)) return true;
          // No border and no fill: text, however its parent lays it out.
          const s = getComputedStyle(e);
          const bordered = parseFloat(s.borderTopWidth) > 0 || parseFloat(s.borderBottomWidth) > 0
            || parseFloat(s.borderLeftWidth) > 0 || parseFloat(s.borderRightWidth) > 0;
          const filled = s.backgroundColor && !/rgba?\(0, 0, 0, 0\)|transparent/.test(s.backgroundColor);
          return bordered || filled;
        });
      for (const el of targets) {
        const r = el.getBoundingClientRect();
        if (r.height > 0 && (r.height < 40 || r.width < 40)) {
          add('responsive.tapTargetTooSmall', 'major',
            `"${window.__ux.text(el) || el.tagName}" is ${Math.round(r.width)}×${Math.round(r.height)}px — below the 44px a thumb reliably hits.`,
            window.__ux.path(el));
        }
      }
      for (const el of [...document.querySelectorAll('p, td, span, div, label')].filter((e) => e.children.length === 0 && window.__ux.visible(e) && (e.textContent || '').trim())) {
        const fs = parseFloat(getComputedStyle(el).fontSize);
        if (fs && fs < 12) add('responsive.textTooSmall', 'minor', `"${window.__ux.text(el)}" renders at ${fs}px on a phone.`, window.__ux.path(el));
      }
      const table = document.querySelector('table');
      if (table && table.getBoundingClientRect().width > vw) {
        add('responsive.tableNotAdaptive', 'major', 'A wide table is shown as-is on a phone rather than collapsing to cards or a scroll container.', window.__ux.path(table));
      }
    }
    return f;
  }, { vw, isPhone: vw <= 480 });
}

// ── 7. i18n ──
export async function auditI18n(page, ctx) {
  return page.evaluate(({ locale, userData }) => {
    const f = [];
    const add = (rule, severity, message, selector, evidence) => f.push({ rule, severity, message, selector, evidence });
    const leaves = [...document.querySelectorAll('*')].filter((e) => e.children.length === 0 && window.__ux.visible(e));

    for (const el of leaves) {
      const txt = (el.textContent || '').trim();
      if (!txt) continue;
      // A raw i18next key reached the DOM
      if (/^[a-z][a-zA-Z0-9]*(\.[a-zA-Z0-9_]+){1,4}$/.test(txt) && !/\.(js|ts|json|yaml|sh|py|md|com|dev|io)$/.test(txt)) {
        add('i18n.rawKeyRendered', 'blocker', `The translation key "${txt}" is showing instead of its text.`, window.__ux.path(el), { key: txt });
      }
      const hangul = /[가-힯]/.test(txt);
      // Record names stay as typed in either language; matched against live values.
      const userContent = el.closest('td, option, [data-user-content]')
        || userData.some((name) => txt.includes(name));
      if (locale === 'en' && hangul && !userContent) {
        add('i18n.untranslatedKorean', 'major', `Korean text "${txt.slice(0, 50)}" appears while the console is in English.`, window.__ux.path(el), { text: txt.slice(0, 80) });
      }
      // Prose, not vocabulary: a run of acronyms is not an untranslated sentence.
      const proseWords = txt.split(/\s+/).filter((w) => /^[A-Za-z][a-z]{2,}/.test(w));
      if (locale === 'ko' && !hangul && !userContent && proseWords.length >= 3) {
        add('i18n.untranslatedEnglish', 'minor', `English sentence "${txt.slice(0, 50)}" is left untranslated in the Korean console.`, window.__ux.path(el), { text: txt.slice(0, 80) });
      }
    }

    if (document.documentElement.getAttribute('lang') !== locale) {
      add('i18n.htmlLangMismatch', 'major', `<html lang="${document.documentElement.getAttribute('lang')}"> does not match the active language "${locale}".`, 'html');
    }
    return f;
  }, { locale: ctx.locale, userData: ctx.userData ?? [] });
}

// ── 8. state in the URL ──
export async function auditUrlState(page, ctx) {
  if (ctx.route.kind !== 'list') return [];
  return page.evaluate(() => {
    const f = [];
    const controls = [...document.querySelectorAll('input[type="search"], [role="tab"], [data-filter]')]
      .filter((e) => window.__ux.visible(e));
    if (document.querySelector('[data-url-state]')) return f;
    if (controls.length && !location.search) {
      f.push({
        rule: 'state.filtersNotInUrl',
        severity: 'major',
        message: `${controls.length} filter or tab controls change what the screen shows, but none of it is in the URL: refreshing, sharing a link or pressing Back all lose the view the user set up.`,
        selector: 'body',
        evidence: { controls: controls.map((c) => window.__ux.label(c) || c.tagName) },
      });
    }
    return f;
  });
}

// ── 9. contrast (cheap, deterministic subset of what axe does) ──
export async function auditContrast(page) {
  return page.evaluate(() => {
    const f = [];
    const lum = (c) => {
      const [r, g, b] = c.map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const parse = (s) => (s.match(/\d+(\.\d+)?/g) || []).slice(0, 3).map(Number);
    const bgOf = (el) => {
      for (let n = el; n; n = n.parentElement) {
        const b = getComputedStyle(n).backgroundColor;
        if (b && !/rgba?\(0, 0, 0, 0\)|transparent/.test(b)) return parse(b);
      }
      return [255, 255, 255];
    };
    const leaves = [...document.querySelectorAll('*')].filter((e) => e.children.length === 0 && window.__ux.visible(e) && (e.textContent || '').trim());
    const seen = new Set();
    for (const el of leaves) {
      const s = getComputedStyle(el);
      const fg = parse(s.color), bg = bgOf(el);
      if (fg.length < 3) continue;
      const l1 = lum(fg), l2 = lum(bg);
      const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
      const size = parseFloat(s.fontSize), bold = Number(s.fontWeight) >= 700;
      const need = size >= 24 || (size >= 18.66 && bold) ? 3 : 4.5;
      if (ratio < need) {
        const key = s.color + '|' + bg.join(',');
        if (seen.has(key)) continue;
        seen.add(key);
        f.push({
          rule: 'contrast.belowWcagAA',
          severity: ratio < need - 1.5 ? 'major' : 'minor',
          message: `"${window.__ux.text(el)}" has a contrast ratio of ${ratio.toFixed(2)}:1 against its background, below the ${need}:1 WCAG AA needs at ${size}px.`,
          selector: window.__ux.path(el),
          evidence: { color: s.color, background: `rgb(${bg.join(',')})`, ratio: Number(ratio.toFixed(2)) },
        });
      }
    }
    return f;
  });
}

export const AUDITS = [
  ['semantics', auditSemantics],
  ['forms', auditForms],
  ['tables', auditTables],
  ['affordance', auditAffordance],
  ['keyboard', auditKeyboard],
  ['responsive', auditResponsive],
  ['i18n', auditI18n],
  ['urlState', auditUrlState],
  ['contrast', auditContrast],
];
