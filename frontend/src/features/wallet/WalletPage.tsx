import { useEffect, useMemo, useState } from 'react';
import { Select } from '@/components/Select';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { Trans, useTranslation } from 'react-i18next';
import { useMyTopupRequests, useTopupRequest, useWallet, useWalletTransactions, type LedgerTxn , useSpendDaily } from '@/api/hooks/useWallet';
import { useSessions } from '@/api/hooks/useSessions';
import { useCreateAllocationRequest, useAllocationRequests, type AllocRequest } from '@/api/hooks/useAllocations';
import { useAuthStore } from '@/auth/authStore';
import { Table, TableToolbar, Pagination, sortAccessor, type Column } from '@/components/Table';
import { PageHeader } from '@/components/PageHeader';
import { EmptyState, NoResults, TableSkeleton } from '@/components/EmptyState';
import { Field, DisabledReason } from '@/components/Field';
import { Timestamp, formatPlainDateTime } from '@/components/Timestamp';
import { Figure } from '@/components/Figure';
import { StatusPill } from '@/components/StatusPill';
import { ReasonPopover } from '@/components/ReasonPopover';
import { Tabs } from '@/components/Tabs';
import { Dialog } from '@/components/Dialog';
import { QuotaRequestForm } from '@/features/account/QuotaRequestPage';
import { useTableState, sortRows } from '@/hooks/useTableState';
import { useUnsavedGuard, unsavedGuardProps } from '@/hooks/useUnsavedGuard';
import { useUiStore } from '@/store/uiStore';
import { formatDuration, formatCredit, reqStatusLabel, runwayLabel, scopeLabel, sessionBurnPerHour } from '@/lib/format';
import i18n from '@/i18n';
import { humanizeError, asApiError } from '@/lib/errors';

const PAGE_SIZE = 25;

/** Ledger columns. Built lazily so the headers pick up the active language. */
const ledgerColumns = (): Column<LedgerTxn>[] => [
  {
    key: 'created_at',
    header: i18n.t('wallet.colWhen'),
    sortBy: (r) => new Date(r.created_at).getTime(),
    // A rollup covers a span, so it prints one; a single transaction prints its moment.
    render: (r) => (
      (r.entry_count ?? 1) > 1 && r.period_start && r.period_end && !r.live ? (
        <span className="inline-flex flex-col leading-tight">
          <Timestamp value={r.period_start} />
          <span className="text-muted text-2xs">
            {i18n.t('wallet.untilTime', { time: formatPlainDateTime(r.period_end) })}
          </span>
        </span>
      ) : <Timestamp value={r.period_start ?? r.created_at} />
    ),
  },
  {
    key: 'type',
    header: i18n.t('wallet.colType'),
    sortBy: (r) => r.type,
    render: (r) => (
      <span className="inline-flex items-center gap-1.5">
        {i18n.t(`wallet.txn.${r.type}`, { defaultValue: r.type })}
        {(r.entry_count ?? 1) > 1 && r.period_start && (
          // The raw per-minute entry count means nothing to a person — show the SPAN instead.
          <span
            className="gs-tag gs-num"
            title={i18n.t('wallet.rollupHint', { count: r.entry_count })}
          >
            {formatDuration(r.period_start, r.live || !r.period_end ? Date.now() : new Date(r.period_end).getTime())}
          </span>
        )}
        {r.live && (
          <span className="gs-tag text-warn inline-flex items-center gap-1" title={i18n.t('wallet.billingNowHint')}>
            <span className="w-1.5 h-1.5 rounded-full bg-warn gs-dot-pulse" aria-hidden="true" />
            {i18n.t('wallet.billingNow')}
          </span>
        )}
        {r.settled && (
          <span className="gs-tag" title={i18n.t('wallet.settledHint')}>
            {i18n.t('wallet.settled')}
          </span>
        )}
      </span>
    ),
  },
  {
    key: 'amount',
    header: i18n.t('wallet.colAmount'),
    sortBy: (r) => Number(r.amount),
    align: 'right',
    render: (r) => {
      const n = Number(r.amount);
      // A hold only moves balance into reserve; +green would read as income, so it stays neutral.
      const sign = r.type === 'hold' ? 'text-muted' : n > 0 ? 'text-free' : n < 0 ? 'text-danger' : '';
      return <span className={`gs-num font-semibold ${sign}`}>{n > 0 ? '+' : ''}{r.amount} C</span>;
    },
  },
  {
    key: 'balance_after',
    header: i18n.t('wallet.colBalanceAfter'),
    sortBy: (r) => Number(r.balance_after),
    align: 'right',
    render: (r) => <span className="gs-num">{r.balance_after} C</span>,
  },
  {
    key: 'ref',
    header: i18n.t('wallet.colRef'),
    hideOnMobile: true,
    render: (r) => {
      if (!r.ref) return <span className="text-muted">-</span>;
      // Session refs read as the session's name and link to it; other refs keep the raw id.
      if (r.ref.startsWith('ses_')) {
        return (
          <Link to={`/sessions/${r.ref}`} className="text-primary font-semibold truncate max-w-[200px] inline-block align-bottom" title={r.ref}>
            {r.ref_name ?? r.ref}
          </Link>
        );
      }
      const REF_LABELS: Record<string, string> = {
        'top-up approval': i18n.t('wallet.refTopupApproval'),
        'welcome credit': i18n.t('wallet.refWelcome'),
      };
      return (
        <span className="text-xs text-muted truncate max-w-[200px] inline-block align-bottom" title={r.ref}>
          {REF_LABELS[r.ref_name ?? ''] ?? r.ref_name ?? r.ref}
        </span>
      );
    },
  },
];

/**
 * Daily spend as a pure-CSS column chart: one column per day, height = share of the busiest day.
 * The days arrive pre-aggregated from the server (consume + storage only); the parent owns the
 * range (last 30 days, a month, or a custom span), so this only draws.
 */
function SpendChart({ days, empty }: { days: { key: string; label: string; spend: number }[]; empty: string }) {
  const { t } = useTranslation();
  const n = days.length;
  const max = Math.max(...days.map((d) => d.spend), 0);
  const total = days.reduce((sum, d) => sum + d.spend, 0);
  if (!n || total <= 0) return <p className="text-muted text-sm py-2">{empty}</p>;
  const ticks = n >= 4 ? [0, Math.floor((n - 1) / 3), Math.floor(((n - 1) * 2) / 3), n - 1] : days.map((_, i) => i);
  return (
    <div>
      <div className="flex items-baseline justify-between mb-2">
        <p className="text-muted text-xs gs-num">
          {t('wallet.spendTotal', { amount: formatCredit(Math.round(total * 100) / 100) })}
        </p>
        <p className="text-muted text-2xs gs-num">
          {t('wallet.spendMax', { amount: formatCredit(Math.round(max * 100) / 100) })}
        </p>
      </div>
      {/* The browser's native title tooltip is flaky over fast-moving hovers, so each column
          carries its own CSS bubble (group-hover); edge columns pin the bubble to their side so
          it never clips outside the card. The half-height hairline gives the eye a scale. */}
      {/* The compact strip of the original design: ~32px columns packed with 2px gaps, centred in
          the card. Bars and axis labels live in the same wrapper, so the labels span exactly the
          strip and day k sits at the k/n mark; on narrow screens the strip fills the card and the
          columns shrink together. */}
      <div className="w-full">
      <div className="relative">
        <div className="absolute inset-x-0 top-1/2 border-t border-dashed border-border/60 pointer-events-none" aria-hidden="true" />
        <div className="flex items-end gap-[2px] h-24 border-b border-border" role="img" aria-label={t('wallet.spendTitle')}>
          {days.map((d, i) => (
            <div key={d.key} className="group relative flex-1 max-w-[64px] h-full flex items-end hover:bg-surface-2/60">
              <span
                aria-hidden="true"
                className={`pointer-events-none absolute bottom-full mb-1.5 hidden group-hover:block z-10
                            whitespace-nowrap rounded-ctl border border-border bg-surface px-2 py-1 text-2xs gs-num shadow-raised
                            ${i < n * 0.15 ? 'left-0' : i > n * 0.85 ? 'right-0' : 'left-1/2 -translate-x-1/2'}`}
              >
                {t('wallet.spendOnDay', { date: d.label, amount: formatCredit(Math.round(d.spend * 100) / 100) })}
              </span>
              {d.spend > 0 ? (
                <span
                  className="block w-full min-h-[2px] rounded-t-tag bg-primary group-hover:opacity-80"
                  style={{ height: `${(d.spend / max) * 100}%` }}
                />
              ) : (
                <span className="block w-full h-[2px] bg-border/60" />
              )}
            </div>
          ))}
        </div>
      </div>
      <div className="flex justify-between text-2xs text-muted gs-num mt-1" aria-hidden="true">
        {ticks.map((i) => <span key={days[i].key}>{days[i].label}</span>)}
      </div>
      </div>
    </div>
  );
}

// The wallet screen: the balance band, spend chart, plus the ledger table.
export function WalletPage() {
  const { t } = useTranslation();
  const { data: wallet, isLoading } = useWallet();
  const walletId = (wallet as { id?: string } | undefined)?.id;
  const { data: txns = [], isLoading: txnLoading } = useWalletTransactions(walletId);
  const { data: myAllocReqs = [] } = useAllocationRequests('mine');
  const { data: myTopups = [] } = useMyTopupRequests();
  // One list, both funding paths: allocation (from the group pool) and system top-up. Without the
  // top-ups a request approved as a MINT looked forever-pending here while the balance grew.
  type MyRequestRow = AllocRequest & { kind: 'allocation' | 'topup' };
  const myReqs = useMemo<MyRequestRow[]>(() => ([
    ...myAllocReqs.map((r) => ({ ...r, kind: 'allocation' as const })),
    ...myTopups.map((r) => ({
      id: r.id, created_at: r.created_at, amount: r.amount, status: r.status, level: 'user',
      decided_reason: r.decided_reason,
      kind: 'topup',
    } as unknown as MyRequestRow)),
  ]), [myAllocReqs, myTopups]);
  const { data: mySessions } = useSessions();
  const table = useTableState('', { sort: 'created_at', dir: 'desc' });
  const reqTable = useTableState('req', { sort: 'created_at', dir: 'desc' });

  // Search is reference-only; the type is a dropdown (전체/사용/스토리지/충전/...), built from the
  // types actually present so labels and data never drift.
  // Local state, NOT table.tab: the wallet page already owns ?tab= for its own tabs.
  const [typeFilter, setTypeFilter] = useState('');
  const txnTypes = useMemo(() => Array.from(new Set(txns.map((r) => r.type).filter(Boolean))).sort(), [txns]);
  const matched = useMemo(() => {
    const q = table.query.trim().toLowerCase();
    return txns.filter((r) =>
      (!typeFilter || r.type === typeFilter)
      && (!q || (r.ref ?? '').toLowerCase().includes(q) || (r.ref_name ?? '').toLowerCase().includes(q)));
  }, [txns, table.query, typeFilter]);

  const columns = useMemo(() => ledgerColumns(), []);
  const sorted = useMemo(() => {
    const col = columns.find((c) => c.key === table.sort);
    return sortRows(matched, col?.sortBy ?? null, table.dir);
  }, [matched, columns, table.sort, table.dir]);
  const pageRows = useMemo(
    () => sorted.slice((table.page - 1) * PAGE_SIZE, table.page * PAGE_SIZE),
    [sorted, table.page],
  );

  const available = wallet?.available != null ? Number(wallet.available) : null;
  const balance = wallet?.balance != null ? Number(wallet.balance) : null;
  // Low-balance threshold: a tenth of the allocated balance.
  const low = available != null && balance != null && balance > 0 && available / balance < 0.1;
  const reserved = wallet?.reserved != null ? Number(wallet.reserved) : null;
  // Burn rate: what MY running sessions cost per hour right now (rate snapshot x occupancy) -
  // the same arithmetic the session list bills by. Runway = available credits at that speed.
  const burn = sessionBurnPerHour(mySessions);
  const runwayHours = burn > 0 && available != null ? available / burn : null;

  // Three tabs, state in the URL (?tab=): the balance band stays above them; the long tables
  // move off the first screen. Overview keeps only summaries: the chart, what is billing NOW,
  // and the five most recent requests.
  const [params, setParams] = useSearchParams();
  const [quotaOpen, setQuotaOpen] = useState(false);
  const [creditOpen, setCreditOpen] = useState(false);
  const walletTab = (params.get('tab') as 'overview' | 'ledger' | 'requests') || 'overview';
  const setWalletTab = (v: string) => setParams((prev) => {
    const next = new URLSearchParams(prev);
    if (v === 'overview') next.delete('tab'); else next.set('tab', v);
    return next;
  }, { replace: true });
  const liveRows = useMemo(() => txns.filter((r) => r.live), [txns]);
  // 현재 과금 중 has charges only — the ledger's signed 금액 reads better here as an
  // unsigned 사용 금액 (refunds/hold releases never appear in this list).
  const liveColumns = useMemo(() => columns.filter((c) => c.key !== 'balance_after').map((c) => (c.key === 'amount' ? {
    ...c,
    header: t('wallet.colAmountUsed'),
    render: (r: (typeof liveRows)[number]) => (
      <span className="gs-num font-semibold">{String(r.amount).replace(/^-/, '')} C</span>
    ),
  } : c)), [columns, t]);
  // Live rows tick per second: the duration badges read Date.now() at render time, so a 1s
  // re-render keeps them moving; the amounts themselves truth-up on the 15s ledger refetch
  // (the worker mints charges per minute — sub-minute amounts do not exist server-side).
  const [, setNowTick] = useState(0);
  useEffect(() => {
    if (!liveRows.length) return;
    const id = window.setInterval(() => setNowTick((v) => v + 1), 1000);
    return () => window.clearInterval(id);
  }, [liveRows.length]);

  // Spend chart range: last 30 days, one calendar month, or a custom span.
  const lang = useTranslation().i18n.language;
  const [spendMode, setSpendMode] = useState<'30d' | 'month' | 'range'>('30d');
  const now0 = new Date();
  const [spendMonth, setSpendMonth] = useState(`${now0.getFullYear()}-${String(now0.getMonth() + 1).padStart(2, '0')}`);
  const [spendFrom, setSpendFrom] = useState('');
  const [spendTo, setSpendTo] = useState('');
  const isoDay = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const spendRange = useMemo(() => {
    const today = new Date(); today.setHours(0, 0, 0, 0);
    if (spendMode === 'month' && /^\d{4}-\d{2}$/.test(spendMonth)) {
      const [y, m] = spendMonth.split('-').map(Number);
      return { from: new Date(y, m - 1, 1), to: new Date(y, m, 0) };
    }
    if (spendMode === 'range' && spendFrom && spendTo) {
      const f = new Date(spendFrom); const u = new Date(spendTo);
      if (!Number.isNaN(f.getTime()) && !Number.isNaN(u.getTime()) && f <= u) return { from: f, to: u };
    }
    const f = new Date(today); f.setDate(f.getDate() - 29);
    return { from: f, to: today };
  }, [spendMode, spendMonth, spendFrom, spendTo]);
  const spendQ = useSpendDaily(walletId, isoDay(spendRange.from), isoDay(spendRange.to));
  const spendDays = useMemo(() => {
    const byDate = new Map((spendQ.data ?? []).map((r) => [r.date, r.amount]));
    const out: { key: string; label: string; spend: number }[] = [];
    const cur = new Date(spendRange.from);
    let guard = 0;
    while (cur <= spendRange.to && guard++ < 400) {
      const key = isoDay(cur);
      out.push({ key, label: cur.toLocaleDateString(lang, { month: 'short', day: 'numeric' }), spend: byDate.get(key) ?? 0 });
      cur.setDate(cur.getDate() + 1);
    }
    return out;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spendQ.data, spendRange, lang]);
  const recentReqs = useMemo(
    () => [...myReqs].sort((a, b) => new Date(b.created_at ?? 0).getTime() - new Date(a.created_at ?? 0).getTime()).slice(0, 5),
    [myReqs],
  );

  const reqCols: Column<AllocRequest>[] = [
    { key: 'created_at', header: t('wallet.colWhen'), sortBy: (r) => (r.created_at ? new Date(r.created_at).getTime() : 0), render: (r) => <Timestamp value={r.created_at} /> },
    { key: 'kind', header: t('wallet.colKind'), render: (r) => (
      <span className="gs-tag">{(r as { kind?: string }).kind === 'topup' ? t('wallet.kindTopup') : t('wallet.kindAllocation')}</span>
    ) },
    { key: 'level', header: t('wallet.colTarget'), render: (r) => (r.level === 'user' ? t('wallet.targetMine') : scopeLabel(r.level)) },
    { key: 'amount', header: t('wallet.colAmount'), align: 'right', sortBy: (r) => Number(r.amount), render: (r) => <span className="gs-num">{r.amount} C</span> },
    { key: 'status', header: t('common.status'), render: (r) => (
      <span className="inline-flex items-center gap-1.5">
        <StatusPill kind={r.status} label={reqStatusLabel(r.status)} />
        {r.status === 'rejected' && r.decided_reason && (
          // Long reasons break row rhythm inline — a hover/focus bubble keeps the row one line.
          <ReasonPopover reason={r.decided_reason} />
        )}
      </span>
    ) },
  ];

  return (
    <div>
      <PageHeader
        title={t('wallet.title')}
        description={t('wallet.subtitle')}
        actions={
          <>
            {/* Two different asks, side by side: credits (money) and compute quota (ceiling). */}
            <button type="button" className="gs-btn" onClick={() => setQuotaOpen(true)}>{t('quota.title')}</button>
            <button type="button" className="gs-btn gs-btn-primary" onClick={() => setCreditOpen(true)}>{t('wallet.requestCredits')}</button>
          </>
        }
      />

      <Dialog open={quotaOpen} wide title={t('quota.title')} onClose={() => setQuotaOpen(false)}>
        <QuotaRequestForm onDone={() => setQuotaOpen(false)} />
      </Dialog>
      <Dialog open={creditOpen} wide title={t('wallet.requestCredits')} onClose={() => setCreditOpen(false)}>
        <CreditRequestBody
          initialNeed={low ? String(Math.max(100, Math.round((balance ?? 0) * 0.5))) : undefined}
          onDone={() => setCreditOpen(false)}
        />
      </Dialog>

      {low && (
        <p role="status" className="gs-card mb-4 border-warn text-sm flex items-center justify-between gap-3 flex-wrap">
          <span>{t('wallet.lowBalance', { available: formatCredit(available ?? 0) })}</span>
          <button type="button" className="gs-btn gs-btn-sm gs-btn-primary" onClick={() => setCreditOpen(true)}>
            {t('wallet.requestCredits')}
          </button>
        </p>
      )}

      {/* One hairline band, not three equal cards: available is the number a user came for. */}
      <section className="gs-panel grid md:grid-cols-4" aria-label={t('wallet.title')}>
        <Figure label={t('wallet.balance')} value={isLoading ? '…' : formatCredit(balance)} unit="C" help={t('wallet.balanceHint')} />
        <Figure
          label={t('wallet.burnRate')}
          value={isLoading ? '…' : formatCredit(Math.round(burn * 10) / 10)}
          unit="C/h"
          foot={runwayHours != null ? t('wallet.runwayFor', { duration: runwayLabel(runwayHours) }) : t('wallet.noBurn')}
        />
        <Figure label={t('wallet.reserved')} value={isLoading ? '…' : formatCredit(reserved)} unit="C" help={t('wallet.reservedHint')} />
        <Figure label={t('wallet.available')} value={isLoading ? '…' : formatCredit(available)} unit="C" help={t('wallet.availableHint')} hero />
      </section>

      {/* The last 30 days of usage, from the ledger rows already on the page. */}
      <Tabs
        className="mt-5"
        ariaLabel={t('wallet.title')}
        active={walletTab}
        onChange={setWalletTab}
        items={[
          { key: 'overview', label: t('wallet.tabOverview') },
          { key: 'ledger', label: t('wallet.tabLedger') },
          { key: 'requests', label: t('wallet.tabRequests'), count: myReqs.filter((r) => r.status === 'pending').length },
        ]}
      />

      {walletTab === 'overview' && (
        <>
      <div className="gs-card mt-4">
        <div className="flex items-center justify-between gap-3 flex-wrap mb-3">
          <h2 className="gs-h2">{t('wallet.spendTitle')}</h2>
          {/* The range never hides data silently: month and custom spans come pre-aggregated by
              day from the server, so a long-lived wallet charts correctly beyond ledger page 1. */}
          <div className="flex items-center gap-2 text-xs">
            <Select
              className="gs-input text-xs py-1 w-auto"
              aria-label={t('wallet.spendTitle')}
              value={spendMode}
              onChange={(e) => setSpendMode(e.target.value as '30d' | 'month' | 'range')}
            >
              <option value="30d">{t('wallet.spendRange30')}</option>
              <option value="month">{t('wallet.spendRangeMonth')}</option>
              <option value="range">{t('wallet.spendRangeCustom')}</option>
            </Select>
            {spendMode === 'month' && (
              <input type="month" className="gs-input text-xs py-1 w-auto" value={spendMonth}
                onChange={(e) => setSpendMonth(e.target.value)} />
            )}
            {spendMode === 'range' && (
              <>
                <input type="date" className="gs-input text-xs py-1 w-auto" value={spendFrom}
                  onChange={(e) => setSpendFrom(e.target.value)} />
                <span className="text-muted" aria-hidden="true">–</span>
                <input type="date" className="gs-input text-xs py-1 w-auto" value={spendTo}
                  onChange={(e) => setSpendTo(e.target.value)} />
              </>
            )}
          </div>
        </div>
        {spendQ.isLoading ? <TableSkeleton rows={2} columns={1} /> : <SpendChart days={spendDays} empty={t('wallet.noSpend')} />}
      </div>

          {liveRows.length > 0 && (
            <div className="gs-card mt-4">
              <h2 className="font-bold mb-3">{t('wallet.billingNowList')}</h2>
              <Table caption={t('wallet.billingNowList')} columns={liveColumns} rows={liveRows} rowKey={(r) => r.id} />
              <button type="button" className="text-primary text-xs font-semibold hover:underline mt-3" onClick={() => setWalletTab('ledger')}>
                {t('wallet.viewAllLedger')}
              </button>
            </div>
          )}

          {recentReqs.length > 0 && (
            <div className="gs-card mt-4">
              <h2 className="font-bold mb-3">{t('wallet.recentRequests')}</h2>
              <Table caption={t('wallet.recentRequests')} columns={reqCols} rows={recentReqs} rowKey={(r) => r.id} />
              <button type="button" className="text-primary text-xs font-semibold hover:underline mt-3" onClick={() => setWalletTab('requests')}>
                {t('wallet.viewAllRequests')}
              </button>
            </div>
          )}
        </>
      )}

      {walletTab === 'requests' && (
        <div className="gs-card mt-4">
          <h2 className="font-bold mb-3">{t('wallet.myRequests')}</h2>
          <Table
            caption={t('wallet.myRequests')}
            columns={reqCols}
            rows={sortRows(myReqs, sortAccessor(reqCols, reqTable.sort), reqTable.dir)}
            rowKey={(r) => r.id}
            empty={t('wallet.noRequests')}
            sort={reqTable.sort}
            dir={reqTable.dir}
            onSort={reqTable.toggleSort}
          />
        </div>
      )}

      {walletTab === 'ledger' && (
      <div className="gs-card mt-4">
        <h2 className="font-bold mb-3">{t('wallet.ledger')}</h2>
        <TableToolbar
          query={table.query}
          onQueryChange={table.setQuery}
          placeholder={t('wallet.searchPlaceholder')}
          total={txns.length}
          shown={matched.length}
        >
          <label className="gs-sr-only" htmlFor="gs-txn-type">{t('wallet.filterAllTypes')}</label>
          <Select
            id="gs-txn-type"
            className="gs-input w-auto"
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
          >
            <option value="">{t('wallet.filterAllTypes')}</option>
            {txnTypes.map((ty) => (
              <option key={ty} value={ty}>{t(`wallet.txn.${ty}`, { defaultValue: ty })}</option>
            ))}
          </Select>
        </TableToolbar>
        {txnLoading ? (
          <TableSkeleton rows={5} columns={5} />
        ) : sorted.length === 0 ? (
          table.isFiltered
            ? <NoResults query={table.query} onClear={table.clear} />
            : <EmptyState icon="≡" title={t('wallet.noTransactions')} description={t('wallet.ledgerHint')} />
        ) : (
          <>
            <Table
              caption={t('wallet.ledger')}
              columns={columns}
              rows={pageRows}
              rowKey={(r) => r.id}
              sort={table.sort}
              dir={table.dir}
              onSort={table.toggleSort}
            />
            <Pagination page={table.page} pageSize={PAGE_SIZE} total={sorted.length} onPage={table.setPage} />
          </>
        )}
      </div>
      )}
    </div>
  );
}

// The credit request form: asks the active group's administrators for an allocation. Requests
// escalate up the hierarchy. No payment is involved; the request simply waits for approval.
export function CreditRequestBody({ onDone, initialNeed, initialTab }: {
  onDone?: () => void; initialNeed?: string; initialTab?: 'dept' | 'system';
}) {
  const { t } = useTranslation();
  const pushToast = useUiStore((s) => s.pushToast);
  const activeProjectId = useAuthStore((s) => s.activeProjectId);
  const membership = useAuthStore((s) => s.memberships.find((m) => m.group_id === activeProjectId));
  const projectName = membership?.project_name;
  // No live admin in the group means nobody could ever approve the request — steer to the
  // system top-up path instead of letting the request hang pending forever.
  const hasApprover = !!activeProjectId && membership?.has_group_admin !== false;
  const createReq = useCreateAllocationRequest();

  type ReqTab = 'dept' | 'system';
  const wanted = initialTab || (hasApprover ? 'dept' : 'system');
  const [reqTabState, setReqTab] = useState<ReqTab>(wanted === 'dept' && !hasApprover ? 'system' : wanted);
  const reqTab: ReqTab = reqTabState === 'dept' && !hasApprover ? 'system' : reqTabState;

  const [amount, setAmount] = useState(initialNeed ?? '');
  const [note, setNote] = useState('');
  const noDept = !activeProjectId;
  const amountNum = Number(amount);
  const amountError = amount.trim() && (!Number.isFinite(amountNum) || amountNum <= 0) ? t('wallet.amountMustBePositive') : null;
  const valid = !!amount.trim() && amountNum > 0 && !!activeProjectId && hasApprover && !!note.trim();
  // Naming what is still missing turns a dead button into one obvious next step.
  const blockers = [
    noDept && t('wallet.noActiveGroupShort'),
    (!amount.trim() || amountNum <= 0) && t('wallet.amountLabel'),
    !note.trim() && t('common.reason'),
  ].filter(Boolean) as string[];

  useUnsavedGuard((!!amount.trim() || !!note.trim()) && !createReq.isPending);

  function submit() {
    if (!valid) return;
    createReq.mutate(
      { level: 'user', group_id: activeProjectId!, amount: amount.trim(), note: note.trim() },
      {
        onSuccess: () => {
          pushToast('success', t('wallet.sent'));
          onDone?.();
        },
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      },
    );
  }

  return (
    <>
      {!hasApprover && (
        <p className="text-warn text-xs mb-2">{t('wallet.deptDisabledNotice')}</p>
      )}
      <Tabs
        ariaLabel={t('wallet.requestCredits')}
        items={[
          { key: 'dept', label: t('wallet.tabDeptRequest'), disabled: !hasApprover, disabledReason: t('wallet.noGroupAdmin') },
          { key: 'system', label: t('wallet.tabSystemRequest') },
        ]}
        active={reqTab}
        onChange={(v) => setReqTab(v as ReqTab)}
      />

      {reqTab === 'system' ? (
        <TopupRequestCard onDone={onDone} />
      ) : (
      <form className="gs-card space-y-3" {...unsavedGuardProps} onSubmit={(e) => { e.preventDefault(); submit(); }}>
        {noDept ? (
          <p role="alert" className="text-danger text-sm">{t('wallet.noActiveGroup')}</p>
        ) : !hasApprover ? (
          <p role="alert" className="text-warn text-sm">{t('wallet.noGroupAdmin')}</p>
        ) : (
          <p className="text-muted text-xs"><Trans i18nKey="wallet.requestGoesTo" values={{ group: projectName ?? activeProjectId }} components={{ 1: <b /> }} /></p>
        )}
        <Field label={t('wallet.amountLabel')} required error={amountError} hint={t('wallet.amountHint')}>
          {(ids) => (
            <input
       {...ids}
       type="number"
       inputMode="numeric"
       min={1}
       max={1000000}
       step="any"
       className="gs-input w-full"
       value={amount}
       onChange={(e) => setAmount(e.target.value)}
       placeholder={t('wallet.amountPlaceholder')}
       autoFocus autoComplete="off" onBlur={(e) => setAmount(String(Math.min(1000000, Math.max(1, Number(e.target.value) || 1))))} />
          )}
        </Field>
        <Field label={t('common.reason')} required hint={t('wallet.reasonHint')}>
          {(ids) => (
            <input
              {...ids}
              className="gs-input w-full"
              value={note}
              maxLength={280}
              onChange={(e) => setNote(e.target.value)}
              placeholder={t('wallet.reasonPlaceholder')} autoComplete="off" />
          )}
        </Field>
        <div className="flex items-center justify-end gap-3 flex-wrap">
          <DisabledReason reasons={valid ? [] : blockers} />
          <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={createReq.isPending || !valid}>
            {createReq.isPending ? t('wallet.sending') : t('wallet.send')}
          </button>
        </div>
      </form>
      )}
    </>
  );
}

// Page wrapper (/wallet/request): the same body behind a header; ?need seeds the amount and
// ?type the tab, so the wizard's out-of-credits deep link keeps working.
export function CreditRequestPage() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  return (
    <div className="w-full max-w-2xl">
      <PageHeader
        title={t('wallet.requestCredits')}
        crumbs={[{ label: t('wallet.title'), to: '/wallet' }, { label: t('wallet.requestCredits') }]}
      />
      <CreditRequestBody
        initialNeed={params.get('need') ?? undefined}
        initialTab={(params.get('type') as 'dept' | 'system') ?? undefined}
        onDone={() => navigate('/wallet')}
      />
    </div>
  );
}

/** Fallback funding path: ask the SYSTEM administrators to mint a top-up (approval required).
 * The form above asks the group's admins to allocate from the group pool - different approvers,
 * different source of funds. */
function TopupRequestCard({ onDone }: { onDone?: () => void }) {
  const { t } = useTranslation();
  const pushToast = useUiStore((s) => s.pushToast);
  const topup = useTopupRequest();
  const [amount, setAmount] = useState('');
  const [note, setNote] = useState('');
  const n = Number(amount);
  const valid = !!amount.trim() && Number.isFinite(n) && n > 0 && !!note.trim();
  return (
    <form
      className="gs-card space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (!valid) return;
        topup.mutate({ amount: amount.trim(), note: note.trim() }, {
          onSuccess: () => { setAmount(''); setNote(''); pushToast('success', t('wallet.topupSent')); onDone?.(); },
          onError: (err) => pushToast('error', humanizeError(asApiError(err))),
        });
      }}
    >
      <h2 className="font-bold">{t('wallet.topupTitle')}</h2>
      <p className="text-muted text-xs">{t('wallet.topupNote')}</p>
      <div className="grid grid-cols-2 gap-3 max-[560px]:grid-cols-1">
        <Field label={t('wallet.amountLabel')} required>
          {(ids) => (
            <input {...ids} type="number" inputMode="numeric" min={1} max={1000000} step="any"
              className="gs-input w-full" value={amount} onChange={(e) => setAmount(e.target.value)} autoComplete="off" />
          )}
        </Field>
        <Field label={t('common.reason')} required>
          {(ids) => (
            <input {...ids} className="gs-input w-full" value={note} maxLength={280}
              onChange={(e) => setNote(e.target.value)} autoComplete="off" />
          )}
        </Field>
      </div>
      <div className="flex justify-end">
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={topup.isPending || !valid}>
          {topup.isPending ? t('wallet.sending') : t('wallet.topupSend')}
        </button>
      </div>
    </form>
  );
}
