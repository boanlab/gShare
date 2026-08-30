import { useState } from 'react';
import { Select } from '@/components/Select';
import { useTranslation } from 'react-i18next';
import { DisabledReason } from '@/components/Field';
import { useWallets, useBillingReport, type BillingReportQuery } from '@/api/hooks/useBilling';
import { useOrganizations, useProjects } from '@/api/hooks/useGroups';
import { Table, type Column } from '@/components/Table';
import { humanizeError, asApiError } from '@/lib/errors';
import { scopeLabel } from '@/lib/format';

// The settlement report: consumption, top-ups, and GPU hours per period and scope (organization,
// group, or wallet), from /metrics/billing-report.
export function SettlementReport() {
  const { t } = useTranslation();
  const [scope, setScope] = useState<'org' | 'group' | 'wallet'>('org');
  const [scopeId, setScopeId] = useState('');
  const orgs = useOrganizations().data ?? [];
  const groups = useProjects().data ?? [];
  const wallets = useWallets({}).data ?? [];
  const scopeTargets =
    scope === 'org' ? orgs.map((o) => ({ id: o.id, label: o.name }))
    : scope === 'group' ? groups.map((g) => ({ id: g.id, label: g.name }))
    : wallets.map((w) => ({ id: w.id, label: w.owner_name ? `${scopeLabel(w.owner_type)} · ${w.owner_name}` : `${scopeLabel(w.owner_type)} · ${w.owner_id}` }));
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [groupBy, setGroupBy] = useState<'group' | 'offering' | 'wallet'>('group');
  const [query, setQuery] = useState<BillingReportQuery | undefined>(undefined);

  type ReportRow = { group: string; group_name?: string; consumed?: string; topup?: string; gpu_hours?: string };
  const reportQ = useBillingReport(query);
  const report = reportQ.data as
    | { totals?: Record<string, string>; rows?: ReportRow[]; currency?: string }
    | undefined;

  const run = () => {
    if (!from || !to) return;
    setQuery({
      scope,
      scope_id: scopeId.trim() || undefined,
      from: new Date(from).toISOString(),
      to: new Date(to).toISOString(),
      group_by: groupBy,
      format: 'json',
    });
  };

  const rowColumns: Column<ReportRow>[] = [
    { key: 'group', header: t('common.group'), render: (r) => <b>{r.group_name ?? r.group}</b> },
    { key: 'consumed', header: t('admin.settlement.colConsumed'), render: (r) => r.consumed ?? '-' },
    { key: 'topup', header: t('admin.settlement.colTopup'), render: (r) => r.topup ?? '-' },
    { key: 'gpu_hours', header: t('admin.settlement.colGpuHours'), render: (r) => r.gpu_hours ?? '-' },
  ];

  return (
    <section className="mt-8">
      <h2 className="text-lg font-bold mb-1">{t('admin.settlement.title')}</h2>
      <p className="text-muted text-sm mb-4">{t('admin.settlement.subtitle')}</p>

      <form
        data-url-state
        className="gs-card mb-4 flex gap-3 flex-wrap items-end"
        onSubmit={(e) => { e.preventDefault(); if (from && to) run(); }}
      >
        <label className="text-sm font-semibold">
          {t('admin.settlement.scope')}
          <Select className="gs-input mt-1 w-auto block" value={scope} onChange={(e) => { setScope(e.target.value as typeof scope); setScopeId(''); }}>
            <option value="org">{t('enum.scope.org')}</option>
            <option value="group">{t('enum.scope.group')}</option>
            <option value="wallet">{t('enum.scope.wallet')}</option>
          </Select>
        </label>
        <label className="text-sm font-semibold">
          {t('admin.settlement.target')}
          <Select className="gs-input mt-1 w-56 block" value={scopeId} onChange={(e) => setScopeId(e.target.value)}>
            <option value="">{t('admin.settlement.everything')}</option>
            {scopeTargets.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
          </Select>
        </label>
        <label className="text-sm font-semibold">
          {t('common.fromDate')}
          <input type="datetime-local" className="gs-input mt-1 block" value={from} onChange={(e) => setFrom(e.target.value)} autoComplete="off" />
        </label>
        <label className="text-sm font-semibold">
          {t('common.toDate')}
          <input type="datetime-local" className="gs-input mt-1 block" value={to} onChange={(e) => setTo(e.target.value)} autoComplete="off" />
        </label>
        <label className="text-sm font-semibold">
          {t('common.groupBy')}
          <Select className="gs-input mt-1 w-auto block" value={groupBy} onChange={(e) => setGroupBy(e.target.value as typeof groupBy)}>
            <option value="group">{t('enum.scope.group')}</option>
            <option value="offering">{t('admin.settlement.byOffering')}</option>
            <option value="wallet">{t('admin.settlement.byWallet')}</option>
          </Select>
        </label>
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!from || !to}>
          {t('admin.settlement.run')}
        </button>
        <span className="basis-full h-0" aria-hidden="true" />
        <DisabledReason reasons={from && to ? [] : [t('admin.settlement.rangeNeeded')]} />
      </form>

      <div className="gs-card">
        {!query ? (
          <p className="text-muted">{t('admin.settlement.pickPeriod')}</p>
        ) : reportQ.isLoading ? (
          <p className="text-muted">{t('admin.settlement.aggregating')}</p>
        ) : reportQ.isError ? (
          <p className="text-danger">{humanizeError(asApiError(reportQ.error))}</p>
        ) : (
          <>
            {report?.totals && (
              <div className="flex flex-wrap gap-4 mb-4 text-sm">
                {Object.entries(report.totals).map(([k, v]) => (
                  <div key={k} className="flex items-baseline gap-1.5">
                    <span className="text-muted text-xs">{k}</span>
                    <b className="gs-num text-sm">{v}</b>
                  </div>
                ))}
                {report.currency && <span className="text-muted">{t('admin.settlement.unit', { currency: report.currency })}</span>}
              </div>
            )}
            <Table columns={rowColumns} rows={report?.rows ?? []} rowKey={(r) => r.group} empty={t('admin.settlement.empty')} />
          </>
        )}
      </div>
    </section>
  );
}
