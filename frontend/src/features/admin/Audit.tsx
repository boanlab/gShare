import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { PageHeader } from '@/components/PageHeader';
import { EmptyState, NoResults, TableSkeleton } from '@/components/EmptyState';
import { Timestamp } from '@/components/Timestamp';
import { CopyableId } from '@/components/CopyButton';
import { useTranslation } from 'react-i18next';
import i18n from '@/i18n';
import { useAuditLogs, type AuditFilter } from '@/api/hooks/useAudit';
import { formatDateTime } from '@/lib/format';

// The audit log (GET /audit-logs), with filters and pagination.

interface AuditRow {
  id: string;
  actor_id: string;
  actor_name?: string;
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
const actionLabel = (a: string) =>
  i18n.t(`admin.audit.actionLabel.${a.replace(/\./g, '_')}`, { defaultValue: a });

const detailKeyLabel = (k: string) => i18n.t(`admin.audit.detailKey.${k}`, { defaultValue: k });

/** Audit result codes to a label and a colour. Only error, fail, denied, and reject read as failure. */
const RESULT_TONES: Record<string, { key: string; tone: string }> = {
  ok: { key: 'admin.audit.resultOk', tone: 'bg-free-soft text-free' },
  ready: { key: 'admin.audit.resultReady', tone: 'bg-free-soft text-free' },
  cordoned: { key: 'admin.audit.resultCordoned', tone: 'bg-free-soft text-free' },
  pending: { key: 'admin.audit.resultPending', tone: 'bg-warn-soft text-warn' },
};
function resultMeta(r?: string): { label: string; tone: string } {
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
  if (v === null || v === undefined) return '—';
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
const changesSummary = (detail?: Record<string, unknown>, names?: Record<string, string>) =>
  changeEntries(detail).map(([k, f, t]) => `${k}: ${detailValue(f, names)} → ${detailValue(t, names)}`).join(', ');

/**
 * The kind of thing an entry targeted, from the id prefix. Targets with no prefix — a node hostname,
 * say — fall back to the action's domain. The raw id appears only in the detail panel.
 */
function targetTypeLabel(target?: string, action?: string): string {
  if (!target) return '—';
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
 * The target column: kind, then the resolved name — whose wallet, session, or volume, or the name of
 * a cluster or organization. The backend's target_name wins, then detail.name, then the kind alone.
 */
function targetDisplay(r: { target: string; action: string; target_name?: string; detail?: Record<string, unknown> }): string {
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
  r.actor_name || (r.actor_id?.startsWith('operator') ? 'Operator' : '—');

const PAGE_SIZE = 20;

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

  const filter: AuditFilter = useMemo(
    () => ({
      actor_q: actor.trim() || undefined,
      action: action.trim() || undefined,
      target: target.trim() || undefined,
      'at[gte]': from ? new Date(from).toISOString() : undefined,
      'at[lt]': to ? new Date(to).toISOString() : undefined,
      page,
      size: PAGE_SIZE,
      sort: '-at',
    }),
    [actor, action, target, from, to, page],
  );

  const { data, isLoading, isFetching, refetch, dataUpdatedAt } = useAuditLogs(filter);
  const rows = (data?.data ?? []) as AuditRow[];
  const names = ((data as { names?: Record<string, string> })?.names) ?? {};
  const pagination = data?.pagination ?? { page: 1, size: PAGE_SIZE, total: 0, total_pages: 1 };
  const totalPages = Math.max(1, pagination.total_pages ?? 1);

  // The setters drop the page parameter themselves.
  const applyFilter = (fn: () => void) => fn();

  return (
    <div>
      <PageHeader
        title={t('admin.audit.title')}
        description={t('admin.audit.subtitle')}
        updatedAt={dataUpdatedAt || null}
        onRefresh={() => refetch()}
        isFetching={isFetching}
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
        <label className="text-[12px] font-semibold">
          {t('admin.audit.actor')}
          <input className="gs-input mt-1 w-44 block" value={actor} onChange={(e) => applyFilter(() => setActor(e.target.value))} placeholder={t('admin.audit.actorPlaceholder')} autoComplete="off" />
        </label>
        <label className="text-[12px] font-semibold">
          {t('admin.audit.action')}
          <input className="gs-input mt-1 w-44 block font-mono" value={action} onChange={(e) => applyFilter(() => setAction(e.target.value))} placeholder="credit.topup" autoComplete="off" />
        </label>
        <label className="text-[12px] font-semibold">
          {t('admin.audit.target')}
          <input className="gs-input mt-1 w-40 block font-mono" value={target} onChange={(e) => applyFilter(() => setTarget(e.target.value))} placeholder="wlt_… / ses_…" autoComplete="off" />
        </label>
        <label className="text-[12px] font-semibold">
          from
          <input type="datetime-local" className="gs-input mt-1 block" value={from} onChange={(e) => applyFilter(() => setFrom(e.target.value))} autoComplete="off" />
        </label>
        <label className="text-[12px] font-semibold">
          to
          <input type="datetime-local" className="gs-input mt-1 block" value={to} onChange={(e) => applyFilter(() => setTo(e.target.value))} autoComplete="off" />
        </label>
        <button
          type="button"
          className="gs-btn"
          onClick={() => applyFilter(() => { setActor(''); setAction(''); setTarget(''); setFrom(''); setTo(''); })}
          disabled={!actor && !action && !target && !from && !to}
        >
          {t('table.clearFilters')}
        </button>
        {/* The filter is applied as it is typed, so this is the only live region the form needs. */}
        <p role="status" aria-live="polite" className="gs-sr-only">
          {t('admin.audit.summary', { total: pagination.total ?? 0, page: pagination.page ?? page, pages: totalPages })}
        </p>
      </form>

      <div className="gs-card">
        {isLoading ? (
          <TableSkeleton rows={6} columns={3} />
        ) : rows.length === 0 ? (
          (actor || action || target || from || to)
            ? <NoResults query={actor || action || target} onClear={() => applyFilter(() => { setActor(''); setAction(''); setTarget(''); setFrom(''); setTo(''); })} />
            : <EmptyState icon="≣" title={t('admin.audit.empty')} description={t('admin.audit.emptyDescription')} />
        ) : (
          <ul className="divide-y divide-border -mx-1">
            {rows.map((r) => {
              const m = resultMeta(r.result);
              const summary = changesSummary(r.detail, names);
              return (
                <li key={r.id}>
                  <button
                    type="button"
                    onClick={() => setDetailRow(r)}
                    className="w-full text-left flex items-start gap-3 py-2.5 px-2 rounded-lg hover:bg-surface-2 transition"
                  >
                    <span className={`gs-pill ${m.tone} shrink-0 mt-0.5`} title={r.result ?? ''}>{m.label}</span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline gap-1.5 min-w-0">
                        <span className="font-semibold shrink-0">{actionLabel(r.action)}</span>
                        <span className="text-[13px] truncate">
                          · {targetPrimary(r)}
                          {r.target && (
                            <span
                              className="font-mono text-[11px] text-muted ml-1"
                              title={t('admin.audit.openForDetail')}
                            >
                              ({r.target})
                            </span>
                          )}
                        </span>
                      </div>
                      <div className="text-muted text-[12px] truncate" title={summary || undefined}>
                        {actorDisplay(r)}{summary ? ` — ${summary}` : ''}
                      </div>
                    </div>
                    <Timestamp value={r.at} className="text-muted text-[12px] whitespace-nowrap shrink-0 mt-0.5" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        <div className="flex items-center justify-between mt-4 text-[13px]">
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

      {detailRow && (
        <div className="gs-card mt-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-bold">{t('admin.audit.detailTitle')} — {actionLabel(detailRow.action)}</h2>
            <button type="button" className="gs-btn gs-btn-sm" onClick={() => setDetailRow(null)}>{t('common.close')}</button>
          </div>
          <div className="grid gap-2 text-[13px]">
            <dl className="grid grid-cols-[5rem_1fr] gap-x-3 gap-y-1.5">
              <dt className="text-muted">{t('admin.audit.action')}</dt>
              <dd>{actionLabel(detailRow.action)} <span className="text-muted font-mono text-[11px]">{detailRow.action}</span></dd>
              <dt className="text-muted">{t('admin.audit.actor')}</dt>
              <dd className="flex items-center gap-1 min-w-0">
                {detailRow.actor_name ? `${detailRow.actor_name} ` : ''}
                {detailRow.actor_id ? <CopyableId value={detailRow.actor_id} /> : <span className="text-muted">—</span>}
              </dd>
              <dt className="text-muted">{t('admin.audit.target')}</dt>
              <dd className="flex items-center gap-1 min-w-0">
                {targetDisplay(detailRow)}
                {detailRow.target ? <CopyableId value={detailRow.target} /> : <span className="text-muted">—</span>}
              </dd>
              <dt className="text-muted">{t('admin.audit.result')}</dt>
              <dd>{(() => { const m = resultMeta(detailRow.result); return m.label === detailRow.result ? m.label : `${m.label} (${detailRow.result ?? 'ok'})`; })()}</dd>
              <dt className="text-muted">{t('admin.audit.time')}</dt>
              <dd>{formatDateTime(detailRow.at)}</dd>
            </dl>
            {changeEntries(detailRow.detail).length > 0 && (
              <div>
                <div className="text-[12px] font-semibold text-muted mb-1">{t('admin.audit.changes')}</div>
                <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1 bg-surface-2 rounded-card p-3">
                  {changeEntries(detailRow.detail).map(([k, f, t]) => (
                    <div key={k} className="contents">
                      <dt className="text-muted">{k}</dt>
                      <dd className="break-all"><span className="text-muted">{detailValue(f, names)}</span> → <b>{detailValue(t, names)}</b></dd>
                    </div>
                  ))}
                </dl>
              </div>
            )}
            {detailRow.detail && Object.keys(detailRow.detail).filter((k) => k !== 'changes').length > 0 && (
              <div className="mt-1">
                <div className="text-[12px] font-semibold text-muted mb-1">{t('admin.audit.detailInfo')}</div>
                <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1 bg-surface-2 rounded-card p-3">
                  {Object.entries(detailRow.detail).filter(([k]) => k !== 'changes').map(([k, v]) => (
                    <div key={k} className="contents">
                      <dt className="text-muted">{detailKeyLabel(k)}</dt>
                      <dd className="break-all">{detailValue(v, names)}</dd>
                    </div>
                  ))}
                </dl>
                <details className="mt-2">
                  <summary className="text-muted text-[12px] cursor-pointer">{t('admin.audit.rawJson')}</summary>
                  <pre className="bg-surface-2 rounded-card p-3 overflow-x-auto text-[12px] font-mono mt-1">
                    {JSON.stringify(detailRow.detail, null, 2)}
                  </pre>
                </details>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
