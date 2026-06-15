import { useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { Trans, useTranslation } from 'react-i18next';
import { useWallet, useWalletTransactions, type LedgerTxn } from '@/api/hooks/useWallet';
import { useCreateAllocationRequest, useAllocationRequests, type AllocRequest } from '@/api/hooks/useAllocations';
import { useAuthStore } from '@/auth/authStore';
import { Table, TableToolbar, Pagination, sortAccessor, type Column } from '@/components/Table';
import { PageHeader, BackLink } from '@/components/PageHeader';
import { EmptyState, NoResults, TableSkeleton } from '@/components/EmptyState';
import { Field, DisabledReason } from '@/components/Field';
import { Timestamp } from '@/components/Timestamp';
import { CopyButton } from '@/components/CopyButton';
import { useTableState, sortRows } from '@/hooks/useTableState';
import { useUnsavedGuard, unsavedGuardProps } from '@/hooks/useUnsavedGuard';
import { useUiStore } from '@/store/uiStore';
import { formatCredit, reqStatusLabel, scopeLabel } from '@/lib/format';
import i18n from '@/i18n';
import { humanizeError, asApiError } from '@/lib/errors';

const PAGE_SIZE = 25;

/** Ledger columns. Built lazily so the headers pick up the active language. */
const ledgerColumns = (): Column<LedgerTxn>[] => [
  {
    key: 'created_at',
    header: i18n.t('wallet.colWhen'),
    sortBy: (r) => new Date(r.created_at).getTime(),
    render: (r) => <Timestamp value={r.created_at} />,
  },
  {
    key: 'type',
    header: i18n.t('wallet.colType'),
    sortBy: (r) => r.type,
    render: (r) => i18n.t(`wallet.txn.${r.type}`, { defaultValue: r.type }),
  },
  {
    key: 'amount',
    header: i18n.t('wallet.colAmount'),
    sortBy: (r) => Number(r.amount),
    align: 'right',
    render: (r) => {
      const n = Number(r.amount);
      const sign = n > 0 ? 'text-free' : n < 0 ? 'text-danger' : '';
      return <span className={`font-semibold ${sign}`}>{n > 0 ? '+' : ''}{r.amount} C</span>;
    },
  },
  {
    key: 'balance_after',
    header: i18n.t('wallet.colBalanceAfter'),
    sortBy: (r) => Number(r.balance_after),
    align: 'right',
    render: (r) => <>{r.balance_after} C</>,
  },
  {
    key: 'ref',
    header: i18n.t('wallet.colRef'),
    hideOnMobile: true,
    render: (r) => (r.ref
      ? (
        <span className="inline-flex items-center gap-1">
          <code className="font-mono text-[12px] text-muted truncate max-w-[160px]" title={r.ref}>{r.ref}</code>
          <CopyButton value={r.ref} label={i18n.t('wallet.copyRef')} />
        </span>
      )
      : <span className="text-muted">-</span>),
  },
];

// The wallet screen: balance cards plus the ledger table.
export function WalletPage() {
  const { t } = useTranslation();
  const { data: wallet, isLoading, isFetching, refetch, dataUpdatedAt } = useWallet();
  const walletId = (wallet as { id?: string } | undefined)?.id;
  const { data: txns = [], isLoading: txnLoading } = useWalletTransactions(walletId);
  const { data: myReqs = [] } = useAllocationRequests('mine');
  const table = useTableState('', { sort: 'created_at', dir: 'desc' });
  const reqTable = useTableState('req', { sort: 'created_at', dir: 'desc' });

  const matched = useMemo(() => {
    const q = table.query.trim().toLowerCase();
    if (!q) return txns;
    return txns.filter((r) =>
      (r.type ?? '').toLowerCase().includes(q)
      || String(r.amount).includes(q)
      || (r.ref ?? '').toLowerCase().includes(q));
  }, [txns, table.query]);

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

  const reqCols: Column<AllocRequest>[] = [
    { key: 'created_at', header: t('wallet.colWhen'), sortBy: (r) => (r.created_at ? new Date(r.created_at).getTime() : 0), render: (r) => <Timestamp value={r.created_at} /> },
    { key: 'level', header: t('wallet.colTarget'), render: (r) => (r.level === 'user' ? t('wallet.targetMine') : scopeLabel(r.level)) },
    { key: 'amount', header: t('wallet.colAmount'), align: 'right', sortBy: (r) => Number(r.amount), render: (r) => `${r.amount} C` },
    { key: 'status', header: t('common.status'), render: (r) => <span className={`gs-pill ${r.status === 'approved' ? 'bg-free-soft text-free' : r.status === 'rejected' ? 'bg-surface-2 text-danger' : 'bg-surface-2 text-muted'}`}>{reqStatusLabel(r.status)}</span> },
  ];

  return (
    <div>
      <PageHeader
        title={t('wallet.title')}
        description={t('wallet.subtitle')}
        updatedAt={dataUpdatedAt || null}
        onRefresh={() => refetch()}
        isFetching={isFetching}
        actions={<Link to="/wallet/request" className="gs-btn gs-btn-primary">{t('wallet.requestCredits')}</Link>}
      />

      {low && (
        <p role="status" className="gs-card mb-4 border-warn text-[13px] flex items-center justify-between gap-3 flex-wrap">
          <span>{t('wallet.lowBalance', { available: formatCredit(available ?? 0) })}</span>
          <Link to={`/wallet/request?need=${Math.max(100, Math.round((balance ?? 0) * 0.5))}`} className="gs-btn gs-btn-sm gs-btn-primary">
            {t('wallet.requestCredits')}
          </Link>
        </p>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        {[
          { label: t('wallet.balance'), v: wallet?.balance, hint: t('wallet.balanceHint') },
          { label: t('wallet.reserved'), v: wallet?.reserved, hint: t('wallet.reservedHint') },
          { label: t('wallet.available'), v: wallet?.available, hint: t('wallet.availableHint') },
        ].map((s) => (
          <div key={s.label} className="gs-card">
            <div className="text-muted text-[12px] font-semibold" title={s.hint}>{s.label}</div>
            <div className="text-2xl font-extrabold mt-1 tabular-nums">
              {isLoading ? '…' : <>{formatCredit(s.v != null ? Number(s.v) : undefined)} <small className="text-[13px] text-muted font-semibold">C</small></>}
            </div>
            <p className="text-muted text-[11px] mt-1">{s.hint}</p>
          </div>
        ))}
      </div>

      {myReqs.length > 0 && (
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

      <div className="gs-card mt-4">
        <h2 className="font-bold mb-3">{t('wallet.ledger')}</h2>
        <TableToolbar
          query={table.query}
          onQueryChange={table.setQuery}
          placeholder={t('wallet.searchPlaceholder')}
          total={txns.length}
          shown={matched.length}
        />
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
    </div>
  );
}

// The credit request form: asks the active group's administrators for an allocation. Requests
// escalate up the hierarchy. No payment is involved; the request simply waits for approval.
export function CreditRequestPage() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const pushToast = useUiStore((s) => s.pushToast);
  const activeProjectId = useAuthStore((s) => s.activeProjectId);
  const projectName = useAuthStore((s) => s.memberships.find((m) => m.group_id === activeProjectId)?.project_name);
  const createReq = useCreateAllocationRequest();

  const [amount, setAmount] = useState(params.get('need') ?? '');
  const [note, setNote] = useState('');
  const noDept = !activeProjectId;
  const amountNum = Number(amount);
  const amountError = amount.trim() && (!Number.isFinite(amountNum) || amountNum <= 0) ? t('wallet.amountMustBePositive') : null;
  const valid = !!amount.trim() && amountNum > 0 && !!activeProjectId && !!note.trim();
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
          navigate('/wallet');
        },
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      },
    );
  }

  return (
    <div className="w-full max-w-2xl">
      <PageHeader
        title={t('wallet.requestCredits')}
        crumbs={[{ label: t('wallet.title'), to: '/wallet' }, { label: t('wallet.requestCredits') }]}
        actions={<BackLink to="/wallet" />}
      />
      <form className="gs-card space-y-3" {...unsavedGuardProps} onSubmit={(e) => { e.preventDefault(); submit(); }}>
        {noDept ? (
          <p role="alert" className="text-danger text-[13px]">{t('wallet.noActiveGroup')}</p>
        ) : (
          <p className="text-muted text-[12px]"><Trans i18nKey="wallet.requestGoesTo" values={{ group: projectName ?? activeProjectId }} components={{ 1: <b /> }} /></p>
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
    </div>
  );
}
