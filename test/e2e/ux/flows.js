// Interaction probes: submit an empty form, type a bad value, press delete, filter a list to
// nothing, open a dialog and press Escape.
//
// Probes drive real screens. `window.confirm` is stubbed to decline, confirmation screens are
// skipped, and an offered undo is taken — but a run still changes state. Point it at a throwaway
// deployment.

const PRIMARY = /save|create|submit|register|request|apply|launch|start|add|import|build|next|continue|저장|생성|등록|신청|시작|다음|추가/i;
const DESTRUCTIVE = /delete|remove|terminate|drain|revoke|삭제|제거|종료|해제/i;

const find = (page, re) =>
  page.locator('button:visible, a[role="button"]:visible').filter({ hasText: re }).first();

/** Stub the native dialogs: records the call, declines, and lets nothing happen. */
async function trapNativeDialogs(page) {
  await page.evaluate(() => {
    window.__uxDialogs = [];
    for (const kind of ['confirm', 'alert', 'prompt']) {
      const orig = window[kind];
      window[kind] = (msg) => { window.__uxDialogs.push({ kind, msg: String(msg).slice(0, 200) }); return kind === 'confirm' ? false : null; };
      window[`__orig_${kind}`] = orig;
    }
  });
}
const nativeDialogs = (page) => page.evaluate(() => window.__uxDialogs || []);

/** Text that appeared on the page as a result of an action. */
async function newText(page, before) {
  const after = await page.evaluate(() => document.body.innerText);
  const b = new Set(before.split('\n').map((s) => s.trim()));
  return after.split('\n').map((s) => s.trim()).filter((s) => s && !b.has(s));
}

// ── 1. submitting an empty form ──
export async function flowEmptySubmit(page, ctx) {
  // Not `destructive`: there the primary button is the deletion itself.
  if (!['form', 'wizard'].includes(ctx.route.kind)) return [];
  const f = [];
  const btn = find(page, PRIMARY);
  if (!(await btn.count())) return [];

  const enabled = await btn.isEnabled();
  const before = await page.evaluate(() => document.body.innerText);
  const url = page.url();

  if (!enabled) {
    // Disabled is fine as long as the screen says what is missing.
    const explained = await page.evaluate(() => /required|fill|enter|choose|select|필수|입력|선택/i.test(document.body.innerText));
    if (!explained) {
      f.push({
        rule: 'flow.submitDisabledUnexplained', severity: 'major', selector: 'button[type=submit]',
        message: 'The primary button starts disabled and nothing on the screen says which field is holding it back, so the user is left clicking a dead button and guessing.',
      });
    }
    return f;
  }

  await trapNativeDialogs(page);
  await btn.click({ timeout: 3000 }).catch(() => {});
  // Wait out a request in flight before judging what the screen said.
  await page.waitForTimeout(600);
  await page
    .waitForFunction(() => !document.querySelector('button[disabled][type="submit"], [aria-busy="true"]'), null, { timeout: 4000 })
    .catch(() => {});
  await page.waitForTimeout(400);

  const dialogs = await nativeDialogs(page);
  const added = await newText(page, before);
  const inlineErr = await page.evaluate(() =>
    !!document.querySelector('[role="alert"], [aria-invalid="true"], .text-danger, .error, [data-error]'));
  const navigated = page.url() !== url;

  if (dialogs.length) {
    f.push({
      rule: 'flow.nativeDialog', severity: 'major', selector: 'window.alert',
      message: `Validation is reported through a native ${dialogs[0].kind}() ("${dialogs[0].msg}"), which cannot be styled, cannot be translated by the app, blocks the whole tab and disappears without a trace.`,
    });
  }
  if (!inlineErr && !dialogs.length && !navigated) {
    f.push({
      rule: 'flow.emptySubmitSilent', severity: 'blocker', selector: 'form',
      message: `Submitting the form with every field empty produced no inline error, no toast and no navigation${added.length ? ` (only "${added[0]}" changed)` : ''} — the user cannot tell whether it worked, failed or is still going.`,
    });
  }
  if (!inlineErr && added.some((t) => /error|failed|invalid|422|400|500/i.test(t))) {
    f.push({
      rule: 'flow.serverErrorNotInline', severity: 'major', selector: 'form',
      message: `The empty submit was rejected by the server ("${added.find((t) => /error|failed|invalid|4\d\d|5\d\d/i.test(t)).slice(0, 90)}") rather than caught in the browser, and the message is not attached to the field that caused it.`,
    });
  }
  return f;
}

// ── 2. obviously invalid values ──
export async function flowInvalidInput(page, ctx) {
  if (!['form', 'wizard'].includes(ctx.route.kind)) return [];
  const f = [];

  const email = page.locator('input[type="email"]:visible').first();
  if (await email.count()) {
    await email.fill('not-an-email');
    await email.blur();
    await page.waitForTimeout(500);
    const flagged = await page.evaluate(() => {
      const e = document.querySelector('input[type="email"]');
      return !!e && (e.getAttribute('aria-invalid') === 'true' || !!e.closest('label, div')?.querySelector('[role="alert"], .text-danger, .error'));
    });
    if (!flagged) {
      f.push({
        rule: 'flow.emailNotValidatedOnBlur', severity: 'major', selector: 'input[type=email]',
        message: 'Leaving the email field with "not-an-email" in it raises nothing: the mistake survives until submit, and on a long form that means scrolling back to find it.',
      });
    }
  }

  for (const [sel, value, rule, why] of [
    ['input[type="number"]:visible', '-1', 'flow.negativeAccepted', 'a negative quantity'],
    ['input[type="number"]:visible', '999999999', 'flow.hugeNumberAccepted', 'a nine-digit quantity'],
  ]) {
    const n = page.locator(sel).first();
    if (!(await n.count())) continue;
    await n.fill(value);
    await n.blur();
    await page.waitForTimeout(300);
    const kept = await n.inputValue();
    const flagged = await page.evaluate(() => !!document.querySelector('[aria-invalid="true"], [role="alert"], .text-danger'));
    if (kept === value && !flagged) {
      f.push({
        rule, severity: 'minor', selector: sel,
        message: `The form accepts ${why} without comment, so the first sign of trouble is a rejection from the API after the user has committed to the action.`,
      });
    }
  }
  return f;
}

// ── 3. destructive actions ──
export async function flowDestructive(page, ctx) {
  // A confirmation screen is itself the confirmation; only in-list controls are probed.
  if (ctx?.route?.kind === 'destructive') return [];
  const f = [];
  const btn = find(page, DESTRUCTIVE);
  if (!(await btn.count())) return [];
  const label = (await btn.innerText().catch(() => '')).trim();

  await trapNativeDialogs(page);
  const before = await page.evaluate(() => document.body.innerText);
  await btn.click({ timeout: 3000 }).catch(() => {});
  await page.waitForTimeout(1000);

  const dialogs = await nativeDialogs(page);
  const modal = await page.evaluate(() => !!document.querySelector('[role="dialog"], [role="alertdialog"], dialog[open]'));
  const added = await newText(page, before);

  if (dialogs.length) {
    f.push({
      rule: 'flow.destructiveNativeConfirm', severity: 'major', selector: 'window.confirm',
      message: `"${label}" guards itself with a native confirm() ("${dialogs[0].msg}"). It cannot show what is about to be lost, cannot be translated with the rest of the console, and offers no typed confirmation for the irreversible case.`,
    });
  } else if (!modal && added.length === 0) {
    f.push({
      rule: 'flow.destructiveNoConfirm', severity: 'blocker', selector: 'button',
      message: `"${label}" appears to act immediately with no confirmation step and no undo.`,
    });
  }
  // Take the offered undo: exercises it, and leaves the fixture as it was found.
  const undoButton = page.locator('[role="status"] button, .gs-card button').filter({ hasText: /undo|되돌|실행 취소/i }).first();
  const undoOffered = (await undoButton.count()) > 0;
  if (undoOffered) {
    await undoButton.click({ timeout: 2000 }).catch(() => {});
    await page.waitForTimeout(800);
  }

  if (dialogs.length || modal || undoOffered) {
    // Either undo, or a confirmation that asks for the record's name.
    const body = (await page.evaluate(() => document.body.innerText)) || '';
    const undo = undoOffered || /undo|revert|되돌/i.test(body);
    const typed = await page.locator('[role="alertdialog"] input, [role="dialog"] input').count();
    if (!undo && !typed) {
      f.push({
        rule: 'flow.destructiveNoUndo', severity: 'minor', selector: 'button',
        message: `"${label}" is one click from final: no undo window, and the confirmation does not ask for the record's name. A misclick here costs whatever the record was.`,
      });
    }
  }

  // Close the dialog: an open overlay would block every probe that follows.
  if (modal) {
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);
  }
  return f;
}

// ── 4. dialogs ──
export async function flowDialog(page) {
  const f = [];
  const opener = page.locator('button:visible').filter({ hasText: /new|add|edit|share|invite|추가|편집|공유/i }).first();
  if (!(await opener.count())) return [];
  const openerBox = await opener.boundingBox().catch(() => null);
  await opener.click({ timeout: 3000 }).catch(() => {});
  await page.waitForTimeout(700);

  const dlg = page.locator('[role="dialog"], [role="alertdialog"], dialog[open]').first();
  if (!(await dlg.count())) return [];

  if (!(await dlg.getAttribute('aria-modal'))) {
    f.push({ rule: 'dialog.notModal', severity: 'minor', selector: '[role=dialog]', message: 'The dialog is not marked aria-modal, so a screen reader keeps reading the page behind it.' });
  }
  if (!(await dlg.getAttribute('aria-label')) && !(await dlg.getAttribute('aria-labelledby'))) {
    f.push({ rule: 'dialog.unnamed', severity: 'major', selector: '[role=dialog]', message: 'The dialog has no accessible name, so it opens as an unlabelled region.' });
  }
  const focusInside = await page.evaluate(() => {
    const d = document.querySelector('[role="dialog"], [role="alertdialog"], dialog[open]');
    return !!d && d.contains(document.activeElement);
  });
  if (!focusInside) {
    f.push({ rule: 'dialog.focusNotMoved', severity: 'major', selector: '[role=dialog]', message: 'Opening the dialog leaves focus on the page behind it, so a keyboard user has to tab through everything to reach the thing that just opened.' });
  }

  await page.keyboard.press('Escape');
  await page.waitForTimeout(400);
  if (await dlg.count()) {
    f.push({ rule: 'dialog.escapeDoesNotClose', severity: 'major', selector: '[role=dialog]', message: 'Escape does not dismiss the dialog, which is the reflex every user has.' });
  } else if (openerBox) {
    const restored = await page.evaluate(() => document.activeElement?.tagName);
    if (restored === 'BODY') {
      f.push({ rule: 'dialog.focusNotRestored', severity: 'minor', selector: 'body', message: 'Closing the dialog drops focus back to <body> instead of the control that opened it, so the next Tab starts over from the top of the page.' });
    }
  }
  return f;
}

// ── 5. searching and filtering a list ──
export async function flowFilter(page, ctx) {
  if (ctx.route.kind !== 'list') return [];
  const f = [];
  const box = page.locator('input[type="search"]:visible, input[placeholder*="earch" i]:visible, input[placeholder*="검색"]:visible').first();
  if (!(await box.count())) return [];

  const rowsBefore = await page.locator('tbody tr').count();
  const urlBefore = page.url();
  await box.fill('zzzz-no-such-thing-zzzz');
  await page.waitForTimeout(900);

  const rowsAfter = await page.locator('tbody tr').count();
  const body = await page.evaluate(() => document.body.innerText);

  if (rowsAfter === 0 && !/no (match|result)|nothing|found|일치|결과가/i.test(body)) {
    f.push({
      rule: 'flow.noResultsIndistinguishable', severity: 'major', selector: 'tbody',
      message: 'A search that matches nothing empties the table without saying so, so "no results for this filter" looks exactly like "you have nothing yet" — and the way out (clear the filter) is not offered.',
    });
  }
  if (rowsAfter !== rowsBefore && page.url() === urlBefore) {
    f.push({
      rule: 'flow.filterNotInUrl', severity: 'major', selector: 'input[type=search]',
      message: `Filtering changed the list from ${rowsBefore} rows to ${rowsAfter} without touching the URL: the view cannot be shared, bookmarked, or recovered with Back after clicking into a row.`,
    });
  }
  const count = (await page.locator('[data-result-count]').count()) > 0
    || /\b\d+\s*(results?|items?|rows?|total|건|개)\b/i.test(body)
    || /\b\d+\s*(of|\/)\s*\d+\b/.test(body);
  if (!count) {
    f.push({
      rule: 'flow.noResultCount', severity: 'minor', selector: 'body',
      message: 'The filtered list never states how many rows matched, so the user has to count them.',
    });
  }
  const clear = await page.locator('[data-clear-filter], button[aria-label*="clear" i], button[title*="clear" i]').count();
  if (!clear) {
    f.push({
      rule: 'flow.noClearFilter', severity: 'minor', selector: 'input[type=search]',
      message: 'There is no one-click way to clear the search: the user has to select the text and delete it.',
    });
  }
  await box.fill('');
  return f;
}

// ── 6. what the screen shows while it loads ──
export async function flowLoading(page, ctx, origin) {
  const f = [];
  await page.goto(origin + ctx.route.path, { waitUntil: 'commit' }).catch(() => {});
  await page.waitForTimeout(180);
  const shot = await page.evaluate(() => ({
    text: (document.body?.innerText || '').trim().slice(0, 120),
    skeleton: !!document.querySelector('[data-skeleton], .skeleton, .animate-pulse, [aria-busy="true"]'),
    spinner: !!document.querySelector('.spinner, .animate-spin'),
  }));
  if (!shot.skeleton && !shot.spinner && shot.text.length < 20) {
    f.push({
      rule: 'flow.blankWhileLoading', severity: 'major', selector: 'body',
      message: 'For the first moments after navigation the screen is blank — no skeleton, no spinner, no heading — which reads as a broken page on a slow connection.',
    });
  } else if (shot.spinner && !shot.skeleton) {
    f.push({
      rule: 'flow.spinnerNotSkeleton', severity: 'polish', selector: 'body',
      message: 'Loading is a bare spinner rather than a skeleton of the layout, so the page visibly jumps when the data lands.',
    });
  }
  await page.waitForTimeout(800);
  const busy = await page.evaluate(() => document.querySelector('[aria-busy]')?.getAttribute('aria-busy'));
  if (busy === null) {
    f.push({
      rule: 'flow.loadingNotAnnounced', severity: 'minor', selector: 'body',
      message: 'Nothing sets aria-busy or a live region while data loads, so a screen-reader user hears silence and assumes the screen is finished.',
    });
  }
  return f;
}

// ── 7. copy affordances ──
export async function flowCopy(page) {
  const f = [];
  const btn = page.locator('button[aria-label*="opy" i], button[title*="opy" i], [data-copy]').first();
  if (!(await btn.count())) return [];
  const before = await page.evaluate(() => document.body.innerText);
  const labelBefore = (await btn.innerText().catch(() => '')) + (await btn.getAttribute('title').catch(() => '') ?? '');
  await btn.click({ timeout: 2000 }).catch(() => {});
  await page.waitForTimeout(600);
  const added = await newText(page, before);
  const labelAfter = (await btn.innerText().catch(() => '')) + (await btn.getAttribute('title').catch(() => '') ?? '');
  if (labelBefore === labelAfter && !added.some((t) => /copied|복사/i.test(t))) {
    f.push({
      rule: 'flow.copyNoFeedback', severity: 'minor', selector: '[data-copy]',
      message: 'Pressing copy gives no confirmation, so the user cannot tell it worked and presses it again.',
    });
  }
  return f;
}

// ── 8. navigating on a phone ──
export async function flowMobileNav(page, ctx) {
  if (ctx.viewport.width > 480) return [];
  // The forced password change has no way out by design.
  if (ctx.route.id === 'change-password') return [];
  const f = [];
  const toggle = page.locator('button[aria-label*="menu" i], button[aria-expanded], [data-sidebar-toggle]').first();
  const navVisible = await page.locator('nav a:visible, aside a:visible').count();
  // The signed-out and error screens carry no console chrome by design, and offer a link home
  // instead of a sidebar; that is a way out, so it does not count as being stranded.
  const wayHome = await page.locator('a[href="/"]:visible, a[href$="/login"]:visible').count();
  if (!(await toggle.count()) && navVisible === 0 && wayHome === 0) {
    f.push({
      rule: 'mobile.noNavAccess', severity: 'blocker', selector: 'body',
      message: 'On a phone there is neither a visible navigation nor a menu button: from this screen the user can only go back.',
    });
    return f;
  }
  if (await toggle.count()) {
    if (!(await toggle.getAttribute('aria-expanded'))) {
      f.push({ rule: 'mobile.menuNoExpandedState', severity: 'minor', selector: 'button[aria-label*=menu]', message: 'The menu button never reports aria-expanded, so its open or closed state is invisible to assistive technology.' });
    }
    await toggle.click({ timeout: 2000 }).catch(() => {});
    await page.waitForTimeout(400);
    const link = page.locator('nav a:visible, aside a:visible').first();
    if (await link.count()) {
      await link.click({ timeout: 2000 }).catch(() => {});
      await page.waitForTimeout(600);
      const stillOpen = await page.locator('nav a:visible, aside a:visible').count();
      if (stillOpen > 3) {
        f.push({ rule: 'mobile.menuStaysOpenAfterNavigate', severity: 'minor', selector: 'nav', message: 'The navigation drawer stays open after choosing a destination, covering the screen the user just asked for.' });
      }
    }
  }
  return f;
}

// ── 9. switching language ──
export async function flowLanguage(page, ctx, origin) {
  if (ctx.route.kind !== 'list') return [];
  const f = [];
  const toggle = page.locator('button, select').filter({ hasText: /english|한국어|EN|KO/i }).first();
  if (!(await toggle.count())) {
    return [{ rule: 'i18n.noLanguageControl', severity: 'major', selector: 'header', message: 'The language cannot be changed from this screen — the control only exists elsewhere, so a user who lands here from a link is stuck in the wrong language.' }];
  }
  const before = page.url();
  await toggle.click({ timeout: 2000 }).catch(() => {});
  await page.waitForTimeout(700);
  if (page.url() !== before) {
    f.push({ rule: 'i18n.languageSwitchLosesPlace', severity: 'major', selector: 'header', message: `Switching language navigated away from ${new URL(before).pathname} to ${new URL(page.url()).pathname} instead of translating the screen in place.` });
  }
  return f;
}

export const FLOWS = [
  ['flow.loading', flowLoading],
  ['flow.emptySubmit', flowEmptySubmit],
  ['flow.invalidInput', flowInvalidInput],
  ['flow.filter', flowFilter],
  ['flow.dialog', flowDialog],
  ['flow.destructive', flowDestructive],
  ['flow.copy', flowCopy],
  ['flow.mobileNav', flowMobileNav],
  ['flow.language', flowLanguage],
];
