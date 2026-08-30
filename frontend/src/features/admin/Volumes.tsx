import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAllVolumes, useDeleteVolume } from '@/api/hooks/useVolumes';
import { Table, TableToolbar, Pagination, type Column } from '@/components/Table';
import { VolumeMountsPanel } from '@/features/volume/VolumePage';
import { EmptyState, NoResults, TableSkeleton, ErrorState } from '@/components/EmptyState';
import { useTableState, sortRows } from '@/hooks/useTableState';
import { PageHeader } from '@/components/PageHeader';
import { useConfirm } from '@/components/ConfirmDialog';
import { useUiStore } from '@/store/uiStore';
import { humanizeError, asApiError } from '@/lib/errors';
import { BlockGauge } from '@/components/BlockGauge';
import { CopyButton } from '@/components/CopyButton';
import { Database } from '@/components/icons';
import { formatGiB, accessModeLabel } from '@/lib/format';

type Vol = Record<string, unknown> & {
  id: string; name?: string; scope?: string; scope_id?: string; type?: string;
  access_mode?: string; quota_gb?: number; used_gb?: number;
  owner_id?: string | null; owner_name?: string | null;
};

// Fleet volume administration (/admin/volumes): every user's volumes with owner names — the
// user-facing /data page shows only the caller's own world, super_admin included.
export function AdminVolumes() {
  const { t } = useTranslation();
  const { data, isLoading, isError, error, refetch } = useAllVolumes();
  const del = useDeleteVolume();
  const confirm = useConfirm();
  const pushToast = useUiStore((s) => s.pushToast);
  const table = useTableState('', { sort: 'owner', dir: 'asc' });
  const [selVol, setSelVol] = useState<Vol | null>(null);

  const all = (data ?? []) as Vol[];
  const matched = all.filter((v) => {
    const q = table.query.trim().toLowerCase();
    if (!q) return true;
    return [v.name, v.id, v.owner_name, v.scope_id, v.type].some((x) => String(x ?? '').toLowerCase().includes(q));
  });
  const rows = sortRows(matched, {
    name: (v: Vol) => v.name || v.id,
    owner: (v: Vol) => v.owner_name ?? v.scope_id ?? '',
    scope: (v: Vol) => v.scope ?? '',
    quota: (v: Vol) => (v.quota_gb ? (v.used_gb ?? 0) / v.quota_gb : 0),
  }[table.sort ?? 'owner'] ?? null, table.dir);
  const pageRows = rows.slice((table.page - 1) * 25, table.page * 25);

  const onDelete = async (v: Vol) => {
    const ok = await confirm({
      title: t('volume.confirmDeleteTitle', { name: v.name || v.id }),
      body: t('volume.confirmDelete'),
      consequences: [t('admin.volumes.consequenceOwner', { owner: v.owner_name ?? v.scope_id ?? '-' })],
      confirmLabel: t('common.delete'),
      destructive: true,
      confirmText: v.name || v.id,
    });
    if (!ok) return;
    del.mutate(v.id, {
      onSuccess: () => { pushToast('success', t('volume.deleted')); refetch(); },
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };

  const columns: Column<Vol>[] = [
    {
      key: 'name',
      header: t('volume.colVolume'),
      sortBy: (v) => v.name || v.id,
      render: (v) => (
        <span className="inline-flex items-center gap-1.5 min-w-0">
          <b className="truncate">{v.name || v.id}</b>
          <CopyButton value={v.id} label={t('common.copy')} />
        </span>
      ),
    },
    {
      key: 'owner',
      header: t('admin.volumes.colOwner'),
      sortBy: (v) => v.owner_name ?? v.scope_id ?? '',
      render: (v) => v.owner_name
        ? <span className="truncate" title={v.owner_id ?? undefined}>{v.owner_name}</span>
        : <span className="font-mono text-xs">{v.scope_id ?? '-'}</span>,
    },
    {
      key: 'scope',
      header: t('volume.colScope'),
      hideOnMobile: true,
      sortBy: (v) => v.scope ?? '',
      render: (v) => <span className="gs-tag">{t(`enum.scope.${v.scope}`, { defaultValue: v.scope ?? '-' })}</span>,
    },
    {
      key: 'access_mode',
      header: t('volume.colAccessMode'),
      hideOnMobile: true,
      render: (v) => accessModeLabel(v.access_mode),
    },
    {
      key: 'quota',
      header: t('volume.colQuota'),
      align: 'right',
      sortBy: (v) => (v.quota_gb ? (v.used_gb ?? 0) / v.quota_gb : 0),
      render: (v) => {
        const pct = v.quota_gb ? Math.min(100, Math.round(((v.used_gb ?? 0) / v.quota_gb) * 100)) : 0;
        return (
          <span className="inline-flex items-center gap-2.5" title={t('volume.usagePercent', { percent: pct })}>
            <span className="gs-num">{formatGiB(v.used_gb ?? 0)} / {formatGiB(v.quota_gb ?? 0)}</span>
            <BlockGauge value={pct} label={t('volume.usagePercent', { percent: pct })} />
          </span>
        );
      },
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      sortable: false,
      render: (v) => (
        <button type="button" className="gs-btn gs-btn-sm gs-btn-danger" disabled={del.isPending} onClick={() => onDelete(v)}>
          {t('common.delete')}
        </button>
      ),
    },
  ];

  return (
    <div>
      <PageHeader title={t('admin.volumes.title')} description={t('admin.volumes.subtitle')} />
      <TableToolbar
        query={table.query}
        onQueryChange={table.setQuery}
        placeholder={t('admin.volumes.searchPlaceholder')}
        total={all.length}
        shown={matched.length}
        onClear={table.clear}
      />
      <div className="gs-card">
        {isError ? (
          <ErrorState error={error} onRetry={() => refetch()} />
        ) : isLoading ? (
          <TableSkeleton rows={4} columns={5} />
        ) : rows.length === 0 ? (
          table.isFiltered
            ? <NoResults query={table.query} onClear={table.clear} />
            : <EmptyState icon={<Database size={26} />} title={t('admin.volumes.empty')} description={t('admin.volumes.emptyDescription')} />
        ) : (
          <Table
            caption={t('admin.volumes.title')}
            columns={columns}
            rows={pageRows}
            rowKey={(v) => v.id}
            onRowClick={(v) => setSelVol((cur) => (cur?.id === v.id ? null : v))}
            expandedKey={selVol?.id ?? null}
            renderExpansion={(v) => <VolumeMountsPanel vol={{ id: v.id, name: (v as { name?: string | null }).name }} />}
            sort={table.sort}
            dir={table.dir}
            onSort={table.toggleSort}
          />
        )}
      </div>
      <Pagination page={table.page} pageSize={25} total={rows.length} onPage={table.setPage} />
    </div>
  );
}
