import { useEffect, useMemo, useState, useRef } from 'react';
import { Select } from '@/components/Select';
import { useSearchParams } from 'react-router-dom';
import { PageHeader } from '@/components/PageHeader';
import { HelpTip } from '@/components/HelpTip';
import { EmptyState, NoResults, TableSkeleton, ErrorState } from '@/components/EmptyState';
import { Timestamp } from '@/components/Timestamp';
import { CopyableId } from '@/components/CopyButton';
import { useTranslation } from 'react-i18next';
import i18n from '@/i18n';
import { useAuditLogs, type AuditFilter } from '@/api/hooks/useAudit';
import { formatDateTime } from '@/lib/format';
import { CaretDown, ClipboardText } from '@/components/icons';

// The audit log (GET /audit-logs), with filters and pagination.

interface AuditRow {
  id: string;
  actor_id: string;
  actor_name?: string;
  actor_email?: string;
  action: string;
  target: string;
  target_name?: string;
  result?: string;
  detail?: Record<string, unknown>;
  at: string;
}

/**
 * Audit labels.
 *
 * Machine codes (actions, detail keys, target prefixes) are looked up in the translation bundle by
 * code, with the code itself as the fallback, so a code the UI has not been taught yet still renders
 * legibly. Dots in an action code are replaced with underscores, because i18next treats a dot as a
 * key separator.
 */
export const actionLabel = (a: string) =>
  i18n.t(`admin.audit.actionLabel.${a.replace(/\./g, '_')}`, { defaultValue: a });

const detailKeyLabel = (k: string) => i18n.t(`admin.audit.detailKey.${k}`, { defaultValue: k });

/** Audit result codes to a label and a colour. Only error, fail, denied, and reject read as failure. */
// Every result the backend writes gets a translated label; an unknown code still renders, but as
// itself in a neutral tone rather than leaking raw English into a Korean screen.
const RESULT_TONES: Record<string, { key: string; tone: string }> = {
  ok: { key: 'admin.audit.resultOk', tone: 'bg-free-soft text-free' },
  ready: { key: 'admin.audit.resultReady', tone: 'bg-free-soft text-free' },
  cordoned: { key: 'admin.audit.resultCordoned', tone: 'bg-free-soft text-free' },
  pending: { key: 'admin.audit.resultPending', tone: 'bg-warn-soft text-warn' },
  accepted: { key: 'admin.audit.resultAccepted', tone: 'bg-primary-soft text-primary' },
  succeeded: { key: 'admin.audit.resultSucceeded', tone: 'bg-free-soft text-free' },
  approved: { key: 'admin.audit.resultApproved', tone: 'bg-free-soft text-free' },
  rejected: { key: 'admin.audit.resultRejected', tone: 'bg-danger-soft text-danger' },
  failed: { key: 'admin.audit.resultFailed', tone: 'bg-danger-soft text-danger' },
  handoff_failed: { key: 'admin.audit.resultHandoffFailed', tone: 'bg-danger-soft text-danger' },
  escalated: { key: 'admin.audit.resultEscalated', tone: 'bg-warn-soft text-warn' },
};
export function resultMeta(r?: string): { label: string; tone: string } {
  if (!r) return { label: i18n.t('admin.audit.resultOk'), tone: 'bg-free-soft text-free' };
  const known = RESULT_TONES[r];
  if (known) return { label: i18n.t(known.key), tone: known.tone };
  if (/error|fail|denied|reject/i.test(r)) {
    return { label: i18n.t('admin.audit.resultFailed'), tone: 'bg-danger-soft text-danger' };
  }
  // An unrecognised code renders as itself, in a neutral tone.
  return { label: r, tone: 'bg-surface-2 text-muted' };
}

/** `names` is the id-to-name map the backend supplies; an id it resolves renders as "Name (id)". */
const detailValue = (v: unknown, names?: Record<string, string>): string => {
  if (v === null || v === undefined) return '-';
  if (Array.isArray(v)) return v.length ? v.map((x) => detailValue(x, names)).join(', ') : i18n.t('admin.audit.none');
  if (typeof v === 'string' && names && names[v]) return `${names[v]} (${v})`;
  return typeof v === 'object' ? JSON.stringify(v) : String(v);
};

/** detail.changes = { field: { from, to } } becomes a list of [field label, before, after]. */
function changeEntries(detail?: Record<string, unknown>): Array<[string, unknown, unknown]> {
  const c = detail?.changes as Record<string, { from?: unknown; to?: unknown }> | undefined;
  if (!c || typeof c !== 'object') return [];
  return Object.entries(c).map(([k, v]) => [detailKeyLabel(k), v?.from, v?.to]);
}
export const changesSummary = (detail?: Record<string, unknown>, names?: Record<string, string>) =>
  changeEntries(detail).map(([k, f, t]) => `${k}: ${detailValue(f, names)} → ${detailValue(t, names)}`).join(', ');

/**
 * The kind of thing an entry targeted, from the id prefix. Targets with no prefix - a node hostname,
 * say - fall back to the action's domain. The raw id appears only in the detail panel.
 */
function targetTypeLabel(target?: string, action?: string): string {
  if (!target) return '-';
  if (target.startsWith('GPU-')) return i18n.t('admin.audit.gpuDevice');
  const pfx = target.includes('_') ? target.slice(0, target.indexOf('_')) : '';
  const byPrefix = pfx && i18n.exists(`admin.audit.targetKind.${pfx}`)
    ? i18n.t(`admin.audit.targetKind.${pfx}`)
    : '';
  if (byPrefix) return byPrefix;
  const domain = (action ?? '').split('.')[0];
  return i18n.t(`admin.audit.actionDomain.${domain}`, { defaultValue: i18n.t('admin.audit.other') });
}

/**
 * The target column: kind, then the resolved name - whose wallet, session, or volume, or the name of
 * a cluster or organization. The backend's target_name wins, then detail.name, then the kind alone.
 */
export function targetDisplay(r: { target: string; action: string; target_name?: string; detail?: Record<string, unknown> }): string {
  const type = targetTypeLabel(r.target, r.action);
  const nm = r.target_name || (typeof r.detail?.name === 'string' ? r.detail?.name : '');
  return nm ? `${type} · ${nm}` : type;
}

/** The name for the activity feed: the resolved name when there is one, otherwise the kind. */
function targetPrimary(r: { target: string; action: string; target_name?: string; detail?: Record<string, unknown> }): string {
  const nm = r.target_name || (typeof r.detail?.name === 'string' ? r.detail?.name : '');
  return nm || targetTypeLabel(r.target, r.action);
}
const actorDisplay = (r: { actor_name?: string; actor_id: string }) =>
  r.actor_name || (r.actor_id?.startsWith('operator') ? 'Operator' : '-');

const PAGE_SIZE = 20;


// Every action the backend records, grouped by domain — the filter is an exact match, so a
// dropdown of the real vocabulary beats a free-text field nobody can guess.
const AUDIT_ACTIONS: Record<string, string[]> = {
  user: ['user.create', 'user.bulk_create', 'user.update', 'user.change_password', 'user.delete.soft', 'user.delete.hard', 'user.global_role.set'],
  org: ['org.create', 'org.update', 'org.delete', 'org.admin.add', 'org.admin.remove'],
  group: ['group.create', 'group.update', 'group.delete', 'membership.create', 'membership.update', 'membership.delete'],
  credit: ['credit.topup', 'credit.adjust', 'credit.transfer', 'credit.allocate', 'credit.set_monthly_grant', 'credit.bulk_allocate', 'credit.bulk_monthly_grant', 'credit.allocation_request', 'credit.allocation_approve', 'credit.allocation_reject', 'credit.allocation_escalate', 'credit.topup_request.approve', 'credit.topup_request.reject'],
  budget: ['budget.create', 'budget.update', 'budget.delete'],
  session: ['session.force_terminate', 'session.pause', 'pod.delete', 'queue.cancel', 'queue.priority.set'],
  infra: ['node.register', 'node.cordon', 'node.drain', 'node.set_pool', 'pool.create', 'pool.update', 'pool.delete', 'pool.grant', 'pool.revoke', 'gpu_device.set_mode', 'gpu_pool.set_targets', 'cluster.register', 'cluster.update', 'cluster.deregister', 'cluster.connection_test'],
  catalog: ['policy.create', 'policy.update', 'policy.delete', 'policy.request', 'policy.request.approve', 'policy.request.reject', 'image.create', 'image.update', 'image.delete', 'image.import', 'image.build.create', 'image.build.finish', 'session.create', 'session.start', 'session.stop', 'session.restart', 'session.terminate', 'offering.create', 'offering.update', 'offering.delete', 'preset.create', 'preset.update', 'preset.delete'],
  storage: ['storage.volume.delete', 'storage.quota.approve', 'storage.quota.reject', 'storage.snapshot.restore', 'storage.snapshot.delete'],
  boards: ['notice.create', 'notice.update', 'notice.delete', 'inquiry.create', 'inquiry.reply'],
  system: ['webhook.create', 'webhook.delete', 'audit.retention'],
};

// Quick ranges: most audit questions are "what just happened", not a calendar exercise.
const PERIOD_PRESETS: { key: string; hours: number }[] = [
  { key: '1h', hours: 1 }, { key: '24h', hours: 24 }, { key: '7d', hours: 24 * 7 }, { key: '30d', hours: 24 * 30 },
];

export function AdminAudit() {
  const { t } = useTranslation();
  // Filters in the URL: an audit query is quoted and shared, so a link reproduces the same rows.
  const [params, setParams] = useSearchParams();
  const q = (k: string) => params.get(k) ?? '';
  const setQ = (changes: Record<string, string>) => setParams((prev) => {
    const next = new URLSearchParams(prev);
    for (const [k, v] of Object.entries(changes)) { if (v) next.set(k, v); else next.delete(k); }
    next.delete('page');   // any change to the filter restarts paging
    return next;
  }, { replace: true });

  const actor = q('actor');
  const action = q('action');
  const target = q('target');
  const from = q('from');
  const to = q('to');
  const period = q('period');
  const page = Number(params.get('page') ?? '1') || 1;
  const setActor = (v: string) => setQ({ actor: v });
  const setAction = (v: string) => setQ({ action: v });
  const setTarget = (v: string) => setQ({ target: v });
  const setFrom = (v: string) => setQ({ from: v });
  const setTo = (v: string) => setQ({ to: v });
  const setPage = (n: number) => setParams((prev) => {
    const next = new URLSearchParams(prev);
    if (n > 1) next.set('page', String(n)); else next.delete('page');
    return next;
  }, { replace: true });
  const [detailRow, setDetailRow] = useState<AuditRow | null>(null);
  // Text filters: typing lands in LOCAL state immediately (a controlled input bound to the
  // debounced URL param swallowed keystrokes); only the URL/query update is debounced.
  const [actorText, setActorText] = useState(actor);
  const [targetText, setTargetText] = useState(target);
  useEffect(() => { setActorText(actor); }, [actor]);
  useEffect(() => { setTargetText(target); }, [target]);
  // Presets cover most questions; the date pickers appear only on demand (or when a custom
  // range is already in the URL).
  const [customPeriod, setCustomPeriod] = useState(!!from || !!to);

  const filter: AuditFilter = useMemo(
    () => ({
      actor_q: actor.trim() || undefined,
      action: action.trim() || undefined,
      target: target.trim() || undefined,
      // A preset is live: its window is recomputed when the query refires. A custom range is
      // date-only, and the end date is inclusive (through 23:59 of that day).
      'at[gte]': period
        ? new Date(Date.now() - (PERIOD_PRESETS.find((p) => p.key === period)?.hours ?? 24) * 3600_000).toISOString()
        : from ? new Date(`${from}T00:00:00`).toISOString() : undefined,
      'at[lt]': period
        ? undefined
        : to ? new Date(new Date(`${to}T00:00:00`).getTime() + 24 * 3600_000).toISOString() : undefined,
      page,
      size: PAGE_SIZE,
      sort: '-at',
    }),
    [actor, action, target, from, to, period, page],
  );

  const { data, isLoading, isError, error, isFetching, refetch } = useAuditLogs(filter);
  const rows = (data?.data ?? []) as AuditRow[];
  const names = ((data as { names?: Record<string, string> })?.names) ?? {};
  const pagination = data?.pagination ?? { page: 1, size: PAGE_SIZE, total: 0, total_pages: 1 };
  const totalPages = Math.max(1, pagination.total_pages ?? 1);

  // The setters drop the page parameter themselves.
  // Debounce free-text filters: each keystroke rewrites the URL and the react-query key, which
  // fired one request per character.
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();
  const applyFilter = (fn: () => void) => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(fn, 300);
  };


  // One expanded row at a time: the detail unfolds right under the entry (no bottom card).
  const renderDetail = (r: AuditRow) => (
        <div className="mx-2 mb-2 mt-1 rounded-card border border-border bg-surface-2/40 p-4">

          <div className="grid gap-2 text-sm">
            <dl className="grid grid-cols-[5rem_1fr] gap-x-3 gap-y-1.5">
              <dt className="text-muted">{t('admin.audit.action')}</dt>
              <dd>{actionLabel(r.action)} <span className="text-muted font-mono text-2xs">{r.action}</span></dd>
              <dt className="text-muted">{t('admin.audit.actor')}</dt>
              <dd className="flex items-center gap-1.5 min-w-0 flex-wrap">
                {r.actor_name ? <b>{r.actor_name}</b> : null}
                {r.actor_email
                  ? <span className="text-muted">{r.actor_email}</span>
                  : (!r.actor_name && (r.actor_id?.startsWith('operator') ? 'Operator' : null))}
                {r.actor_id ? <CopyableId value={r.actor_id} /> : <span className="text-muted">-</span>}
              </dd>
              <dt className="text-muted">{t('admin.audit.target')}</dt>
              <dd className="flex items-center gap-1 min-w-0">
                {targetDisplay(r)}
                {r.target ? <CopyableId value={r.target} /> : <span className="text-muted">-</span>}
              </dd>
              <dt className="text-muted">{t('admin.audit.result')}</dt>
              <dd>{(() => { const m = resultMeta(r.result); return m.label === r.result ? m.label : `${m.label} (${r.result ?? 'ok'})`; })()}</dd>
              <dt className="text-muted">{t('admin.audit.time')}</dt>
              <dd>{formatDateTime(r.at)}</dd>
            </dl>
            {changeEntries(r.detail).length > 0 && (
              <div>
                <div className="text-xs font-semibold text-muted mb-1">{t('admin.audit.changes')}</div>
                <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1 bg-surface-2 rounded-card p-3">
                  {changeEntries(r.detail).map(([k, f, t]) => (
                    <div key={k} className="contents">
                      <dt className="text-muted">{k}</dt>
                      <dd className="break-all"><span className="text-muted">{detailValue(f, names)}</span> → <b>{detailValue(t, names)}</b></dd>
                    </div>
                  ))}
                </dl>
              </div>
            )}
            {r.detail && Object.keys(r.detail).filter((k) => k !== 'changes').length > 0 && (
              <div className="mt-1">
                <div className="text-xs font-semibold text-muted mb-1">{t('admin.audit.detailInfo')}</div>
                <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1 bg-surface-2 rounded-card p-3">
                  {Object.entries(r.detail).filter(([k]) => k !== 'changes').map(([k, v]) => (
                    <div key={k} className="contents">
                      <dt className="text-muted">{detailKeyLabel(k)}</dt>
                      <dd className="break-all">{detailValue(v, names)}</dd>
                    </div>
                  ))}
                </dl>
                <details className="mt-2">
                  <summary className="text-muted text-xs cursor-pointer">{t('admin.audit.rawJson')}</summary>
                  <pre className="bg-surface-2 rounded-card p-3 overflow-x-auto text-xs font-mono mt-1">
                    {JSON.stringify(r.detail, null, 2)}
                  </pre>
                </details>
              </div>
            )}
          </div>
        </div>
  );

  return (
    <div>
      <PageHeader
        title={t('admin.audit.title')}
        description={t('admin.audit.subtitle')}
      />

      {/* A form, so Enter applies the filter and Escape-free keyboard use works; the fields are
          already debounced, and the submit is a no-op beyond blurring. */}
      <form
        data-url-state
        className="gs-card mb-4 flex gap-3 flex-wrap items-end"
        onSubmit={(e) => e.preventDefault()}
        role="search"
        aria-label={t('admin.audit.filterLabel')}
      >
        <label className="text-xs font-semibold">
          {t('admin.audit.actor')}
          <input className="gs-input mt-1 w-44 block" value={actorText} onChange={(e) => { const v = e.target.value; setActorText(v); applyFilter(() => setActor(v)); }} placeholder={t('admin.audit.actorPlaceholder')} autoComplete="off" />
        </label>
        <label className="text-xs font-semibold">
          {t('admin.audit.action')}
          <Select className="gs-input mt-1 w-52 block" value={action} onChange={(e) => setAction(e.target.value)}>
            <option value="">{t('admin.audit.allActions')}</option>
            {Object.entries(AUDIT_ACTIONS).map(([group, actions]) => (
              <optgroup key={group} label={t(`admin.audit.actionGroup.${group}`)}>
                {actions.map((a) => (
                  <option key={a} value={a}>
                    {t(`admin.audit.actionLabel.${a.replace(/\./g, '_')}`, { defaultValue: a })}
                  </option>
                ))}
              </optgroup>
            ))}
          </Select>
        </label>
        <label className="text-xs font-semibold">
          <span className="inline-flex items-center gap-1">{t('admin.audit.target')}<HelpTip text={t('admin.audit.targetHint')} /></span>
          <input className="gs-input mt-1 w-52 block font-mono" value={targetText} onChange={(e) => { const v = e.target.value; setTargetText(v); applyFilter(() => setTarget(v)); }} placeholder={t('admin.audit.targetPlaceholder')} autoComplete="off" />
        </label>
        <div className="text-xs font-semibold">
          {t('admin.audit.period')}
          <div className="mt-1 flex gap-1">
            {PERIOD_PRESETS.map((pz) => (
              <button
                key={pz.key}
                type="button"
                className={`gs-btn gs-btn-sm ${period === pz.key ? 'gs-btn-primary' : ''}`}
                aria-pressed={period === pz.key}
                onClick={() => {
                  setQ({ period: period === pz.key ? '' : pz.key, from: '', to: '' });
                  setCustomPeriod(false);
                }}
              >
                {t(`admin.audit.preset.${pz.key}`)}
              </button>
            ))}
            <button
              type="button"
              className={`gs-btn gs-btn-sm ${customPeriod ? 'gs-btn-primary' : ''}`}
              aria-pressed={customPeriod}
              onClick={() => { if (!customPeriod) setQ({ period: '' }); setCustomPeriod((v) => !v); }}
            >
              {t('admin.audit.customRange')}
            </button>
          </div>
        </div>
        {customPeriod && (
          <label className="text-xs font-semibold">
            {t('common.fromDate')}
            <input type="date" className="gs-input mt-1 block" value={from} onChange={(e) => applyFilter(() => setFrom(e.target.value))} autoComplete="off" />
          </label>
        )}
        {customPeriod && (
          <label className="text-xs font-semibold">
            {t('common.toDate')}
            <input type="date" className="gs-input mt-1 block" value={to} onChange={(e) => applyFilter(() => setTo(e.target.value))} autoComplete="off" />
          </label>
        )}
        <button
          type="button"
          className="gs-btn"
          onClick={() => applyFilter(() => { setQ({ actor: '', action: '', target: '', from: '', to: '', period: '' }); setCustomPeriod(false); })}
          disabled={!actor && !action && !target && !from && !to && !period}
        >
          {t('table.clearFilters')}
        </button>
        {/* The filter is applied as it is typed, so this is the only live region the form needs. */}
        <p role="status" aria-live="polite" className="gs-sr-only">
          {t('admin.audit.summary', { total: pagination.total ?? 0, page: pagination.page ?? page, pages: totalPages })}
        </p>
      </form>

      <div className="gs-card">
        {isError ? (
          <ErrorState error={error} onRetry={() => refetch()} />
        ) : isLoading ? (
          <TableSkeleton rows={6} columns={3} />
        ) : rows.length === 0 ? (
          (actor || action || target || from || to || period)
            ? <NoResults query={actor || action || target} onClear={() => applyFilter(() => { setQ({ actor: '', action: '', target: '', from: '', to: '', period: '' }); setCustomPeriod(false); })} />
            : <EmptyState icon={<ClipboardText size={26} />} title={t('admin.audit.empty')} description={t('admin.audit.emptyDescription')} />
        ) : (
          <ul className="divide-y divide-border -mx-1">
            {rows.map((r) => {
              const m = resultMeta(r.result);
              const summary = changesSummary(r.detail, names);
              return (
                <li key={r.id}>
                  <button
                    type="button"
                    aria-expanded={detailRow?.id === r.id}
                    onClick={() => setDetailRow(detailRow?.id === r.id ? null : r)}
                    className="w-full text-left flex items-start gap-3 py-2.5 px-2 rounded-ctl hover:bg-surface-2 transition"
                  >
                    <span className={`gs-pill ${m.tone} shrink-0 mt-0.5 w-[4.5rem] justify-center text-center`} title={r.result ?? ''}>{m.label}</span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline gap-1.5 min-w-0">
                        <span className="font-semibold shrink-0">{actionLabel(r.action)}</span>
                        <span className="text-sm truncate">· {targetPrimary(r)}</span>
                        <span className="text-muted text-xs truncate shrink-0" title={t('admin.audit.openForDetail')}>· {actorDisplay(r)}</span>
                      </div>
                      {summary && (
                        <div className="text-muted text-xs truncate" title={summary}>{summary}</div>
                      )}
                    </div>
                    <Timestamp value={r.at} className="text-muted text-xs whitespace-nowrap shrink-0 mt-0.5" />
                    <CaretDown size={14} aria-hidden="true"
                      className={`shrink-0 mt-1 text-muted transition-transform ${detailRow?.id === r.id ? 'rotate-180' : ''}`} />
                  </button>
                  {detailRow?.id === r.id && renderDetail(r)}
                </li>
              );
            })}
          </ul>
        )}

        <div className="flex items-center justify-between mt-4 text-sm">
          <span className="text-muted">
            {t('admin.audit.summary', { total: pagination.total ?? 0, page: pagination.page ?? page, pages: totalPages })}
            {isFetching && <span className="ml-2">{t('admin.audit.refreshing')}</span>}
          </span>
          <div className="flex gap-2">
            <button type="button" className="gs-btn gs-btn-sm" onClick={() => setPage(Math.max(1, page - 1))} disabled={page <= 1}>
              {t('common.previous')}
            </button>
            <button type="button" className="gs-btn gs-btn-sm" onClick={() => setPage(Math.min(totalPages, page + 1))} disabled={page >= totalPages}>
              {t('common.next')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
