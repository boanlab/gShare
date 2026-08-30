import { useState } from 'react';
import { Select } from '@/components/Select';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import i18n from '@/i18n';
import {
  useAllocationRequests, useApproveRequest, useRejectRequest,
  useAllocate, useAllocationScope, useSetMonthlyGrant, useTopupWallet,
  useBulkAllocate, useBulkMonthlyGrant,
  type AllocRequest, type ScopeWallet, type SystemTotal,
} from '@/api/hooks/useAllocations';
import { Table, sortAccessor, type Column } from '@/components/Table';
import { EmptyState, TableSkeleton, ErrorState } from '@/components/EmptyState';
import { CopyableId } from '@/components/CopyButton';
import { useTableState, sortRows } from '@/hooks/useTableState';
import { PageHeader } from '@/components/PageHeader';
import { Tabs } from '@/components/Tabs';
import { usePrompt } from '@/components/PromptDialog';
import { useUiStore } from '@/store/uiStore';
import { HelpTip } from '@/components/HelpTip';
import { useRefillSchedule, useSetRefillSchedule } from '@/api/hooks/useAllocations';
import { useAuthStore } from '@/auth/authStore';
import { humanizeError, asApiError } from '@/lib/errors';
import { SettlementReport } from './SettlementReport';
import { Timestamp } from '@/components/Timestamp';
import { useApproveTopupRequest, useRejectTopupRequest, useTopupRequests, useCreateTopupRequest } from '@/api/hooks/useBilling';
import { StatusPill } from '@/components/StatusPill';
import { ReasonPopover } from '@/components/ReasonPopover';
import { reqStatusLabel } from '@/lib/format';
import { Check } from '@/components/icons';

// Credit management: one "pool to children" table, shaped by the caller's role.
//  - super_admin: the pool is an organization (issuance and refills available), children are groups
//  - org_admin: the pool is their organization (allocating from its balance), children are groups
//  - group_admin: the pool is their group, children are its members
// Each child row allocates, reclaims, and sets a refill inline; incoming requests sit underneath.

const C = (v: string | number | undefined) => `${Number(v ?? 0).toLocaleString()} C`;

// Turn a refill-over-ceiling failure into something a person can act on.
function grantErrorMsg(e: unknown): string {
  const err = asApiError(e);
  if (typeof err.message === 'string' && err.message.includes('monthly grants exceed')) {
    return i18n.t('admin.credits.grantOverLimit');
  }
  return humanizeError(err);
}

export function AdminCreditAllocation() {
  const { t } = useTranslation();
  const globalRole = useAuthStore((s) => s.claims.global_role);
  const canMint = globalRole === 'super_admin';
  const { data: scope } = useAllocationScope();

  const allPools = scope?.pools ?? [];
  // The top pool for this role: an organization pool when there is one (managing groups), otherwise
  // the group pool (managing individuals).
  const orgPools = allPools.filter((p) => p.scope === 'org');
  const pools = orgPools.length ? orgPools : allPools.filter((p) => p.scope === 'group');

  // One long scroll of cards buried the request queue and the report; tabs give each concern its
  // own screen, with allocation (the daily task) first. Tab state lives in the URL.
  const [params, setParams] = useSearchParams();
  type CreditTab = 'alloc' | 'requests' | 'settlement';
  const tab = (params.get('tab') as CreditTab) || 'alloc';
  const setTab = (v: string) => setParams((prev) => {
    const next = new URLSearchParams(prev);
    if (v === 'alloc') next.delete('tab'); else next.set('tab', v);
    return next;
  }, { replace: true });

  // Pending funding requests, surfaced as a count chip on the tab (parity with quota requests).
  const pendingTopups = (useTopupRequests({ status: 'pending', ...(canMint ? { scope: 'all' as const } : {}) }).data?.data ?? []).length;

  return (
    <div>
      <PageHeader
        title={t('admin.credits.title')}
        description={t('admin.credits.subtitle')}
        crumbs={[{ label: t('admin.credits.breadcrumb') }]}
      />

      <Tabs
        ariaLabel={t('admin.credits.title')}
        items={[
          { key: 'alloc', label: t('admin.credits.tabAlloc') },
          { key: 'requests', label: t('admin.credits.tabRequests'), count: pendingTopups || undefined },
          ...(canMint ? [{ key: 'settlement', label: t('admin.credits.tabSettlement') }] : []),
        ]}
        active={tab}
        onChange={setTab}
      />

      {tab === 'alloc' && (
        <>
          {canMint && (
            <SystemTotalsCard system={scope?.system ?? null} orgPools={orgPools} childrenOf={(id) => scope?.children[id] ?? []} />
          )}
          {pools.length === 0 ? (
            <div className="gs-card text-muted text-sm">{t('admin.credits.noPool')}</div>
          ) : (
            pools.map((p) => (
              <PoolBlock key={p.wallet_id} pool={p} children={scope?.children[p.wallet_id] ?? []} />
            ))
          )}
        </>
      )}

      {tab === 'requests' && (
        <>
          {/* The group administrator's funding channel: escalation is gone — short pools are asked
              for directly, as a top-up request on the GROUP wallet, straight to the system tier. */}
          {!canMint && <GroupFundingCard pools={pools} />}
          <RequestsInbox canMint={canMint} />
          <RequestsHistory canMint={canMint} />
        </>
      )}

      {tab === 'settlement' && canMint && <SettlementReport />}
    </div>
  );
}

function GroupBulkActions({ pool, memberCount }: { pool: ScopeWallet; memberCount: number }) {
  // Start-of-term cohort actions on a group pool: fund every member at once, or set the whole
  // group's monthly refill with one ceiling check. Rendered only for group-scope pools.
  const { t } = useTranslation();
  const pushToast = useUiStore((s) => s.pushToast);
  const [amount, setAmount] = useState('');
  const [grant, setGrant] = useState('');
  const bulkAllocate = useBulkAllocate();
  const bulkGrant = useBulkMonthlyGrant();
  if (!pool.owner_id) return null;
  const groupId = pool.owner_id;
  return (
    <div className="flex flex-wrap items-end gap-4 mb-3 p-2 rounded-ctl bg-surface-2">
      <div>
        <label className="block text-2xs text-muted mb-1" htmlFor={`bulk-amt-${groupId}`}>
          {t('admin.credits.bulkAllocateLabel', { count: memberCount })}
        </label>
        <div className="flex gap-2">
          <input id={`bulk-amt-${groupId}`} type="number" min="1" className="gs-input w-28"
            value={amount} onChange={(e) => setAmount(e.target.value)} />
          <button type="button" className="gs-btn gs-btn-sm" disabled={!Number(amount) || bulkAllocate.isPending}
            onClick={() => bulkAllocate.mutate({ group_id: groupId, amount }, {
              onSuccess: () => { setAmount(''); pushToast('success', t('admin.credits.bulkAllocateDone', { count: memberCount })); },
              onError: (e) => pushToast('error', humanizeError(asApiError(e))),
            })}>
            {t('admin.credits.bulkAllocateGo')}
          </button>
        </div>
      </div>
      <div>
        <label className="block text-2xs text-muted mb-1" htmlFor={`bulk-grant-${groupId}`}>
          {t('admin.credits.bulkGrantLabel')}
        </label>
        <div className="flex gap-2">
          <input id={`bulk-grant-${groupId}`} type="number" min="0" className="gs-input w-28"
            value={grant} onChange={(e) => setGrant(e.target.value)} />
          <button type="button" className="gs-btn gs-btn-sm" disabled={grant === '' || bulkGrant.isPending}
            onClick={() => bulkGrant.mutate({ group_id: groupId, amount: grant }, {
              onSuccess: () => { setGrant(''); pushToast('success', t('admin.credits.bulkGrantDone', { count: memberCount })); },
              onError: (e) => pushToast('error', humanizeError(asApiError(e))),
            })}>
            {t('admin.credits.bulkGrantGo')}
          </button>
        </div>
      </div>
    </div>
  );
}

function PoolBlock({ pool, children }: { pool: ScopeWallet; children: ScopeWallet[] }) {
  const { t } = useTranslation();
  const childLabel = t(pool.scope === 'org' ? 'admin.credits.childGroup' : pool.scope === 'group' ? 'admin.credits.childUser' : 'admin.credits.childGeneric');
  const poolTable = useTableState(`pool-${pool.wallet_id}`, { sort: 'name', dir: 'asc' });
  // A course group can hold thousands of members; render incrementally so the screen stays
  // responsive (the group-wide actions above cover the whole cohort regardless).
  const [shown, setShown] = useState(50);
  const cols: Column<ScopeWallet>[] = [
    { key: 'name', header: childLabel, sortBy: (c) => c.name, render: (c) => <b>{c.name}</b> },
    { key: 'balance', header: t('admin.credits.colBalance'), sortBy: (c) => Number(c.balance ?? 0), align: 'right', render: (c) => <span className="tabular-nums">{C(c.balance)}</span> },
    { key: 'grant', header: t('admin.credits.colMonthlyRefill'), sortBy: (c) => Number(c.monthly_grant ?? 0), align: 'right', render: (c) => <span className="tabular-nums text-muted">{C(c.monthly_grant)}</span> },
    { key: 'act', header: t('admin.credits.colActions'), sortable: false, align: 'right', render: (c) => <ChildActions pool={pool} child={c} /> },
  ];
  return (
    <div className="gs-card mb-4">
      <div className="mb-3">
        <h2 className="font-bold">{pool.name} <span className="gs-tag">{t('admin.credits.poolBadge')}</span></h2>
        <div className="text-muted text-xs">{t('admin.credits.allocateFrom', { amount: C(pool.balance), children: childLabel })}</div>
      </div>
      {pool.scope === 'group' && children.length > 0 && (
        <GroupBulkActions pool={pool} memberCount={children.length} />
      )}
      <Table
        caption={childLabel}
        columns={cols}
        rows={sortRows(children, sortAccessor(cols, poolTable.sort), poolTable.dir).slice(0, shown)}
        rowKey={(c) => c.wallet_id}
        empty={t('admin.credits.emptyChildren', { children: childLabel })}
        sort={poolTable.sort}
        dir={poolTable.dir}
        onSort={poolTable.toggleSort}
      />
      {children.length > shown && (
        <button type="button" className="gs-btn gs-btn-sm mt-2" onClick={() => setShown((n) => n + 200)}>
          {t('admin.credits.showMore', { shown, total: children.length })}
        </button>
      )}
    </div>
  );
}

// Issuance management, super_admin only: the monthly total (automatic refill), the system balance
// (issuance), and each organization's top-ups and refills.
function SystemTotalsCard({ system, orgPools, childrenOf }: {
  system: SystemTotal | null;
  orgPools: ScopeWallet[];
  childrenOf: (walletId: string) => ScopeWallet[];
}) {
  const { t } = useTranslation();
  const setGrant = useSetMonthlyGrant();
  const topup = useTopupWallet();
  const pushToast = useUiStore((s) => s.pushToast);
  const [total, setTotal] = useState('');
  const [issue, setIssue] = useState('');
  const sched = useRefillSchedule().data;
  const setSched = useSetRefillSchedule();
  const [schedDay, setSchedDay] = useState('');
  const [schedHour, setSchedHour] = useState('');
  const inp = 'gs-input gs-input-sm w-24';

  const setTotalGrant = () => {
    if (!system) return;
    setGrant.mutate({ walletId: system.wallet_id, amount: total || '0' }, {
      onSuccess: () => { pushToast('success', t('admin.credits.monthlyTotalSet')); setTotal(''); },
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };
  const doIssue = () => {
    if (!system) return;
    topup.mutate({ walletId: system.wallet_id, amount: issue, note: 'system issuance' }, {
      onSuccess: () => { pushToast('success', t('admin.credits.issueDone')); setIssue(''); },
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };

  // Organization rows behave exactly like group rows, with the system wallet as the pool.
  const sysPool = system
    ? ({ wallet_id: system.wallet_id, balance: system.balance, scope: 'org', name: t('admin.credits.system') } as ScopeWallet)
    : null;
  const orgTable = useTableState('orgs', { sort: 'name', dir: 'asc' });
  const cols: Column<ScopeWallet>[] = [
    { key: 'name', header: t('admin.credits.colOrg'), render: (o) => <b>{o.name}</b> },
    { key: 'balance', header: t('admin.credits.colOrgBalance'), render: (o) => <span className="tabular-nums">{C(o.balance)}</span> },
    { key: 'dist', header: t('admin.credits.colDistributed'), render: (o) => <span className="tabular-nums text-muted">{C(childrenOf(o.wallet_id).reduce((a, c) => a + Number(c.balance || 0), 0))}</span> },
    { key: 'grant', header: t('admin.credits.colMonthlyRefill'), render: (o) => <span className="tabular-nums text-muted">{C(o.monthly_grant)}</span> },
    { key: 'act', header: t('admin.credits.colActions'), render: (o) => (sysPool ? <ChildActions pool={sysPool} child={o} /> : null) },
  ];

  return (
    <div className="gs-card mb-4">
      <h2 className="font-bold mb-1">{t('admin.credits.systemTitle')} <span className="gs-tag">{t('admin.credits.systemBadge')}</span></h2>
      <p className="text-muted text-xs mb-3">{t('admin.credits.systemNote')}</p>

      <div data-url-state className="flex flex-wrap items-end gap-4 mb-3 pb-3 border-b border-border">
        <label className="text-2xs text-muted">{t('admin.credits.monthlyTotal')}
          <div className="flex gap-1 mt-0.5">
            <input className={inp} type="number" inputMode="numeric" min={0} max={100000000} step="any" value={total} onChange={(e) => setTotal(e.target.value)} placeholder={system?.monthly_total ?? '0'} disabled={!system} autoComplete="off" onBlur={(e) => setTotal(String(Math.min(100000000, Math.max(0, Number(e.target.value) || 0))))} />
            <button type="button" className="gs-btn gs-btn-sm gs-btn-primary disabled:opacity-50" disabled={!system || setGrant.isPending} onClick={setTotalGrant}>{t('admin.credits.set')}</button>
          </div>
        </label>
        <label className="text-2xs text-muted"><span className="inline-flex items-center gap-1">{t('admin.credits.issue')}<HelpTip text={t('admin.credits.issueHelp')} /></span>
          <div className="flex gap-1 mt-0.5">
            <input className={inp} type="number" inputMode="numeric" min={0} max={100000000} step="any" value={issue} onChange={(e) => setIssue(e.target.value)} placeholder="0" disabled={!system} autoComplete="off" onBlur={(e) => setIssue(String(Math.min(100000000, Math.max(0, Number(e.target.value) || 0))))} />
            <button type="button" className="gs-btn gs-btn-sm disabled:opacity-50" disabled={!system || Number(issue) <= 0 || topup.isPending} onClick={doIssue}>{t('admin.credits.issue')}</button>
          </div>
        </label>
        <label className="text-2xs text-muted">
          {/* The next-run readout rides on the LABEL line: as a footer it pushed the controls up
              out of line with the neighbouring fields. */}
          <span className="inline-flex items-center gap-1 flex-wrap">
            {t('admin.credits.refillAt')}<HelpTip text={t('admin.credits.refillAtHelp')} />
            {sched?.next_at && (
              <span className="text-muted/80">
                · {t('admin.credits.nextRefill', { time: new Date(sched.next_at).toLocaleString(undefined, { hour12: false }) })}
              </span>
            )}
          </span>
          <div className="flex gap-1 mt-0.5 items-center">
            <Select className="gs-input gs-input-sm w-auto" value={schedDay || String(sched?.day ?? 1)} onChange={(e) => setSchedDay(e.target.value)} aria-label={t('admin.credits.refillDay')}>
              {Array.from({ length: 28 }, (_, i) => i + 1).map((d) => <option key={d} value={d}>{t('admin.credits.dayN', { day: d })}</option>)}
            </Select>
            <Select className="gs-input gs-input-sm w-auto" value={schedHour || String(sched?.hour ?? 0)} onChange={(e) => setSchedHour(e.target.value)} aria-label={t('admin.credits.refillHour')}>
              {Array.from({ length: 24 }, (_, i) => i).map((h) => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
            </Select>
            <button type="button" className="gs-btn gs-btn-sm gs-btn-primary disabled:opacity-50" disabled={setSched.isPending}
              onClick={() => setSched.mutate(
                { day: Number(schedDay || sched?.day || 1), hour: Number(schedHour || String(sched?.hour ?? 0)) },
                { onSuccess: () => pushToast('success', t('admin.credits.refillAtSet')),
                  onError: (e) => pushToast('error', humanizeError(asApiError(e))) },
              )}>{t('admin.credits.set')}</button>
          </div>
        </label>
        <div className="flex flex-nowrap gap-3">
          <Metric label={t('admin.credits.metricBalance')} value={C(system?.balance)} />
          <Metric label={t('admin.credits.metricMonthlyTotal')} value={C(system?.monthly_total)} />
          <Metric label={t('admin.credits.metricAssigned')} value={C(system?.org_grant_sum)} />
          <Metric label={t('admin.credits.metricRemaining')} value={C(system?.remaining)} />
        </div>
      </div>

      <Table
        caption={t('admin.credits.orgPools')}
        columns={cols}
        rows={sortRows(orgPools, sortAccessor(cols, orgTable.sort), orgTable.dir)}
        rowKey={(o) => o.wallet_id}
        empty={t('admin.credits.emptyOrgs')}
        sort={orgTable.sort}
        dir={orgTable.dir}
        onSort={orgTable.toggleSort}
      />
    </div>
  );
}

/** Pending top-up requests (system-minted credits): list, approve, reject with a reason.
 * The backing API existed with no console surface - notifications deep-linked here to nothing. */
interface TopupRow {
  id: string; wallet_id: string; amount: string; status: string;
  requester_id?: string | null; requester_name?: string | null;
  wallet_owner_type?: 'user' | 'group' | null; wallet_owner_name?: string | null;
  note?: string | null; decided_reason?: string | null; created_at: string;
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-surface-2 rounded-card px-3 py-2 shrink-0 flex items-baseline gap-2 whitespace-nowrap">
      <span className="text-muted text-xs">{label}</span>
      <span className="gs-num text-md font-bold">{value}</span>
    </div>
  );
}

// Inline actions on one child row (organization, group, or user): allocate, reclaim, set refill.
function ChildActions({ pool, child }: { pool: ScopeWallet; child: ScopeWallet }) {
  const { t } = useTranslation();
  const allocate = useAllocate();
  const setGrant = useSetMonthlyGrant();
  const pushToast = useUiStore((s) => s.pushToast);
  const [amt, setAmt] = useState('');
  const [grant, setGrantAmt] = useState('');
  const inp = 'gs-input gs-input-sm w-20';

  const move = (dir: 'down' | 'up') => {
    const [from_wallet_id, to_wallet_id] = dir === 'down'
      ? [pool.wallet_id, child.wallet_id]   // allocate: pool to child
      : [child.wallet_id, pool.wallet_id];  // reclaim: child to pool
    allocate.mutate({ from_wallet_id, to_wallet_id, amount: amt, reason: dir === 'down' ? 'allocate' : 'reclaim' }, {
      onSuccess: () => { pushToast('success', t(dir === 'down' ? 'admin.credits.allocated' : 'admin.credits.reclaimed')); setAmt(''); },
      onError: (e) => {
        const err = asApiError(e);
        pushToast('error', err.code === 'insufficient_credit' ? t('admin.credits.poolShort') : humanizeError(err));
      },
    });
  };
  const doGrant = () =>
    setGrant.mutate({ walletId: child.wallet_id, amount: grant || '0' }, {
      onSuccess: () => { pushToast('success', t('admin.credits.refillSet')); setGrantAmt(''); },
      onError: (e) => pushToast('error', grantErrorMsg(e)),
    });

  const amtOk = Number(amt) > 0;
  return (
    <div data-url-state className="flex items-center gap-3 flex-wrap">
      <div className="flex items-center gap-1">
        <label className="gs-sr-only" htmlFor={`alloc-${child.wallet_id}`}>{t('admin.credits.allocateAmountLabel', { name: child.name })}</label>
        <input
     id={`alloc-${child.wallet_id}`}
     className={inp}
     type="number"
     inputMode="numeric"
     min={0}
     max={10000000}
     step="any"
     value={amt}
     onChange={(e) => setAmt(e.target.value)}
     placeholder={t('admin.credits.amountPlaceholder')}
     title={t('admin.credits.allocateAmountLabel', { name: child.name })} autoComplete="off" onBlur={(e) => setAmt(String(Math.min(10000000, Math.max(0, Number(e.target.value) || 0))))} />
        <button type="button" className="gs-btn gs-btn-sm gs-btn-primary disabled:opacity-50" disabled={!amtOk || allocate.isPending} onClick={() => move('down')}>{t('admin.credits.allocate')}</button>
        <button type="button" className="gs-btn gs-btn-sm disabled:opacity-50" disabled={!amtOk || allocate.isPending} onClick={() => move('up')}>{t('admin.credits.reclaim')}</button>
      </div>
      <div className="flex items-center gap-1">
        <label className="text-2xs text-muted" htmlFor={`grant-${child.wallet_id}`}>{t('admin.credits.refillShort')}</label>
        <input
     id={`grant-${child.wallet_id}`}
     className={inp}
     type="number"
     inputMode="numeric"
     min={0}
     max={10000000}
     step="any"
     value={grant}
     onChange={(e) => setGrantAmt(e.target.value)}
     placeholder={String(child.monthly_grant ?? '0')} autoComplete="off" onBlur={(e) => setGrantAmt(String(Math.min(10000000, Math.max(0, Number(e.target.value) || 0))))} />
        <button type="button" className="gs-btn gs-btn-sm disabled:opacity-50" disabled={setGrant.isPending} onClick={doGrant}>{t('admin.credits.set')}</button>
      </div>
    </div>
  );
}

// One inbox for everything awaiting THIS approver. A group admin sees member allocation
// requests; a super admin additionally sees top-up requests (personal and group funding),
// distinguished by the type column instead of living in a second card.
type InboxRow =
  | { kind: 'alloc'; id: string; a: AllocRequest }
  | { kind: 'topup'; id: string; p: TopupRow };

function typeLabel(r: InboxRow | HistoryRow): string {
  if (r.kind === 'alloc') {
    const lvl = r.a.level;
    return i18n.t(`admin.credits.level${lvl.charAt(0).toUpperCase()}${lvl.slice(1)}`, { defaultValue: lvl });
  }
  return r.p.wallet_owner_type === 'group' ? i18n.t('admin.credits.typeTopupGroup') : i18n.t('admin.credits.typeTopupUser');
}
function targetLabel(r: InboxRow | HistoryRow): string | null {
  return r.kind === 'alloc' ? (r.a.group_name ?? null) : (r.p.wallet_owner_name ?? null);
}

function RequestsInbox({ canMint }: { canMint: boolean }) {
  const { t } = useTranslation();
  const pushToast = useUiStore((s) => s.pushToast);
  const promptDialog = usePrompt();
  const { data: incoming = [], isLoading, isError, error, refetch } = useAllocationRequests('incoming');
  const topupsQ = useTopupRequests({ status: 'pending', ...(canMint ? { scope: 'all' as const } : {}) });
  const approve = useApproveRequest();
  const reject = useRejectRequest();
  const tApprove = useApproveTopupRequest();
  const tReject = useRejectTopupRequest();

  const topups = canMint ? (((topupsQ.data as { data?: TopupRow[] } | undefined)?.data ?? []) as TopupRow[]) : [];
  const rows: InboxRow[] = [
    ...incoming.map((a) => ({ kind: 'alloc' as const, id: a.id, a })),
    ...topups.map((p) => ({ kind: 'topup' as const, id: p.id, p })),
  ].sort((x, y) => new Date((y.kind === 'alloc' ? y.a.created_at : y.p.created_at) ?? 0).getTime()
                 - new Date((x.kind === 'alloc' ? x.a.created_at : x.p.created_at) ?? 0).getTime());

  const onApprove = (r: InboxRow) => {
    if (r.kind === 'alloc') {
      approve.mutate({ id: r.id }, {
        onSuccess: () => pushToast('success', t('admin.credits.approved')),
        onError: (e) => {
          const err = asApiError(e);
          pushToast('error', err.code === 'insufficient_pool' ? t('admin.credits.poolShort') : humanizeError(err));
        },
      });
    } else {
      tApprove.mutate({ id: r.id }, {
        onSuccess: () => pushToast('success', t('admin.credits.topupApproved', { amount: r.p.amount })),
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      });
    }
  };
  const onReject = async (r: InboxRow) => {
    const reason = await promptDialog({ title: t('admin.credits.rejectPrompt'), required: true });
    if (!reason) return;
    if (r.kind === 'alloc') {
      reject.mutate({ id: r.id, body: { reason } }, {
        onSuccess: () => pushToast('info', t('admin.credits.rejected')),
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      });
    } else {
      tReject.mutate({ id: r.id, reason }, {
        onSuccess: () => pushToast('info', t('admin.credits.rejected')),
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      });
    }
  };

  const cols: Column<InboxRow>[] = [
    { key: 'type', header: t('admin.credits.colRequest'), sortable: false, render: (r) => <span className="gs-tag">{typeLabel(r)}</span> },
    { key: 'target', header: t('admin.credits.colTarget'), sortable: false, render: (r) => targetLabel(r) ? <span className="text-xs">{targetLabel(r)}</span> : <span className="text-muted text-xs">-</span> },
    { key: 'amount', header: t('common.amount'), sortable: false, align: 'right', render: (r) => C(r.kind === 'alloc' ? r.a.amount : r.p.amount) },
    { key: 'requester', header: t('admin.credits.colRequester'), sortable: false, render: (r) => (r.kind === 'alloc' ? r.a.requester_name ?? <CopyableId value={r.a.requester_id} /> : r.p.requester_name ?? r.p.requester_id ?? '-') },
    { key: 'note', header: t('common.reason'), sortable: false, render: (r) => { const n = r.kind === 'alloc' ? r.a.note : r.p.note; return n ? <span className="text-xs">{n}</span> : <span className="text-muted">-</span>; } },
    { key: 'when', header: t('admin.credits.colWhen'), sortable: false, render: (r) => <Timestamp value={r.kind === 'alloc' ? r.a.created_at : r.p.created_at} className="text-muted text-xs" /> },
    {
      key: 'actions', header: '', sortable: false, align: 'right',
      render: (r) => (
        <div className="flex gap-1.5 justify-end">
          <button type="button" className="gs-btn gs-btn-sm gs-btn-primary" disabled={approve.isPending || tApprove.isPending} onClick={() => onApprove(r)}>{t('common.approve')}</button>
          <button type="button" className="gs-btn gs-btn-sm gs-btn-danger" disabled={reject.isPending || tReject.isPending} onClick={() => onReject(r)}>{t('common.reject')}</button>
        </div>
      ),
    },
  ];

  return (
    <div className="gs-card">
      <h2 className="font-bold mb-3">{t('admin.credits.incoming')} <span className="text-muted text-xs font-normal">{t('admin.credits.incomingCount', { count: rows.length })}</span></h2>
      {isError ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading ? (
        <TableSkeleton rows={3} columns={6} />
      ) : rows.length === 0 ? (
        <EmptyState icon={<Check size={26} />} title={t('admin.credits.emptyRequests')} description={t('admin.credits.emptyRequestsHint')} />
      ) : (
        <Table caption={t('admin.credits.incoming')} columns={cols} rows={rows} rowKey={(r) => r.id} />
      )}
    </div>
  );
}

// What already got decided (and, for a group admin, the funding asks THEY sent): the missing
// paper trail under the inbox.
type HistoryRow = InboxRow;

function RequestsHistory({ canMint }: { canMint: boolean }) {
  const { t } = useTranslation();
  const { data: handled = [] } = useAllocationRequests('handled');
  const topupsQ = useTopupRequests(canMint ? { scope: 'all' } : {});
  const allTopups = ((topupsQ.data as { data?: TopupRow[] } | undefined)?.data ?? []) as TopupRow[];
  // Super admin: decided top-ups (pending ones sit in the inbox above). Group admin: the list is
  // already scoped to the requests THEY raised — show all statuses, pending included, so the
  // funding ask they just sent is visible with its state.
  const topups = canMint ? allTopups.filter((r) => r.status !== 'pending') : allTopups;
  const rows: HistoryRow[] = [
    ...handled.map((a) => ({ kind: 'alloc' as const, id: a.id, a })),
    ...topups.map((p) => ({ kind: 'topup' as const, id: p.id, p })),
  ].sort((x, y) => new Date((y.kind === 'alloc' ? y.a.created_at : y.p.created_at) ?? 0).getTime()
                 - new Date((x.kind === 'alloc' ? x.a.created_at : x.p.created_at) ?? 0).getTime());

  const cols: Column<HistoryRow>[] = [
    { key: 'type', header: t('admin.credits.colRequest'), sortable: false, render: (r) => <span className="gs-tag">{typeLabel(r)}</span> },
    { key: 'target', header: t('admin.credits.colTarget'), sortable: false, render: (r) => targetLabel(r) ? <span className="text-xs">{targetLabel(r)}</span> : <span className="text-muted text-xs">-</span> },
    { key: 'amount', header: t('common.amount'), sortable: false, align: 'right', render: (r) => C(r.kind === 'alloc' ? r.a.amount : r.p.amount) },
    { key: 'requester', header: t('admin.credits.colRequester'), sortable: false, render: (r) => (r.kind === 'alloc' ? r.a.requester_name ?? <CopyableId value={r.a.requester_id} /> : r.p.requester_name ?? r.p.requester_id ?? '-') },
    {
      key: 'status', header: t('common.status'), sortable: false,
      render: (r) => {
        const st = r.kind === 'alloc' ? r.a.status : r.p.status;
        const reason = r.kind === 'alloc' ? r.a.decided_reason : r.p.decided_reason;
        return (
          <span className="inline-flex items-center gap-1.5">
            <StatusPill kind={st} label={reqStatusLabel(st)} />
            {st === 'rejected' && reason && <ReasonPopover reason={reason} />}
          </span>
        );
      },
    },
    { key: 'when', header: t('admin.credits.colWhen'), sortable: false, render: (r) => <Timestamp value={r.kind === 'alloc' ? r.a.created_at : r.p.created_at} className="text-muted text-xs" /> },
  ];

  return (
    <div className="gs-card mt-4">
      <h2 className="font-bold mb-3">{t('admin.credits.requestHistory')} <span className="text-muted text-xs font-normal">{t('admin.credits.incomingCount', { count: rows.length })}</span></h2>
      {rows.length === 0 ? (
        <p className="text-muted text-sm py-2">{t('admin.credits.emptyHistory')}</p>
      ) : (
        <Table caption={t('admin.credits.requestHistory')} columns={cols} rows={rows} rowKey={(r) => r.id} />
      )}
    </div>
  );
}

// The group administrator's ask: fund the GROUP wallet from the system tier. Replaces escalation
// with a direct, visible request the super admin actually receives.
function GroupFundingCard({ pools }: { pools: ScopeWallet[] }) {
  const { t } = useTranslation();
  const pushToast = useUiStore((s) => s.pushToast);
  const create = useCreateTopupRequest();
  const groupPools = pools.filter((p) => p.scope === 'group');
  const [poolId, setPoolId] = useState('');
  const [amount, setAmount] = useState('');
  const [note, setNote] = useState('');
  const pool = groupPools.find((p) => p.wallet_id === poolId) ?? groupPools[0];
  if (!groupPools.length) return null;
  const ok = pool && Number(amount) > 0;
  const submit = () => {
    if (!ok) return;
    create.mutate({ wallet_id: pool.wallet_id, amount, note: note || undefined }, {
      onSuccess: () => { pushToast('success', t('admin.credits.askFundingSent')); setAmount(''); setNote(''); },
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };
  return (
    <div className="gs-card mb-4">
      <h2 className="font-bold">{t('admin.credits.askFundingTitle')}</h2>
      <p className="text-muted text-xs mt-0.5 mb-3">{t('admin.credits.askFundingHint')}</p>
      <div className="flex items-end gap-2 flex-wrap">
        {groupPools.length > 1 && (
          <Select className="gs-input w-auto text-sm" value={pool?.wallet_id ?? ''} onChange={(e) => setPoolId(e.target.value)} aria-label={t('common.group')}>
            {groupPools.map((p) => <option key={p.wallet_id} value={p.wallet_id}>{p.name}</option>)}
          </Select>
        )}
        <input className="gs-input w-32 text-sm" type="number" inputMode="numeric" min={1} value={amount}
          placeholder={t('admin.credits.amountPlaceholder')} aria-label={t('common.amount')}
          onChange={(e) => setAmount(e.target.value)} />
        <input className="gs-input flex-1 min-w-[200px] text-sm" value={note} maxLength={200}
          placeholder={t('admin.credits.askFundingNote')} aria-label={t('common.reason')}
          onChange={(e) => setNote(e.target.value)} />
        <button type="button" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!ok || create.isPending} onClick={submit}>
          {t('admin.credits.askFundingSubmit')}
        </button>
      </div>
    </div>
  );
}
