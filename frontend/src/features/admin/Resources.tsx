import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  useOfferings,
  useOffering,
  useCreateOffering,
  useUpdateOffering,
  useDeleteOffering,
  usePresets,
  usePreset,
  useCreatePreset,
  useUpdatePreset,
  useDeletePreset,
  usePolicies,
  usePolicy,
  useCreatePolicy,
  useUpdatePolicy,
  useDeletePolicy,
  type Offering,
  type ResourcePreset,
  type ResourcePolicy,
  type ResourceClass,
  type PolicyScope,
} from '@/api/hooks/useResources';
import { useOrganizations, useProjects } from '@/api/hooks/useGroups';
import { useUsers } from '@/api/hooks/useUsers';
import { Table, TableToolbar, Pagination, sortAccessor, type Column } from '@/components/Table';
import { EmptyState, NoResults, TableSkeleton } from '@/components/EmptyState';
import { useTableState, sortRows } from '@/hooks/useTableState';
import { useConfirm } from '@/components/ConfirmDialog';
import { PageHeader, BackLink } from '@/components/PageHeader';
import { DisabledReason } from '@/components/Field';
import { useFormGuard } from '@/hooks/useUnsavedGuard';
import { useUiStore } from '@/store/uiStore';
import { humanizeError, asApiError } from '@/lib/errors';
import { formatVram, scopeLabel } from '@/lib/format';

// Resource settings, as three tabs: offerings, presets, and resource policy (quotas).

type Tab = 'offerings' | 'presets' | 'policy';

const CATALOGUE_PAGE = 25;

export function AdminResources() {
  const { t } = useTranslation();
  // The tab is in the URL: "the presets screen" is a thing people link each other to.
  const [params, setParams] = useSearchParams();
  const tab = (params.get('tab') as Tab) || 'offerings';
  const setTab = (v: Tab) => setParams((p) => {
    const next = new URLSearchParams(p);
    if (v === 'offerings') next.delete('tab'); else next.set('tab', v);
    return next;
  }, { replace: true });
  // Catalogue freshness, shared by all three tabs.
  const offeringsQ = useOfferings();

  return (
    <div>
      <PageHeader
        title={t('admin.resources.title')}
        description={t('admin.resources.subtitle')}
        updatedAt={offeringsQ.dataUpdatedAt || null}
        onRefresh={() => offeringsQ.refetch()}
        isFetching={offeringsQ.isFetching}
      />

      <div className="flex gap-2 mb-4 border-b border-border" role="tablist" aria-label={t('admin.resources.title')}>
        {([
          ['offerings', t('admin.resources.tabOfferings')],
          ['presets', t('admin.resources.tabPresets')],
          ['policy', t('admin.resources.tabPolicy')],
        ] as [Tab, string][]).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            className={`px-4 py-2 text-[13px] font-bold border-b-2 -mb-px ${
              tab === id ? 'border-primary text-primary' : 'border-transparent text-muted'
            }`}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'offerings' && <OfferingsTab />}
      {tab === 'presets' && <PresetsTab />}
      {tab === 'policy' && <PolicyTab />}
    </div>
  );
}

function OfferingsTab() {
  const { t } = useTranslation();
  const { data, isLoading } = useOfferings();
  const del = useDeleteOffering();
  const pushToast = useUiStore((s) => s.pushToast);
  const confirm = useConfirm();

  const onDelete = async (o: Offering) => {
    const ok = await confirm({
      title: t('admin.resources.confirmDeleteOfferingTitle', { name: o.name }),
      body: t('admin.resources.confirmDeleteOffering', { name: o.name }),
      consequences: [t('admin.resources.consequenceOffering')],
      confirmLabel: t('common.delete'),
      destructive: true,
      confirmText: o.name,
    });
    if (!ok) return;
    del.mutate(o.id, {
      onSuccess: () => pushToast('success', t('admin.resources.offeringDeleted')),
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };

  const table = useTableState('off', { sort: 'name', dir: 'asc' });
  const columns: Column<Offering>[] = [
    { key: 'name', header: t('common.name'), render: (o) => <b>{o.name}</b> },
    { key: 'gpu_model', header: t('admin.resources.colModel'), render: (o) => o.gpu_model ?? <span className="text-muted">{t('admin.resources.cpuPool')}</span> },
    {
      key: 'spec',
      header: t('admin.resources.colVramCores'),
      render: (o) => (o.resource_class === 'cpu' ? `${o.cpu ?? 0} vCPU · ${o.mem_gb ?? 0} GiB` : `${formatVram(o.gpu_mem_mb)} · ${o.gpu_cores}%`),
    },
    {
      key: 'resource_class',
      header: t('admin.resources.colClass'),
      render: (o) =>
        o.resource_class === 'cpu' ? (
          <span className="gs-pill bg-free-soft text-free">CPU</span>
        ) : (
          <span className="gs-pill bg-gpu-soft text-gpu">GPU</span>
        ),
    },
    { key: 'credit_per_hour', header: t('admin.resources.colRate'), render: (o) => (o.resource_class === 'cpu' ? t('admin.resources.computeProportional') : `${o.credit_per_hour} C/h`) },
    {
      key: 'min_cuda',
      header: t('admin.resources.colMinCuda'),
      render: (o) => (o.resource_class === 'cpu' ? <span className="text-muted">—</span> : (o.min_cuda ? `≥ ${o.min_cuda}` : <span className="text-muted">{t('admin.resources.noLimit')}</span>)),
    },
    {
      key: 'status',
      header: t('common.status'),
      render: (o) => <span className={`gs-pill ${o.status === 'active' ? 'bg-free-soft text-free' : 'bg-surface-2 text-muted'}`}>{o.status}</span>,
    },
    {
      key: 'actions',
      header: t('admin.resources.colActions'),
      render: (o) => (
        <div className="flex flex-nowrap gap-2">
          <Link to={`/admin/resources/offerings/${o.id}/edit`} className="gs-btn gs-btn-sm">{t('common.edit')}</Link>
          <button type="button" className="gs-btn gs-btn-sm text-danger" disabled={del.isPending} onClick={() => onDelete(o)}>{t('common.delete')}</button>
        </div>
      ),
    },
  ];

  const all = (data ?? []) as Offering[];
  const match = (o: Offering) => `${o.name} ${o.gpu_model ?? ''}`.toLowerCase();
  const matched = all.filter((r) => !table.query.trim() || match(r).includes(table.query.trim().toLowerCase()));
  const sorted = sortRows(matched, sortAccessor(columns, table.sort), table.dir);
  const rows = sorted.slice((table.page - 1) * CATALOGUE_PAGE, table.page * CATALOGUE_PAGE);

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <div>
          <h2 className="font-bold">{t('admin.resources.offeringsTitle')}</h2>
          <p className="text-muted text-[12px]">{t('admin.resources.offeringsNote')}</p>
        </div>
        <Link to="/admin/resources/offerings/new" className="gs-btn gs-btn-primary">{t('admin.resources.addOffering')}</Link>
      </div>
      <TableToolbar
        query={table.query}
        onQueryChange={table.setQuery}
        placeholder={t('admin.resources.searchPlaceholder')}
        total={all.length}
        shown={matched.length}
        onClear={table.clear}
      />
      <div className="gs-card">
        {isLoading ? (
          <TableSkeleton rows={4} columns={5} />
        ) : rows.length === 0 ? (
          table.isFiltered
            ? <NoResults query={table.query} onClear={table.clear} />
            : <EmptyState icon="◇" title={t('admin.resources.emptyOfferings')} />
        ) : (
          <Table
            caption={t('admin.resources.offeringsTitle')}
            columns={columns}
            rows={rows}
            rowKey={(o) => o.id}
            sort={table.sort}
            dir={table.dir}
            onSort={table.toggleSort}
          />
        )}
        <Pagination page={table.page} pageSize={CATALOGUE_PAGE} total={sorted.length} onPage={table.setPage} />
        <p className="text-muted text-[11.5px] mt-3">
          {t('admin.resources.billingNote')}
        </p>
      </div>
    </div>
  );
}

// Offering editing, at /admin/resources/offerings/:offeringId/edit.
export function EditOfferingPage() {
  const { t } = useTranslation();
  const { offeringId = '' } = useParams();
  const offering = useOffering(offeringId).data;
  return (
    <div className="w-full">
      <PageHeader
        title={t('admin.resources.editOffering') + (offering ? ' — ' + (offering.name) : '')}
        crumbs={[{ label: t('admin.resources.title'), to: '/admin/resources' }, { label: t('admin.resources.editOffering') }]}
        actions={<BackLink to={'/admin/resources'} />}
      />
      {offering ? <EditOfferingForm offering={offering} /> : <p className="text-muted">{t('admin.resources.offeringNotFound')}</p>}
    </div>
  );
}

function EditOfferingForm({ offering }: { offering: Offering }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const update = useUpdateOffering();
  const pushToast = useUiStore((s) => s.pushToast);
  const isCpu = offering.resource_class === 'cpu';
  const [name, setName] = useState(offering.name);
  const [gpuModel, setGpuModel] = useState(offering.gpu_model ?? '');
  const [memMb, setMemMb] = useState(String(offering.gpu_mem_mb ?? 0));
  const [cores, setCores] = useState(String(offering.gpu_cores ?? 0));
  const [cpu, setCpu] = useState(String(offering.cpu ?? 0));
  const [mem, setMem] = useState(String(offering.mem_gb ?? 0));
  const [disk, setDisk] = useState(String((offering as { disk_gb?: number }).disk_gb ?? 0));
  const [cph, setCph] = useState(String(offering.credit_per_hour ?? '0'));
  const [minCuda, setMinCuda] = useState(offering.min_cuda ?? '');
  const [status, setStatus] = useState<'active' | 'inactive'>(offering.status === 'inactive' ? 'inactive' : 'active');

  const submit = () =>
    update.mutate(
      isCpu
        ? { id: offering.id, name: name.trim(), cpu: Number(cpu), mem_gb: Number(mem), disk_gb: Number(disk), status }
        : { id: offering.id, name: name.trim(), gpu_model: gpuModel.trim(), gpu_mem_mb: Number(memMb), gpu_cores: Number(cores), cpu: Number(cpu), mem_gb: Number(mem), disk_gb: Number(disk), credit_per_hour: cph.trim(), status, min_cuda: minCuda.trim() },
      {
        onSuccess: () => { pushToast('success', t('admin.resources.offeringUpdated', { name })); navigate('/admin/resources'); },
        onError: (e) => { const m = humanizeError(asApiError(e)); setServerError(m); pushToast('error', m); },
      },
    );


  const [serverError, setServerError] = useState<string | null>(null);
  const guard = useFormGuard();
  return (
    <form className="gs-card" noValidate {...guard.props} onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <div className="grid gap-3">
        <div className="grid grid-cols-2 gap-3">
          <label className="text-[13px] font-semibold">{t('common.name')}<input className="gs-input mt-1 w-full" value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" /></label>
          <label className="text-[13px] font-semibold">{t('common.status')}
            <select className="gs-input mt-1 w-full" value={status} onChange={(e) => setStatus(e.target.value as 'active' | 'inactive')}>
              <option value="active">{t('enum.status.active')}</option>
              <option value="inactive">{t('enum.status.inactive')}</option>
            </select>
          </label>
        </div>
        {!isCpu && (
          <div className="grid grid-cols-2 gap-3">
            <label className="text-[13px] font-semibold">{t('admin.resources.gpuModel')}<input className="gs-input mt-1 w-full" value={gpuModel} onChange={(e) => setGpuModel(e.target.value)} autoComplete="off" /></label>
            <label className="text-[13px] font-semibold">VRAM(MB)<input className="gs-input mt-1 w-full" type="number" value={memMb} onChange={(e) => setMemMb(e.target.value)} min={0} max={1048576} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setMemMb(String(Math.min(1048576, Math.max(0, Number(e.target.value) || 0))))} /></label>
            <label className="text-[13px] font-semibold">{t('admin.resources.cores')}<input className="gs-input mt-1 w-full" type="number" value={cores} onChange={(e) => setCores(e.target.value)} min={0} max={100} step={1} inputMode="numeric" autoComplete="off" onBlur={(e) => setCores(String(Math.min(100, Math.max(0, Number(e.target.value) || 0))))} /></label>
            <label className="text-[13px] font-semibold">{t('admin.resources.rate')}<input className="gs-input mt-1 w-full" value={cph} onChange={(e) => setCph(e.target.value)} autoComplete="off" /></label>
            <label className="text-[13px] font-semibold">{t('admin.resources.minCuda')}<input className="gs-input mt-1 w-full" value={minCuda} onChange={(e) => setMinCuda(e.target.value)} placeholder={t('admin.resources.minCudaPlaceholder')} autoComplete="off" /></label>
          </div>
        )}
        <div className="grid grid-cols-3 gap-3">
          <label className="text-[13px] font-semibold">CPU<input className="gs-input mt-1 w-full" type="number" value={cpu} onChange={(e) => setCpu(e.target.value)} min={0} max={512} step={1} inputMode="numeric" autoComplete="off" onBlur={(e) => setCpu(String(Math.min(512, Math.max(0, Number(e.target.value) || 0))))} /></label>
          <label className="text-[13px] font-semibold">MEM(GiB)<input className="gs-input mt-1 w-full" type="number" value={mem} onChange={(e) => setMem(e.target.value)} min={0} max={8192} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setMem(String(Math.min(8192, Math.max(0, Number(e.target.value) || 0))))} /></label>
          <label className="text-[13px] font-semibold">DISK(GiB)<input className="gs-input mt-1 w-full" type="number" value={disk} onChange={(e) => setDisk(e.target.value)} min={0} max={65536} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setDisk(String(Math.min(65536, Math.max(0, Number(e.target.value) || 0))))} /></label>
        </div>
      </div>
      {serverError && <p role="alert" className="text-danger text-[12.5px] mt-3">{serverError}</p>}
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={name.trim() ? [] : [t('common.name')]} />
        <button type="button" className="gs-btn" onClick={() => navigate('/admin/resources')}>{t('common.cancel')}</button>
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!name.trim() || update.isPending}>
          {update.isPending ? t('admin.resources.saving') : t('common.save')}</button>
      </div>
    </form>
  );
}

// New offering, at /admin/resources/offerings/new.
export function CreateOfferingPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const create = useCreateOffering();
  const pushToast = useUiStore((s) => s.pushToast);
  const [name, setName] = useState('');
  const [rc, setRc] = useState<ResourceClass>('gpu');
  const [gpuModel, setGpuModel] = useState('RTX-4090');
  const [memMb, setMemMb] = useState('8192');
  const [cores, setCores] = useState('50');
  const [cph, setCph] = useState('120.00');
  const [minCuda, setMinCuda] = useState('');

  const isCpu = rc === 'cpu';
  const valid = name.trim().length > 0 && (isCpu || (gpuModel.trim() && Number(memMb) > 0));
  const blockers = [
    !name.trim() && t('common.name'),
    !isCpu && !gpuModel.trim() && t('admin.resources.gpuModel'),
    !isCpu && !(Number(memMb) > 0) && 'VRAM(MB)',
  ].filter(Boolean) as string[];

  const submit = () => {
    const body = isCpu
      ? { name: name.trim(), resource_class: 'cpu' as const, gpu_model: null, gpu_mem_mb: 0, gpu_cores: 0, credit_per_hour: '0.00' }
      : {
          name: name.trim(),
          resource_class: 'gpu' as const,
          gpu_model: gpuModel.trim(),
          gpu_mem_mb: Number(memMb),
          gpu_cores: Number(cores),
          credit_per_hour: cph.trim(),
          min_cuda: minCuda.trim() || null,
        };
    setServerError(null);
    create.mutate(body, {
      onSuccess: () => { pushToast('success', t('admin.resources.offeringCreated', { name })); navigate('/admin/resources'); },
      onError: (e) => { const m = humanizeError(asApiError(e)); setServerError(m); pushToast('error', m); },
    });
  };


  const [serverError, setServerError] = useState<string | null>(null);
  const guard = useFormGuard();
  return (
    <div className="w-full">
      <PageHeader
        title={t('admin.resources.newOffering')}
        crumbs={[{ label: t('admin.resources.title'), to: '/admin/resources' }, { label: t('admin.resources.newOffering') }]}
        actions={<BackLink to={'/admin/resources'} />}
      />
      <form className="gs-card" noValidate {...guard.props} onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <div className="grid gap-3">
        <label className="text-[13px] font-semibold">
          {t('common.name')}
          <input className="gs-input mt-1 w-full" value={name} onChange={(e) => setName(e.target.value)} placeholder="rtx4090-8g" autoComplete="off" />
        </label>
        <label className="text-[13px] font-semibold">
          {t('admin.resources.resourceClass')}
          <select className="gs-input mt-1 w-full" value={rc} onChange={(e) => setRc(e.target.value as ResourceClass)}>
            <option value="gpu">{t('admin.resources.classGpu')}</option>
            <option value="cpu">{t('admin.resources.classCpu')}</option>
          </select>
        </label>
        {!isCpu && (
          <>
            <label className="text-[13px] font-semibold">
              {t('admin.resources.gpuModel')}
              <input className="gs-input mt-1 w-full" value={gpuModel} onChange={(e) => setGpuModel(e.target.value)} autoComplete="off" />
            </label>
            <div className="grid grid-cols-2 gap-3">
              <label className="text-[13px] font-semibold">
                VRAM (MB)
                <input className="gs-input mt-1 w-full" type="number" value={memMb} onChange={(e) => setMemMb(e.target.value)} min={0} max={1048576} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setMemMb(String(Math.min(1048576, Math.max(0, Number(e.target.value) || 0))))} />
              </label>
              <label className="text-[13px] font-semibold">
                {t('admin.resources.cores')}
                <input className="gs-input mt-1 w-full" type="number" value={cores} onChange={(e) => setCores(e.target.value)} min={0} max={100} step={1} inputMode="numeric" autoComplete="off" onBlur={(e) => setCores(String(Math.min(100, Math.max(0, Number(e.target.value) || 0))))} />
              </label>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <label className="text-[13px] font-semibold">
                {t('admin.resources.rate')}
                <input className="gs-input mt-1 w-full" value={cph} onChange={(e) => setCph(e.target.value)} autoComplete="off" />
              </label>
              <label className="text-[13px] font-semibold">
                {t('admin.resources.minCuda')}
                <input className="gs-input mt-1 w-full" value={minCuda} onChange={(e) => setMinCuda(e.target.value)} placeholder={t('admin.resources.minCudaPlaceholderOptional')} autoComplete="off" />
              </label>
            </div>
          </>
        )}
        {isCpu && <p className="text-muted text-[12px]">{t('admin.resources.cpuOfferingNote')}</p>}
      </div>
      {serverError && <p role="alert" className="text-danger text-[12.5px] mt-3">{serverError}</p>}
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={valid ? [] : blockers} />
        <button type="button" className="gs-btn" onClick={() => navigate('/admin/resources')}>{t('common.cancel')}</button>
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!valid || create.isPending}>
          {create.isPending ? t('admin.resources.creating') : t('common.create')}</button>
      </div>
      </form>
    </div>
  );
}

function PresetsTab() {
  const { t } = useTranslation();
  const { data, isLoading } = usePresets();
  const del = useDeletePreset();
  const pushToast = useUiStore((s) => s.pushToast);
  const confirm = useConfirm();

  const onDelete = async (p: ResourcePreset) => {
    const ok = await confirm({
      title: t('admin.resources.confirmDeletePresetTitle', { name: p.name }),
      body: t('admin.resources.confirmDeletePreset', { name: p.name }),
      consequences: [t('admin.resources.consequencePreset')],
      confirmLabel: t('common.delete'),
      destructive: true,
      confirmText: p.name,
    });
    if (!ok) return;
    del.mutate(p.id, {
      onSuccess: () => pushToast('success', t('admin.resources.presetDeleted')),
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };

  const table = useTableState('pre', { sort: 'name', dir: 'asc' });
  const columns: Column<ResourcePreset>[] = [
    { key: 'name', header: t('admin.resources.colPreset'), render: (p) => <b>{p.name}</b> },
    { key: 'kind', header: t('admin.resources.colKind'), render: (p) => <span className={`gs-pill ${p.kind === 'gpu' ? 'bg-primary-soft text-primary' : 'bg-surface-2 text-muted'}`}>{p.kind === 'gpu' ? 'GPU' : t('admin.resources.kindCompute')}</span> },
    { key: 'spec', header: t('admin.resources.colSpec'), render: (p) => p.kind === 'gpu'
        ? <span className="text-[12px]">VRAM {p.gpu_frac != null ? t('admin.resources.vramFractionOf', { percent: Math.round(p.gpu_frac * 100) }) : '—'} · {t('admin.resources.cores')} {p.gpu_cores}% · {t(p.mode === 'exclusive' ? 'admin.resources.modeExclusive' : 'admin.resources.modeShared')}</span>
        : <span className="text-[12px]">CPU {p.cpu} core · MEM {p.mem_gb} GiB · DISK {p.disk_gb ?? 0} GiB</span> },
    { key: 'actions', header: t('admin.resources.colActions'), render: (p) => (
      <div className="flex flex-nowrap gap-2">
        <Link to={`/admin/resources/presets/${p.id}/edit`} className="gs-btn gs-btn-sm">{t('common.edit')}</Link>
        <button type="button" className="gs-btn gs-btn-sm text-danger" disabled={del.isPending} onClick={() => onDelete(p)}>{t('common.delete')}</button>
      </div>
    ) },
  ];

  const all = (data ?? []) as ResourcePreset[];
  const match = (p: ResourcePreset) => `${p.name} ${p.kind ?? ''}`.toLowerCase();
  const matched = all.filter((r) => !table.query.trim() || match(r).includes(table.query.trim().toLowerCase()));
  const sorted = sortRows(matched, sortAccessor(columns, table.sort), table.dir);
  const rows = sorted.slice((table.page - 1) * CATALOGUE_PAGE, table.page * CATALOGUE_PAGE);

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <div>
          <h2 className="font-bold">{t('admin.resources.presetsTitle')}</h2>
          <p className="text-muted text-[12px]">{t('admin.resources.presetsNote')}</p>
        </div>
        <Link to="/admin/resources/presets/new" className="gs-btn gs-btn-primary">{t('admin.resources.addPreset')}</Link>
      </div>
      <TableToolbar
        query={table.query}
        onQueryChange={table.setQuery}
        placeholder={t('admin.resources.searchPlaceholder')}
        total={all.length}
        shown={matched.length}
        onClear={table.clear}
      />
      <div className="gs-card">
        {isLoading ? (
          <TableSkeleton rows={4} columns={5} />
        ) : rows.length === 0 ? (
          table.isFiltered
            ? <NoResults query={table.query} onClear={table.clear} />
            : <EmptyState icon="◇" title={t('admin.resources.emptyPresets')} />
        ) : (
          <Table
            caption={t('admin.resources.presetsTitle')}
            columns={columns}
            rows={rows}
            rowKey={(p) => p.id}
            sort={table.sort}
            dir={table.dir}
            onSort={table.toggleSort}
          />
        )}
        <Pagination page={table.page} pageSize={CATALOGUE_PAGE} total={sorted.length} onPage={table.setPage} />
        <p className="text-muted text-[11.5px] mt-3">{t('admin.resources.presetVramNote')}</p>
      </div>
    </div>
  );
}

// Preset editing, at /admin/resources/presets/:presetId/edit.
export function EditPresetPage() {
  const { t } = useTranslation();
  const { presetId = '' } = useParams();
  const preset = usePreset(presetId).data;
  return (
    <div className="w-full">
      <PageHeader
        title={t('admin.resources.editPreset') + (preset ? ' — ' + (preset.name) : '')}
        crumbs={[{ label: t('admin.resources.title'), to: '/admin/resources' }, { label: t('admin.resources.editPreset') }]}
        actions={<BackLink to={'/admin/resources'} />}
      />
      {preset ? <EditPresetForm preset={preset} /> : <p className="text-muted">{t('admin.resources.presetNotFound')}</p>}
    </div>
  );
}

function EditPresetForm({ preset }: { preset: ResourcePreset }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const update = useUpdatePreset();
  const pushToast = useUiStore((s) => s.pushToast);
  const isGpu = preset.kind === 'gpu';
  const [name, setName] = useState(preset.name);
  const [cpu, setCpu] = useState(String(preset.cpu ?? 0));
  const [mem, setMem] = useState(String(preset.mem_gb ?? 0));
  const [disk, setDisk] = useState(String(preset.disk_gb ?? 0));
  const [fracPct, setFracPct] = useState(String(Math.round((preset.gpu_frac ?? 0) * 100)));
  const [cores, setCores] = useState(String(preset.gpu_cores ?? 0));
  const [mode, setMode] = useState<'fractional' | 'exclusive'>(preset.mode === 'exclusive' ? 'exclusive' : 'fractional');

  const submit = () =>
    update.mutate(
      isGpu
        ? { id: preset.id, name: name.trim(), gpu_frac: Number(fracPct) / 100, gpu_cores: mode === 'exclusive' ? 100 : Number(cores), mode }
        : { id: preset.id, name: name.trim(), cpu: Number(cpu), mem_gb: Number(mem), disk_gb: Number(disk) },
      {
        onSuccess: () => { pushToast('success', t('admin.resources.presetUpdated', { name })); navigate('/admin/resources'); },
        onError: (e) => { const m = humanizeError(asApiError(e)); setServerError(m); pushToast('error', m); },
      },
    );


  const [serverError, setServerError] = useState<string | null>(null);
  const guard = useFormGuard();
  return (
    <form className="gs-card" noValidate {...guard.props} onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <div className="grid gap-3">
        <label className="text-[13px] font-semibold">{t('common.name')}<input className="gs-input mt-1 w-full" value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" /></label>
        {isGpu ? (
          <div className="grid grid-cols-3 gap-3">
            <label className="text-[13px] font-semibold">{t('admin.resources.mode')}
              <select className="gs-input mt-1 w-full" value={mode} onChange={(e) => setMode(e.target.value as 'fractional' | 'exclusive')}>
                <option value="fractional">{t('admin.resources.modeSharedOption')}</option>
                <option value="exclusive">{t('admin.resources.modeExclusiveOption')}</option>
              </select>
            </label>
            <label className="text-[13px] font-semibold">{t('admin.resources.vramFraction')}<input className="gs-input mt-1 w-full" type="number" value={mode === 'exclusive' ? '100' : fracPct} disabled={mode === 'exclusive'} onChange={(e) => setFracPct(e.target.value)} min={0} max={1048576} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setFracPct(String(Math.min(1048576, Math.max(0, Number(e.target.value) || 0))))} /></label>
            <label className="text-[13px] font-semibold">{t('admin.resources.cores')}<input className="gs-input mt-1 w-full" type="number" value={mode === 'exclusive' ? '100' : cores} disabled={mode === 'exclusive'} onChange={(e) => setCores(e.target.value)} min={0} max={100} step={1} inputMode="numeric" autoComplete="off" onBlur={(e) => setCores(String(Math.min(100, Math.max(0, Number(e.target.value) || 0))))} /></label>
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-3">
            <label className="text-[13px] font-semibold">CPU<input className="gs-input mt-1 w-full" type="number" value={cpu} onChange={(e) => setCpu(e.target.value)} min={0} max={512} step={1} inputMode="numeric" autoComplete="off" onBlur={(e) => setCpu(String(Math.min(512, Math.max(0, Number(e.target.value) || 0))))} /></label>
            <label className="text-[13px] font-semibold">MEM(GiB)<input className="gs-input mt-1 w-full" type="number" value={mem} onChange={(e) => setMem(e.target.value)} min={0} max={8192} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setMem(String(Math.min(8192, Math.max(0, Number(e.target.value) || 0))))} /></label>
            <label className="text-[13px] font-semibold">DISK(GiB)<input className="gs-input mt-1 w-full" type="number" value={disk} onChange={(e) => setDisk(e.target.value)} min={0} max={65536} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setDisk(String(Math.min(65536, Math.max(0, Number(e.target.value) || 0))))} /></label>
          </div>
        )}
      </div>
      {serverError && <p role="alert" className="text-danger text-[12.5px] mt-3">{serverError}</p>}
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={name.trim() ? [] : [t('common.name')]} />
        <button type="button" className="gs-btn" onClick={() => navigate('/admin/resources')}>{t('common.cancel')}</button>
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!name.trim() || update.isPending}>
          {update.isPending ? t('admin.resources.saving') : t('common.save')}</button>
      </div>
    </form>
  );
}

// New preset, at /admin/resources/presets/new.
export function CreatePresetPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const create = useCreatePreset();
  const pushToast = useUiStore((s) => s.pushToast);
  const [kind, setKind] = useState<'compute' | 'gpu'>('gpu');
  const [name, setName] = useState('');
  const [cpu, setCpu] = useState('8');
  const [memGb, setMemGb] = useState('64');
  const [diskGb, setDiskGb] = useState('200');
  const [fracPct, setFracPct] = useState('25');   // the card fraction, as a percentage
  const [cores, setCores] = useState('25');
  const [mode, setMode] = useState<'fractional' | 'exclusive'>('fractional');

  const valid = name.trim().length > 0 && (kind === 'compute'
    ? Number(cpu) >= 1 && Number(memGb) >= 1
    : Number(fracPct) > 0 && Number(fracPct) <= 100);
  const blockers = [
    !name.trim() && t('common.name'),
    kind === 'compute' && !(Number(cpu) >= 1) && 'CPU',
    kind === 'compute' && !(Number(memGb) >= 1) && 'MEM(GiB)',
    kind !== 'compute' && !(Number(fracPct) > 0 && Number(fracPct) <= 100) && t('admin.resources.gpuFractionBlocker'),
  ].filter(Boolean) as string[];

  const submit = () =>
    create.mutate(
      kind === 'compute'
        ? { name: name.trim(), kind, cpu: Number(cpu), mem_gb: Number(memGb), disk_gb: Number(diskGb) }
        : { name: name.trim(), kind, gpu_frac: Number(fracPct) / 100, gpu_cores: mode === 'exclusive' ? 100 : Number(cores), mode },
      {
        onSuccess: () => { pushToast('success', t('admin.resources.presetCreated', { name })); navigate('/admin/resources'); },
        onError: (e) => { const m = humanizeError(asApiError(e)); setServerError(m); pushToast('error', m); },
      },
    );


  const [serverError, setServerError] = useState<string | null>(null);
  const guard = useFormGuard();
  return (
    <div className="w-full">
      <PageHeader
        title={t('admin.resources.newPreset')}
        crumbs={[{ label: t('admin.resources.title'), to: '/admin/resources' }, { label: t('admin.resources.newPreset') }]}
        actions={<BackLink to={'/admin/resources'} />}
      />
      <form className="gs-card" noValidate {...guard.props} onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <div className="grid gap-3">
        <label className="text-[13px] font-semibold">
          {t('admin.resources.kind')}
          <select className="gs-input mt-1 w-full" value={kind} onChange={(e) => setKind(e.target.value as 'compute' | 'gpu')}>
            <option value="gpu">{t('admin.resources.kindGpuOption')}</option>
            <option value="compute">{t('admin.resources.kindComputeOption')}</option>
          </select>
        </label>
        <label className="text-[13px] font-semibold">
          {t('common.name')}
          <input className="gs-input mt-1 w-full" value={name} onChange={(e) => setName(e.target.value)} placeholder={kind === 'gpu' ? 'GPU M (1/4)' : t('admin.resources.presetNamePlaceholderCompute')} autoComplete="off" />
        </label>
        {kind === 'compute' ? (
          <div className="grid grid-cols-3 gap-3">
            <label className="text-[13px] font-semibold">CPU (core)<input className="gs-input mt-1 w-full" type="number" value={cpu} onChange={(e) => setCpu(e.target.value)} min={0} max={512} step={1} inputMode="numeric" autoComplete="off" onBlur={(e) => setCpu(String(Math.min(512, Math.max(0, Number(e.target.value) || 0))))} /></label>
            <label className="text-[13px] font-semibold">MEM (GiB)<input className="gs-input mt-1 w-full" type="number" value={memGb} onChange={(e) => setMemGb(e.target.value)} min={0} max={8192} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setMemGb(String(Math.min(8192, Math.max(0, Number(e.target.value) || 0))))} /></label>
            <label className="text-[13px] font-semibold">DISK (GiB)<input className="gs-input mt-1 w-full" type="number" value={diskGb} onChange={(e) => setDiskGb(e.target.value)} min={0} max={65536} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setDiskGb(String(Math.min(65536, Math.max(0, Number(e.target.value) || 0))))} /></label>
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-3">
            <label className="text-[13px] font-semibold">{t('admin.resources.mode')}
              <select className="gs-input mt-1 w-full" value={mode} onChange={(e) => setMode(e.target.value as 'fractional' | 'exclusive')}>
                <option value="fractional">{t('admin.resources.modeSharedOption')}</option>
                <option value="exclusive">{t('admin.resources.modeExclusiveOption')}</option>
              </select>
            </label>
            <label className="text-[13px] font-semibold">{t('admin.resources.vramFraction')}<input className="gs-input mt-1 w-full" type="number" value={mode === 'exclusive' ? '100' : fracPct} disabled={mode === 'exclusive'} onChange={(e) => setFracPct(e.target.value)} min={0} max={1048576} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setFracPct(String(Math.min(1048576, Math.max(0, Number(e.target.value) || 0))))} /></label>
            <label className="text-[13px] font-semibold">{t('admin.resources.cores')}<input className="gs-input mt-1 w-full" type="number" value={mode === 'exclusive' ? '100' : cores} disabled={mode === 'exclusive'} onChange={(e) => setCores(e.target.value)} min={0} max={100} step={1} inputMode="numeric" autoComplete="off" onBlur={(e) => setCores(String(Math.min(100, Math.max(0, Number(e.target.value) || 0))))} /></label>
          </div>
        )}
        <p className="text-muted text-[12px]">{t(kind === 'gpu' ? 'admin.resources.presetGpuNote' : 'admin.resources.presetComputeNote')}</p>
      </div>
      {serverError && <p role="alert" className="text-danger text-[12.5px] mt-3">{serverError}</p>}
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={valid ? [] : blockers} />
        <button type="button" className="gs-btn" onClick={() => navigate('/admin/resources')}>{t('common.cancel')}</button>
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!valid || create.isPending}>
          {create.isPending ? t('admin.resources.creating') : t('common.create')}</button>
      </div>
      </form>
    </div>
  );
}

function PolicyTab() {
  const { t } = useTranslation();
  const [scope, setScope] = useState<PolicyScope | ''>('');
  const { data, isLoading } = usePolicies(scope || undefined);
  const del = useDeletePolicy();
  const pushToast = useUiStore((s) => s.pushToast);
  const confirm = useConfirm();

  const onDelete = async (p: ResourcePolicy) => {
    const label = p.scope === 'global'
      ? t('admin.resources.globalPolicy')
      : t('admin.resources.policyLabelSuffix', { scope: scopeLabel(p.scope) });
    const ok = await confirm({
      title: t('admin.resources.confirmDeletePolicyTitle', { label }),
      body: t('admin.resources.confirmDeletePolicy', { label }),
      consequences: [
        p.scope === 'global' ? t('admin.resources.consequenceGlobalPolicy') : t('admin.resources.consequencePolicy'),
      ],
      confirmLabel: t('common.delete'),
      destructive: true,
      // Losing the global policy removes every limit at once.
      confirmText: p.scope === 'global' ? t('admin.resources.globalPolicy') : undefined,
    });
    if (!ok) return;
    del.mutate(p.id, {
      onSuccess: () => pushToast('success', t('admin.resources.policyDeleted')),
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };

  const table = useTableState('pol', { sort: 'name', dir: 'asc' });
  const columns: Column<ResourcePolicy>[] = [
    { key: 'scope', header: t('admin.resources.colScope'), render: (p) => <span className="gs-pill bg-primary-soft text-primary">{scopeLabel(p.scope)}</span> },
    { key: 'scope_id', header: t('admin.resources.colTarget'), render: (p) => p.scope === 'global' ? <span className="text-[12px]">{t('admin.resources.globalTarget')}</span> : <span className="font-mono text-[11px] text-muted">{p.scope_id}</span> },
    { key: 'max_concurrent', header: t('admin.resources.colConcurrent'), render: (p) => p.max_concurrent },
    { key: 'max_queued', header: t('admin.resources.colMaxQueued'), render: (p) => p.max_queued },
    { key: 'cpu', header: t('admin.resources.colMaxCpu'), render: (p) => (p.limits.cpu ? `${p.limits.cpu} vCPU` : '—') },
    { key: 'mem_gb', header: t('admin.resources.colMaxMem'), render: (p) => (p.limits.mem_gb ? `${p.limits.mem_gb} GiB` : '—') },
    { key: 'gpu_mem', header: t('admin.resources.colMaxVram'), render: (p) => formatVram(p.limits.gpu_mem_mb) },
    { key: 'storage_gb', header: t('admin.resources.colMaxStorage'), render: (p) => (p.limits.storage_gb > 0 ? `${p.limits.storage_gb} GiB` : t('admin.resources.unlimited')) },
    { key: 'max_runtime_min', header: t('admin.resources.colMaxRuntime'), render: (p) => (p.max_runtime_min > 0 ? `${Math.round(p.max_runtime_min / 60)}h` : t('admin.resources.unlimited')) },
    { key: 'idle', header: t('admin.resources.colIdleTimeout'), render: (p) => (p.idle_timeout_sec > 0 ? `${Math.round(p.idle_timeout_sec / 60)}${t('admin.resources.minutesSuffix')}` : t('admin.resources.unlimited')) },
    { key: 'actions', header: t('admin.resources.colActions'), render: (p) => (
      <div className="flex flex-nowrap gap-2">
        <Link to={`/admin/resources/policies/${p.id}/edit`} className="gs-btn gs-btn-sm">{t('common.edit')}</Link>
        <button type="button" className="gs-btn gs-btn-sm text-danger" disabled={del.isPending} onClick={() => onDelete(p)}>{t('common.delete')}</button>
      </div>
    ) },
  ];

  const all = (data ?? []) as ResourcePolicy[];
  const match = (p: ResourcePolicy) => `${p.scope} ${p.scope_id ?? ''}`.toLowerCase();
  const matched = all.filter((r) => !table.query.trim() || match(r).includes(table.query.trim().toLowerCase()));
  const sorted = sortRows(matched, sortAccessor(columns, table.sort), table.dir);
  const rows = sorted.slice((table.page - 1) * CATALOGUE_PAGE, table.page * CATALOGUE_PAGE);

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <div>
          <h2 className="font-bold">{t('admin.resources.policyTitle')}</h2>
          <p className="text-muted text-[12px]">{t('admin.resources.policyNote')}</p>
        </div>
        <Link to="/admin/resources/policies/new" className="gs-btn gs-btn-primary">{t('admin.resources.addPolicy')}</Link>
      </div>
      <div className="gs-card mb-4 flex gap-3 flex-wrap items-center">
        <span className="text-[13px] font-semibold text-muted">{t('admin.resources.scopeFilter')}</span>
        <select className="gs-input w-auto" value={scope} onChange={(e) => setScope(e.target.value as PolicyScope | '')}>
          <option value="">{t('common.all')}</option>
          <option value="global">{t('enum.scope.global')}</option>
          <option value="org">{t('enum.scope.org')}</option>
          <option value="group">{t('enum.scope.group')}</option>
          <option value="user">{t('enum.scope.user')}</option>
        </select>
      </div>
      <TableToolbar
        query={table.query}
        onQueryChange={table.setQuery}
        placeholder={t('admin.resources.searchPlaceholder')}
        total={all.length}
        shown={matched.length}
        onClear={table.clear}
      />
      <div className="gs-card">
        {isLoading ? (
          <TableSkeleton rows={4} columns={5} />
        ) : rows.length === 0 ? (
          table.isFiltered
            ? <NoResults query={table.query} onClear={table.clear} />
            : <EmptyState icon="◇" title={t('admin.resources.emptyPolicies')} />
        ) : (
          <Table
            caption={t('admin.resources.policiesTitle')}
            columns={columns}
            rows={rows}
            rowKey={(p) => p.id}
            sort={table.sort}
            dir={table.dir}
            onSort={table.toggleSort}
          />
        )}
        <Pagination page={table.page} pageSize={CATALOGUE_PAGE} total={sorted.length} onPage={table.setPage} />
        <p className="text-muted text-[11.5px] mt-3">{t('admin.resources.policyMergeNote')}</p>
      </div>
    </div>
  );
}

// Policy editing, at /admin/resources/policies/:policyId/edit.
export function EditPolicyPage() {
  const { t } = useTranslation();
  const { policyId = '' } = useParams();
  const policy = usePolicy(policyId).data;
  return (
    <div className="w-full">
      <PageHeader
        title={t('admin.resources.editPolicy') + (policy ? ' — ' + (scopeLabel(policy.scope)) : '')}
        crumbs={[{ label: t('admin.resources.title'), to: '/admin/resources' }, { label: t('admin.resources.editPolicy') }]}
        actions={<BackLink to={'/admin/resources'} />}
      />
      {policy ? <EditPolicyForm policy={policy} /> : <p className="text-muted">{t('admin.resources.policyNotFound')}</p>}
    </div>
  );
}

function EditPolicyForm({ policy }: { policy: ResourcePolicy }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const update = useUpdatePolicy();
  const pushToast = useUiStore((s) => s.pushToast);
  const [maxConcurrent, setMaxConcurrent] = useState(String(policy.max_concurrent ?? 0));
  const [maxQueued, setMaxQueued] = useState(String(policy.max_queued ?? 0));
  const [maxRuntime, setMaxRuntime] = useState(String(policy.max_runtime_min ?? 0));
  const [idle, setIdle] = useState(String(policy.idle_timeout_sec ?? 0));
  const [cpu, setCpu] = useState(String(policy.limits.cpu ?? 0));
  const [memGb, setMemGb] = useState(String(policy.limits.mem_gb ?? 0));
  const [gpuMem, setGpuMem] = useState(String(policy.limits.gpu_mem_mb ?? 0));
  const [gpuCores, setGpuCores] = useState(String(policy.limits.gpu_cores ?? 0));
  const [storageGb, setStorageGb] = useState(String(policy.limits.storage_gb ?? 0));

  const submit = () =>
    update.mutate(
      {
        id: policy.id,
        max_concurrent: Number(maxConcurrent), max_queued: Number(maxQueued),
        max_runtime_min: Number(maxRuntime), idle_timeout_sec: Number(idle),
        limits: { cpu: Number(cpu), mem_gb: Number(memGb), gpu_mem_mb: Number(gpuMem), gpu_cores: Number(gpuCores), storage_gb: Number(storageGb) },
      },
      {
        onSuccess: () => { pushToast('success', t('admin.resources.policyUpdated')); navigate('/admin/resources'); },
        onError: (e) => { const m = humanizeError(asApiError(e)); setServerError(m); pushToast('error', m); },
      },
    );


  const [serverError, setServerError] = useState<string | null>(null);
  const guard = useFormGuard();
  return (
    <form className="gs-card" noValidate {...guard.props} onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <div className="grid grid-cols-2 gap-3">
        <label className="text-[13px] font-semibold">{t('admin.resources.maxConcurrent')}<input className="gs-input mt-1 w-full" type="number" value={maxConcurrent} onChange={(e) => setMaxConcurrent(e.target.value)} min={0} max={1000} step={1} inputMode="numeric" autoComplete="off" onBlur={(e) => setMaxConcurrent(String(Math.min(1000, Math.max(0, Number(e.target.value) || 0))))} /></label>
        <label className="text-[13px] font-semibold">{t('admin.resources.maxQueued')}<input className="gs-input mt-1 w-full" type="number" value={maxQueued} onChange={(e) => setMaxQueued(e.target.value)} min={0} max={1000} step={1} inputMode="numeric" autoComplete="off" onBlur={(e) => setMaxQueued(String(Math.min(1000, Math.max(0, Number(e.target.value) || 0))))} /></label>
        <label className="text-[13px] font-semibold">{t('admin.resources.maxRuntime')}<input className="gs-input mt-1 w-full" type="number" value={maxRuntime} onChange={(e) => setMaxRuntime(e.target.value)} min={0} max={43200} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setMaxRuntime(String(Math.min(43200, Math.max(0, Number(e.target.value) || 0))))} /></label>
        <label className="text-[13px] font-semibold">{t('admin.resources.idleTimeout')}<input className="gs-input mt-1 w-full" type="number" value={idle} onChange={(e) => setIdle(e.target.value)} min={0} max={604800} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setIdle(String(Math.min(604800, Math.max(0, Number(e.target.value) || 0))))} /></label>
        <label className="text-[13px] font-semibold">{t('admin.resources.maxCpuSum')}<input className="gs-input mt-1 w-full" type="number" value={cpu} onChange={(e) => setCpu(e.target.value)} min={0} max={100000} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setCpu(String(Math.min(100000, Math.max(0, Number(e.target.value) || 0))))} /></label>
        <label className="text-[13px] font-semibold">{t('admin.resources.maxMemSum')}<input className="gs-input mt-1 w-full" type="number" value={memGb} onChange={(e) => setMemGb(e.target.value)} min={0} max={1000000} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setMemGb(String(Math.min(1000000, Math.max(0, Number(e.target.value) || 0))))} /></label>
        <label className="text-[13px] font-semibold">{t('admin.resources.maxVramSum')}<input className="gs-input mt-1 w-full" type="number" value={gpuMem} onChange={(e) => setGpuMem(e.target.value)} min={0} max={100000000} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setGpuMem(String(Math.min(100000000, Math.max(0, Number(e.target.value) || 0))))} /></label>
        <label className="text-[13px] font-semibold">{t('admin.resources.maxCoresSum')}<input className="gs-input mt-1 w-full" type="number" value={gpuCores} onChange={(e) => setGpuCores(e.target.value)} min={0} max={100000} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setGpuCores(String(Math.min(100000, Math.max(0, Number(e.target.value) || 0))))} /></label>
        <label className="text-[13px] font-semibold">{t('admin.resources.maxStorageSum')}<input className="gs-input mt-1 w-full" type="number" value={storageGb} onChange={(e) => setStorageGb(e.target.value)} min={0} max={10000000} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setStorageGb(String(Math.min(10000000, Math.max(0, Number(e.target.value) || 0))))} /></label>
      </div>
      {serverError && <p role="alert" className="text-danger text-[12.5px] mt-3">{serverError}</p>}
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={[]} />
        <button type="button" className="gs-btn" onClick={() => navigate('/admin/resources')}>{t('common.cancel')}</button>
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={update.isPending}>
          {update.isPending ? t('admin.resources.saving') : t('common.save')}</button>
      </div>
    </form>
  );
}

// New resource policy, at /admin/resources/policies/new.
export function CreatePolicyPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const create = useCreatePolicy();
  const pushToast = useUiStore((s) => s.pushToast);
  const [scope, setScope] = useState<PolicyScope>('group');
  const [scopeId, setScopeId] = useState('');
  // Offer the candidate targets for the chosen scope — organizations, groups, or users.
  const orgs = useOrganizations().data ?? [];
  const groups = useProjects().data ?? [];
  const users = useUsers({}).data ?? [];
  const isGlobal = scope === 'global';
  const targets =
    scope === 'org' ? orgs.map((o) => ({ id: o.id, label: o.name }))
    : scope === 'group' ? groups.map((g) => ({ id: g.id, label: g.name }))
    : scope === 'user' ? users.map((u) => ({ id: u.id, label: `${u.name} (${u.email})` }))
    : [];
  const effScopeId = isGlobal ? '*' : (scopeId || targets[0]?.id || '');
  const [maxConcurrent, setMaxConcurrent] = useState('3');
  const [maxQueued, setMaxQueued] = useState('5');
  const [maxRuntime, setMaxRuntime] = useState('1440');
  const [idle, setIdle] = useState('1800');
  const [cpu, setCpu] = useState('16');
  const [memGb, setMemGb] = useState('128');
  const [gpuMem, setGpuMem] = useState('48000');
  const [gpuCores, setGpuCores] = useState('300');
  const [storageGb, setStorageGb] = useState('500');

  const valid = isGlobal || effScopeId.length > 0;
  const blockers = valid ? [] : [t('admin.resources.scopeTargetBlocker')];

  const submit = () => {
    setServerError(null);
    create.mutate(
      {
        scope,
        scope_id: effScopeId,
        max_concurrent: Number(maxConcurrent),
        max_queued: Number(maxQueued),
        max_runtime_min: Number(maxRuntime),
        idle_timeout_sec: Number(idle),
        limits: { cpu: Number(cpu), mem_gb: Number(memGb), gpu_mem_mb: Number(gpuMem), gpu_cores: Number(gpuCores), storage_gb: Number(storageGb) },
      },
      {
        onSuccess: () => { pushToast('success', t('admin.resources.policyCreated', { scope, target: scopeId })); navigate('/admin/resources'); },
        onError: (e) => { const m = humanizeError(asApiError(e)); setServerError(m); pushToast('error', m); },
      },
    );
  };


  const [serverError, setServerError] = useState<string | null>(null);
  const guard = useFormGuard();
  return (
    <div className="w-full">
      <PageHeader
        title={t('admin.resources.newPolicy')}
        crumbs={[{ label: t('admin.resources.title'), to: '/admin/resources' }, { label: t('admin.resources.newPolicy') }]}
        actions={<BackLink to={'/admin/resources'} />}
      />
      <form className="gs-card" noValidate {...guard.props} onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <div className="grid gap-3">
        <div className="grid grid-cols-2 gap-3">
          <label className="text-[13px] font-semibold">
            {t('admin.resources.scope')}
            <select className="gs-input mt-1 w-full" value={scope} onChange={(e) => { setScope(e.target.value as PolicyScope); setScopeId(''); }}>
              <option value="global">{t('admin.resources.globalPolicy')}</option>
              <option value="org">{t('enum.scope.org')}</option>
              <option value="group">{t('enum.scope.group')}</option>
              <option value="user">{t('enum.scope.user')}</option>
            </select>
          </label>
          <label className="text-[13px] font-semibold">
            {t('admin.resources.target')}
            {isGlobal ? (
              <div className="gs-input mt-1 w-full text-muted bg-surface-2">{t('admin.resources.globalTarget')}</div>
            ) : (
              <select className="gs-input mt-1 w-full" value={effScopeId} onChange={(e) => setScopeId(e.target.value)}>
                {targets.length === 0 && <option value="">{t('admin.resources.noTarget')}</option>}
                {targets.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
              </select>
            )}
          </label>
          <label className="text-[13px] font-semibold">
            {t('admin.resources.maxConcurrent')}
            <input className="gs-input mt-1 w-full" type="number" value={maxConcurrent} onChange={(e) => setMaxConcurrent(e.target.value)} min={0} max={1000} step={1} inputMode="numeric" autoComplete="off" onBlur={(e) => setMaxConcurrent(String(Math.min(1000, Math.max(0, Number(e.target.value) || 0))))} />
          </label>
          <label className="text-[13px] font-semibold">
            {t('admin.resources.maxQueued')}
            <input className="gs-input mt-1 w-full" type="number" value={maxQueued} onChange={(e) => setMaxQueued(e.target.value)} min={0} max={1000} step={1} inputMode="numeric" autoComplete="off" onBlur={(e) => setMaxQueued(String(Math.min(1000, Math.max(0, Number(e.target.value) || 0))))} />
          </label>
          <label className="text-[13px] font-semibold">
            {t('admin.resources.maxRuntime')}
            <input className="gs-input mt-1 w-full" type="number" value={maxRuntime} onChange={(e) => setMaxRuntime(e.target.value)} min={0} max={43200} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setMaxRuntime(String(Math.min(43200, Math.max(0, Number(e.target.value) || 0))))} />
          </label>
          <label className="text-[13px] font-semibold">
            {t('admin.resources.idleTimeout')}
            <input className="gs-input mt-1 w-full" type="number" value={idle} onChange={(e) => setIdle(e.target.value)} min={0} max={604800} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setIdle(String(Math.min(604800, Math.max(0, Number(e.target.value) || 0))))} />
          </label>
          <label className="text-[13px] font-semibold">
            {t('admin.resources.maxCpuSum')}
            <input className="gs-input mt-1 w-full" type="number" value={cpu} onChange={(e) => setCpu(e.target.value)} min={0} max={100000} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setCpu(String(Math.min(100000, Math.max(0, Number(e.target.value) || 0))))} />
          </label>
          <label className="text-[13px] font-semibold">
            {t('admin.resources.maxMemSum')}
            <input className="gs-input mt-1 w-full" type="number" value={memGb} onChange={(e) => setMemGb(e.target.value)} min={0} max={1000000} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setMemGb(String(Math.min(1000000, Math.max(0, Number(e.target.value) || 0))))} />
          </label>
          <label className="text-[13px] font-semibold">
            {t('admin.resources.maxVramSum')}
            <input className="gs-input mt-1 w-full" type="number" value={gpuMem} onChange={(e) => setGpuMem(e.target.value)} min={0} max={100000000} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setGpuMem(String(Math.min(100000000, Math.max(0, Number(e.target.value) || 0))))} />
          </label>
          <label className="text-[13px] font-semibold">
            {t('admin.resources.maxCoresSum')}
            <input className="gs-input mt-1 w-full" type="number" value={gpuCores} onChange={(e) => setGpuCores(e.target.value)} min={0} max={100000} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setGpuCores(String(Math.min(100000, Math.max(0, Number(e.target.value) || 0))))} />
          </label>
          <label className="text-[13px] font-semibold">
            {t('admin.resources.maxStorageSum')}
            <input className="gs-input mt-1 w-full" type="number" value={storageGb} onChange={(e) => setStorageGb(e.target.value)} min={0} max={10000000} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setStorageGb(String(Math.min(10000000, Math.max(0, Number(e.target.value) || 0))))} />
          </label>
        </div>
        <p className="text-muted text-[12px]">{t('admin.resources.uniqueNote')}</p>
      </div>
      {serverError && <p role="alert" className="text-danger text-[12.5px] mt-3">{serverError}</p>}
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={valid ? [] : blockers} />
        <button type="button" className="gs-btn" onClick={() => navigate('/admin/resources')}>{t('common.cancel')}</button>
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!valid || create.isPending}>
          {create.isPending ? t('admin.resources.creating') : t('common.create')}</button>
      </div>
      </form>
    </div>
  );
}
