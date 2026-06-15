import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import i18n from '@/i18n';
import {
  useAllocationRequests, useApproveRequest, useRejectRequest, useEscalateRequest,
  useAllocate, useAllocationScope, useSetMonthlyGrant, useTopupWallet,
  type AllocRequest, type ScopeWallet, type SystemTotal,
} from '@/api/hooks/useAllocations';
import { Table, sortAccessor, type Column } from '@/components/Table';
import { EmptyState, TableSkeleton } from '@/components/EmptyState';
import { CopyableId } from '@/components/CopyButton';
import { useTableState, sortRows } from '@/hooks/useTableState';
import { PageHeader } from '@/components/PageHeader';
import { useUiStore } from '@/store/uiStore';
import { useAuthStore } from '@/auth/authStore';
import { humanizeError, asApiError } from '@/lib/errors';
import { SettlementReport } from './SettlementReport';

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
  const { data: scope, isFetching, refetch, dataUpdatedAt } = useAllocationScope();

  const allPools = scope?.pools ?? [];
  // The top pool for this role: an organization pool when there is one (managing groups), otherwise
  // the group pool (managing individuals).
  const orgPools = allPools.filter((p) => p.scope === 'org');
  const pools = orgPools.length ? orgPools : allPools.filter((p) => p.scope === 'group');

  return (
    <div>
      <PageHeader
        title={t('admin.credits.title')}
        description={t('admin.credits.subtitle')}
        crumbs={[{ label: t('admin.credits.breadcrumb') }]}
        updatedAt={dataUpdatedAt || null}
        onRefresh={() => refetch()}
        isFetching={isFetching}
      />

      {canMint && (
        <SystemTotalsCard system={scope?.system ?? null} orgPools={orgPools} childrenOf={(id) => scope?.children[id] ?? []} />
      )}

      {pools.length === 0 ? (
        <div className="gs-card text-muted text-[13px]">{t('admin.credits.noPool')}</div>
      ) : (
        pools.map((p) => (
          <PoolBlock key={p.wallet_id} pool={p} children={scope?.children[p.wallet_id] ?? []} />
        ))
      )}

      <RequestsCard />

      {canMint && <SettlementReport />}
    </div>
  );
}

function PoolBlock({ pool, children }: { pool: ScopeWallet; children: ScopeWallet[] }) {
  const { t } = useTranslation();
  const childLabel = t(pool.scope === 'org' ? 'admin.credits.childGroup' : pool.scope === 'group' ? 'admin.credits.childUser' : 'admin.credits.childGeneric');
  const poolTable = useTableState(`pool-${pool.wallet_id}`, { sort: 'name', dir: 'asc' });
  const cols: Column<ScopeWallet>[] = [
    { key: 'name', header: childLabel, sortBy: (c) => c.name, render: (c) => <b>{c.name}</b> },
    { key: 'balance', header: t('admin.credits.colBalance'), sortBy: (c) => Number(c.balance ?? 0), align: 'right', render: (c) => <span className="tabular-nums">{C(c.balance)}</span> },
    { key: 'grant', header: t('admin.credits.colMonthlyRefill'), sortBy: (c) => Number(c.monthly_grant ?? 0), align: 'right', render: (c) => <span className="tabular-nums text-muted">{C(c.monthly_grant)}</span> },
    { key: 'act', header: t('admin.credits.colActions'), sortable: false, align: 'right', render: (c) => <ChildActions pool={pool} child={c} /> },
  ];
  return (
    <div className="gs-card mb-4">
      <div className="mb-3">
        <h2 className="font-bold">{pool.name} <span className="gs-pill bg-surface-2 text-muted text-[11px]">{t('admin.credits.poolBadge')}</span></h2>
        <div className="text-muted text-[12px]">{t('admin.credits.allocateFrom', { amount: C(pool.balance), children: childLabel })}</div>
      </div>
      <Table
        caption={childLabel}
        columns={cols}
        rows={sortRows(children, sortAccessor(cols, poolTable.sort), poolTable.dir)}
        rowKey={(c) => c.wallet_id}
        empty={t('admin.credits.emptyChildren', { children: childLabel })}
        sort={poolTable.sort}
        dir={poolTable.dir}
        onSort={poolTable.toggleSort}
      />
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
  const inp = 'px-2 py-1 border border-border rounded-md bg-surface-2 text-[13px] w-24';

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
      <h2 className="font-bold mb-1">{t('admin.credits.systemTitle')} <span className="gs-pill bg-primary-soft text-primary text-[11px]">{t('admin.credits.systemBadge')}</span></h2>
      <p className="text-muted text-[12px] mb-3">{t('admin.credits.systemNote')}</p>

      <div data-url-state className="flex flex-wrap items-end gap-4 mb-3 pb-3 border-b border-border">
        <label className="text-[11px] text-muted">{t('admin.credits.monthlyTotal')}
          <div className="flex gap-1 mt-0.5">
            <input className={inp} type="number" inputMode="numeric" min={0} max={100000000} step="any" value={total} onChange={(e) => setTotal(e.target.value)} placeholder={system?.monthly_total ?? '0'} disabled={!system} autoComplete="off" onBlur={(e) => setTotal(String(Math.min(100000000, Math.max(0, Number(e.target.value) || 0))))} />
            <button type="button" className="gs-btn gs-btn-sm gs-btn-primary disabled:opacity-50" disabled={!system || setGrant.isPending} onClick={setTotalGrant}>{t('admin.credits.set')}</button>
          </div>
        </label>
        <label className="text-[11px] text-muted">{t('admin.credits.issue')}
          <div className="flex gap-1 mt-0.5">
            <input className={inp} type="number" inputMode="numeric" min={0} max={100000000} step="any" value={issue} onChange={(e) => setIssue(e.target.value)} placeholder="0" disabled={!system} autoComplete="off" onBlur={(e) => setIssue(String(Math.min(100000000, Math.max(0, Number(e.target.value) || 0))))} />
            <button type="button" className="gs-btn gs-btn-sm disabled:opacity-50" disabled={!system || Number(issue) <= 0 || topup.isPending} onClick={doIssue}>{t('admin.credits.issue')}</button>
          </div>
        </label>
        <div className="flex flex-nowrap gap-3">
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

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-surface-2 rounded-card px-3 py-2 shrink-0 flex items-baseline gap-2 whitespace-nowrap">
      <span className="text-muted text-[12px]">{label}</span>
      <span className="text-base font-extrabold tabular-nums">{value}</span>
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
  const inp = 'px-2 py-1 border border-border rounded-md bg-surface-2 text-[13px] w-20';

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
        <label className="text-[11px] text-muted" htmlFor={`grant-${child.wallet_id}`}>{t('admin.credits.refillShort')}</label>
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

function RequestsCard() {
  const { t } = useTranslation();
  const { data: incoming = [], isLoading } = useAllocationRequests('incoming');
  const approve = useApproveRequest();
  const reject = useRejectRequest();
  const escalate = useEscalateRequest();
  const pushToast = useUiStore((s) => s.pushToast);

  const onApprove = (r: AllocRequest) =>
    approve.mutate({ id: r.id }, {
      onSuccess: () => pushToast('success', t('admin.credits.approved')),
      onError: (e) => {
        const err = asApiError(e);
        pushToast('error', err.code === 'insufficient_pool' ? t('admin.credits.poolShortEscalate') : humanizeError(err));
      },
    });
  const onReject = (r: AllocRequest) => {
    const reason = window.prompt(t('admin.credits.rejectPrompt'));
    if (!reason) return;
    reject.mutate({ id: r.id, body: { reason } }, {
      onSuccess: () => pushToast('info', t('admin.credits.rejected')),
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };
  const onEscalate = (r: AllocRequest) => {
    const amt = window.prompt(t('admin.credits.escalatePrompt'), r.amount);
    escalate.mutate({ id: r.id, body: amt ? { amount: amt } : {} }, {
      onSuccess: () => pushToast('success', t('admin.credits.escalated')),
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };

  const cols: Column<AllocRequest>[] = [
    { key: 'level', header: t('admin.credits.colRequest'), render: (r) => t(`admin.credits.level${r.level.charAt(0).toUpperCase()}${r.level.slice(1)}`, { defaultValue: r.level }) },
    { key: 'group', header: t('common.group'), render: (r) => r.group_name ? <span className="text-[12px]">{r.group_name}</span> : <span className="text-muted text-[12px]">—</span> },
    { key: 'amount', header: t('common.amount'), render: (r) => C(r.amount) },
    { key: 'requester_id', header: t('admin.credits.colRequester'), sortBy: (r) => r.requester_name ?? r.requester_id, render: (r) => r.requester_name ?? <CopyableId value={r.requester_id} /> },
    {
      key: 'actions', header: '',
      render: (r) => (
        <div className="flex gap-1.5">
          <button type="button" className="gs-btn gs-btn-sm gs-btn-primary" disabled={approve.isPending} onClick={() => onApprove(r)}>{t('common.approve')}</button>
          {r.fulfiller_scope !== 'system' && (
            <button type="button" className="gs-btn gs-btn-sm" disabled={escalate.isPending} onClick={() => onEscalate(r)}>{t('admin.credits.escalate')}</button>
          )}
          <button type="button" className="gs-btn gs-btn-sm text-danger" disabled={reject.isPending} onClick={() => onReject(r)}>{t('common.reject')}</button>
        </div>
      ),
    },
  ];

  const table = useTableState('req', { sort: 'created_at', dir: 'desc' });

  return (
    <div className="gs-card">
      <h2 className="font-bold mb-3">{t('admin.credits.incoming')} <span className="text-muted text-[12px] font-normal">{t('admin.credits.incomingCount', { count: incoming.length })}</span></h2>
      {isLoading ? (
        <TableSkeleton rows={3} columns={4} />
      ) : incoming.length === 0 ? (
        <EmptyState icon="✓" title={t('admin.credits.emptyRequests')} description={t('admin.credits.emptyRequestsHint')} />
      ) : (
        <Table
          caption={t('admin.credits.incoming')}
          columns={cols}
          rows={sortRows(incoming, sortAccessor(cols, table.sort), table.dir)}
          rowKey={(r) => r.id}
          sort={table.sort}
          dir={table.dir}
          onSort={table.toggleSort}
        />
      )}
    </div>
  );
}
