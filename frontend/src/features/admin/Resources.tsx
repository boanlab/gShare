import { useState } from 'react';
import { Select } from '@/components/Select';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  useGpuAvailability,
  useOfferings,
  useCreateOffering,
  useUpdateOffering,
  useDeleteOffering,
  usePresets,
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
import { EmptyState, NoResults, TableSkeleton, ErrorState } from '@/components/EmptyState';
import { useTableState, sortRows } from '@/hooks/useTableState';
import { useConfirm } from '@/components/ConfirmDialog';
import { Dialog } from '@/components/Dialog';
import { PageHeader } from '@/components/PageHeader';
import { Field, DisabledReason } from '@/components/Field';
import { useFormGuard } from '@/hooks/useUnsavedGuard';
import { useUiStore } from '@/store/uiStore';
import { humanizeError, asApiError } from '@/lib/errors';
import { formatVram, scopeLabel, statusLabel } from '@/lib/format';
import { Plus, Tag } from '@/components/icons';
import { StatusPill } from '@/components/StatusPill';
import { Tabs } from '@/components/Tabs';
import { HelpTip } from '@/components/HelpTip';
import { Timestamp } from '@/components/Timestamp';
import { usePrompt } from '@/components/PromptDialog';
import { useDecideResourceRequest, useResourceRequests } from '@/api/hooks/useResourceRequests';

// Resource settings, as three tabs: offerings, presets, and resource policy (quotas).

type Tab = 'offerings' | 'presets';

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

  return (
    <div>
      <PageHeader
        title={t('admin.resources.title')}
        description={t('admin.resources.subtitle')}
      />

      <Tabs
        ariaLabel={t('admin.resources.title')}
        items={[
          { key: 'offerings', label: t('admin.resources.tabOfferings') },
          { key: 'presets', label: t('admin.resources.tabPresets') },
        ]}
        active={tab}
        onChange={(k) => setTab(k as Tab)}
      />

      {tab === 'offerings' && <OfferingsTab />}
      {tab === 'presets' && <PresetsTab />}
    </div>
  );
}

function OfferingsTab() {
  const { t } = useTranslation();
  const [createOpen, setCreateOpen] = useState(false);
  const [editOffering, setEditOffering] = useState<Offering | null>(null);
  const { data, isLoading, isError, error, refetch } = useOfferings();
  // Which GPU models physically exist in the cluster right now — catalogue-only rows (A100/H100
  // price entries with no hardware) get no tag, so the two kinds read apart at a glance.
  // Fleet-wide: pool grants must not hide a model that physically exists in the cluster.
  const availModels = new Set((useGpuAvailability({ fleet: true }).data ?? []).map((m) => m.gpu_model));
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
    {
      key: 'gpu_model',
      header: t('admin.resources.colModel'),
      render: (o) => o.gpu_model ? (
        <span className="inline-flex items-center gap-1.5 min-w-0">
          <span className="truncate">{o.gpu_model}</span>
          {availModels.has(o.gpu_model) && <span className="gs-tag shrink-0 text-primary">{t('admin.resources.inCluster')}</span>}
        </span>
      ) : <span className="text-muted">{t('admin.resources.cpuPool')}</span>,
    },
    {
      key: 'spec',
      header: t('admin.resources.colVramCores'),
      render: (o) => (o.resource_class === 'cpu' ? `${o.cpu ?? 0} vCPU · ${o.mem_gb ?? 0} GiB` : `${formatVram(o.gpu_mem_mb)} · ${o.gpu_cores}%`),
    },
    {
      key: 'resource_class',
      header: t('admin.resources.colClass'),
      render: (o) =>
        o.resource_class === 'cpu' ? <span className="gs-tag">CPU</span> : <span className="gs-tag">GPU</span>,
    },
    { key: 'credit_per_hour', header: t('admin.resources.colRate'), render: (o) => (o.resource_class === 'cpu' ? t('admin.resources.computeProportional') : `${o.credit_per_hour} C/h`) },
    {
      key: 'min_cuda',
      header: t('admin.resources.colMinCuda'),
      render: (o) => (o.resource_class === 'cpu' ? <span className="text-muted">-</span> : (o.min_cuda ? `≥ ${o.min_cuda}` : <span className="text-muted">{t('admin.resources.noLimit')}</span>)),
    },
    {
      key: 'status',
      header: t('common.status'),
      render: (o) => <StatusPill kind={o.status} label={statusLabel(o.status)} />,
    },
    {
      key: 'actions',
      header: t('admin.resources.colActions'),
      render: (o) => (
        <div className="flex flex-nowrap gap-2">
          <button type="button" className="gs-btn gs-btn-sm" onClick={() => setEditOffering(o)}>{t('common.edit')}</button>
          <button type="button" className="gs-btn gs-btn-sm gs-btn-danger" disabled={del.isPending} onClick={() => onDelete(o)}>{t('common.delete')}</button>
        </div>
      ),
    },
  ];

  // CPU is quota-governed, not billed — the catalogue lists what carries a price. The CPU
  // offering row stays in the DB (admission for CPU sessions references it); when compute
  // billing lands, drop this filter to surface it again.
  const all = ((data ?? []) as Offering[]).filter((o) => o.resource_class !== 'cpu');
  const match = (o: Offering) => `${o.name} ${o.gpu_model ?? ''}`.toLowerCase();
  const matched = all.filter((r) => !table.query.trim() || match(r).includes(table.query.trim().toLowerCase()));
  const sorted = sortRows(matched, sortAccessor(columns, table.sort), table.dir);
  const rows = sorted.slice((table.page - 1) * CATALOGUE_PAGE, table.page * CATALOGUE_PAGE);

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <div>
          <h2 className="font-bold inline-flex items-center gap-1.5">{t('admin.resources.offeringsTitle')}<HelpTip text={t('admin.resources.statusHelp')} /></h2>
          <p className="text-muted text-xs">{t('admin.resources.offeringsNote')}</p>
        </div>
        <button type="button" className="gs-btn gs-btn-primary" onClick={() => setCreateOpen(true)}><Plus size={15} weight="bold" aria-hidden="true" />{t('admin.resources.addOffering')}</button>
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
        {isError ? (
          <ErrorState error={error} onRetry={() => refetch()} />
        ) : isLoading ? (
          <TableSkeleton rows={4} columns={5} />
        ) : rows.length === 0 ? (
          table.isFiltered
            ? <NoResults query={table.query} onClear={table.clear} />
            : <EmptyState icon={<Tag size={26} />} title={t('admin.resources.emptyOfferings')} />
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
        <p className="text-muted text-2xs mt-3">
          {t('admin.resources.billingNote')}
        </p>
      </div>
      <Dialog open={createOpen} wide title={t('admin.resources.newOffering')} onClose={() => setCreateOpen(false)}>
        <CreateOfferingForm onDone={() => setCreateOpen(false)} />
      </Dialog>
      <Dialog open={!!editOffering} wide title={t('admin.resources.editOffering') + (editOffering ? ' - ' + editOffering.name : '')} onClose={() => setEditOffering(null)}>
        {editOffering && <EditOfferingForm offering={editOffering} onDone={() => setEditOffering(null)} />}
      </Dialog>
    </div>
  );
}

function EditOfferingForm({ offering, onDone }: { offering: Offering; onDone: () => void }) {
  const { t } = useTranslation();
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
        onSuccess: () => { guard.clear(); pushToast('success', t('admin.resources.offeringUpdated', { name })); onDone(); },
        onError: (e) => { const m = humanizeError(asApiError(e)); setServerError(m); pushToast('error', m); },
      },
    );


  const [serverError, setServerError] = useState<string | null>(null);
  const guard = useFormGuard();
  return (
    <form className="gs-card" noValidate {...guard.props} onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <div className="grid gap-3">
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm font-semibold">{t('common.name')}<input className="gs-input mt-1 w-full" value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" /></label>
          <label className="text-sm font-semibold">{t('common.status')}
            <Select className="gs-input mt-1 w-full" value={status} onChange={(e) => setStatus(e.target.value as 'active' | 'inactive')}>
              <option value="active">{t('enum.status.active')}</option>
              <option value="inactive">{t('enum.status.inactive')}</option>
            </Select>
          </label>
        </div>
        {!isCpu && (
          <div className="grid grid-cols-2 gap-3">
            <label className="text-sm font-semibold">{t('admin.resources.gpuModel')}<input className="gs-input mt-1 w-full" value={gpuModel} onChange={(e) => setGpuModel(e.target.value)} autoComplete="off" /></label>
            <label className="text-sm font-semibold">VRAM(MB)<input className="gs-input mt-1 w-full" type="number" value={memMb} onChange={(e) => setMemMb(e.target.value)} min={0} max={1048576} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setMemMb(String(Math.min(1048576, Math.max(0, Number(e.target.value) || 0))))} /></label>
            <label className="text-sm font-semibold">{t('admin.resources.cores')}<input className="gs-input mt-1 w-full" type="number" value={cores} onChange={(e) => setCores(e.target.value)} min={0} max={100} step={1} inputMode="numeric" autoComplete="off" onBlur={(e) => setCores(String(Math.min(100, Math.max(0, Number(e.target.value) || 0))))} /></label>
            <label className="text-sm font-semibold">{t('admin.resources.rate')}<input className="gs-input mt-1 w-full" value={cph} onChange={(e) => setCph(e.target.value)} autoComplete="off" />
              <span className="block text-2xs text-muted font-normal mt-1">{t('admin.resources.rateChangeNote')}</span></label>
            <label className="text-sm font-semibold">{t('admin.resources.minCuda')}<input className="gs-input mt-1 w-full" value={minCuda} onChange={(e) => setMinCuda(e.target.value)} placeholder={t('admin.resources.minCudaPlaceholder')} autoComplete="off" /></label>
          </div>
        )}
        <div className="grid grid-cols-3 gap-3">
          <label className="text-sm font-semibold">CPU<input className="gs-input mt-1 w-full" type="number" value={cpu} onChange={(e) => setCpu(e.target.value)} min={0} max={512} step={1} inputMode="numeric" autoComplete="off" onBlur={(e) => setCpu(String(Math.min(512, Math.max(0, Number(e.target.value) || 0))))} /></label>
          <label className="text-sm font-semibold">MEM(GiB)<input className="gs-input mt-1 w-full" type="number" value={mem} onChange={(e) => setMem(e.target.value)} min={0} max={8192} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setMem(String(Math.min(8192, Math.max(0, Number(e.target.value) || 0))))} /></label>
          <label className="text-sm font-semibold">DISK(GiB)<input className="gs-input mt-1 w-full" type="number" value={disk} onChange={(e) => setDisk(e.target.value)} min={0} max={65536} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setDisk(String(Math.min(65536, Math.max(0, Number(e.target.value) || 0))))} /></label>
        </div>
      </div>
      {serverError && <p role="alert" className="text-danger text-xs mt-3">{serverError}</p>}
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={name.trim() ? [] : [t('common.name')]} />
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!name.trim() || update.isPending}>
          {update.isPending ? t('admin.resources.saving') : t('common.save')}</button>
        <button type="button" className="gs-btn" onClick={onDone}>{t('common.cancel')}</button>
      </div>
    </form>
  );
}

// New offering (모달).
function CreateOfferingForm({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
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
      onSuccess: () => { guard.clear(); pushToast('success', t('admin.resources.offeringCreated', { name })); onDone(); },
      onError: (e) => { const m = humanizeError(asApiError(e)); setServerError(m); pushToast('error', m); },
    });
  };


  const [serverError, setServerError] = useState<string | null>(null);
  const guard = useFormGuard();
  return (
      <form className="gs-card" noValidate {...guard.props} onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <div className="grid gap-3">
        <Field label={t('common.name')} required>
          {(ids) => <input {...ids} className="gs-input w-full" value={name} onChange={(e) => setName(e.target.value)} placeholder="rtx4090-8g" autoComplete="off" />}
        </Field>
        <Field label={t('admin.resources.resourceClass')}>
          {(ids) => (
            <Select {...ids} className="gs-input w-full" value={rc} onChange={(e) => setRc(e.target.value as ResourceClass)}>
              <option value="gpu">{t('admin.resources.classGpu')}</option>
              <option value="cpu">{t('admin.resources.classCpu')}</option>
            </Select>
          )}
        </Field>
        {!isCpu && (
          <>
            <Field label={t('admin.resources.gpuModel')} required>
              {(ids) => <input {...ids} className="gs-input w-full" value={gpuModel} onChange={(e) => setGpuModel(e.target.value)} autoComplete="off" />}
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <label className="text-sm font-semibold">
                VRAM (MB)
                <input className="gs-input mt-1 w-full" type="number" value={memMb} onChange={(e) => setMemMb(e.target.value)} min={0} max={1048576} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setMemMb(String(Math.min(1048576, Math.max(0, Number(e.target.value) || 0))))} />
              </label>
              <label className="text-sm font-semibold">
                {t('admin.resources.cores')}
                <input className="gs-input mt-1 w-full" type="number" value={cores} onChange={(e) => setCores(e.target.value)} min={0} max={100} step={1} inputMode="numeric" autoComplete="off" onBlur={(e) => setCores(String(Math.min(100, Math.max(0, Number(e.target.value) || 0))))} />
              </label>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <label className="text-sm font-semibold">
                {t('admin.resources.rate')}
                <input className="gs-input mt-1 w-full" value={cph} onChange={(e) => setCph(e.target.value)} autoComplete="off" />
              </label>
              <label className="text-sm font-semibold">
                {t('admin.resources.minCuda')}
                <input className="gs-input mt-1 w-full" value={minCuda} onChange={(e) => setMinCuda(e.target.value)} placeholder={t('admin.resources.minCudaPlaceholderOptional')} autoComplete="off" />
              </label>
            </div>
          </>
        )}
        {isCpu && <p className="text-muted text-xs">{t('admin.resources.cpuOfferingNote')}</p>}
      </div>
      {serverError && <p role="alert" className="text-danger text-xs mt-3">{serverError}</p>}
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={valid ? [] : blockers} />
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!valid || create.isPending}>
          {create.isPending ? t('admin.resources.creating') : t('common.create')}</button>
        <button type="button" className="gs-btn" onClick={onDone}>{t('common.cancel')}</button>
      </div>
      </form>
  );
}

function PresetsTab() {
  const { t } = useTranslation();
  const [createOpen, setCreateOpen] = useState(false);
  const [editPreset, setEditPreset] = useState<ResourcePreset | null>(null);
  const { data, isLoading, isError, error, refetch } = usePresets();
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
  // Compute and GPU presets are different animals (cpu/mem/disk vs a card fraction), so each kind
  // gets its own section and spec column; the mixed-kind table needed a redundant "kind" tag on
  // every row just to stay readable.
  const actionsCol: Column<ResourcePreset> = {
    key: 'actions', header: t('admin.resources.colActions'), render: (p) => (
      <div className="flex flex-nowrap gap-2">
        <button type="button" className="gs-btn gs-btn-sm" onClick={() => setEditPreset(p)}>{t('common.edit')}</button>
        <button type="button" className="gs-btn gs-btn-sm gs-btn-danger" disabled={del.isPending} onClick={() => onDelete(p)}>{t('common.delete')}</button>
      </div>
    ),
  };
  const computeColumns: Column<ResourcePreset>[] = [
    { key: 'name', header: t('admin.resources.colPreset'), render: (p) => <b>{p.name}</b> },
    { key: 'spec', header: t('admin.resources.colSpec'), render: (p) => (
      <span className="text-xs gs-num">CPU {p.cpu} core · MEM {p.mem_gb} GiB · DISK {p.disk_gb ?? 0} GiB</span>
    ) },
    actionsCol,
  ];
  const gpuColumns: Column<ResourcePreset>[] = [
    { key: 'name', header: t('admin.resources.colPreset'), render: (p) => <b>{p.name}</b> },
    { key: 'spec', header: t('admin.resources.colSpec'), render: (p) => (
      <span className="text-xs">VRAM {p.gpu_frac != null ? t('admin.resources.vramFractionOf', { percent: Math.round(p.gpu_frac * 100) }) : '-'} · {t('admin.resources.cores')} {p.gpu_cores}% · {t(p.mode === 'exclusive' ? 'admin.resources.modeExclusive' : 'admin.resources.modeShared')}</span>
    ) },
    actionsCol,
  ];

  const all = (data ?? []) as ResourcePreset[];
  const match = (p: ResourcePreset) => `${p.name} ${p.kind ?? ''}`.toLowerCase();
  const matched = all.filter((r) => !table.query.trim() || match(r).includes(table.query.trim().toLowerCase()));
  const sorted = sortRows(matched, sortAccessor(computeColumns, table.sort), table.dir);
  const computeRows = sorted.filter((p) => p.kind !== 'gpu');
  const gpuRows = sorted.filter((p) => p.kind === 'gpu');

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <div>
          <h2 className="font-bold">{t('admin.resources.presetsTitle')}</h2>
          <p className="text-muted text-xs">{t('admin.resources.presetsNote')}</p>
        </div>
        <button type="button" className="gs-btn gs-btn-primary" onClick={() => setCreateOpen(true)}><Plus size={15} weight="bold" aria-hidden="true" />{t('admin.resources.addPreset')}</button>
      </div>
      <TableToolbar
        query={table.query}
        onQueryChange={table.setQuery}
        placeholder={t('admin.resources.searchPlaceholder')}
        total={all.length}
        shown={matched.length}
        onClear={table.clear}
      />
      {isError ? (
        <div className="gs-card"><ErrorState error={error} onRetry={() => refetch()} /></div>
      ) : isLoading ? (
        <div className="gs-card"><TableSkeleton rows={4} columns={4} /></div>
      ) : matched.length === 0 ? (
        <div className="gs-card">
          {table.isFiltered
            ? <NoResults query={table.query} onClear={table.clear} />
            : <EmptyState icon={<Tag size={26} />} title={t('admin.resources.emptyPresets')} />}
        </div>
      ) : (
        <div className="space-y-5">
          <section>
            <h3 className="text-sm font-bold mb-2">{t('admin.resources.computePresets')}</h3>
            <div className="gs-card">
              {computeRows.length === 0
                ? <p className="text-muted text-xs">{t('admin.resources.noneOfKind')}</p>
                : <Table caption={t('admin.resources.computePresets')} columns={computeColumns} rows={computeRows} rowKey={(p) => p.id} sort={table.sort} dir={table.dir} onSort={table.toggleSort} />}
            </div>
          </section>
          <section>
            <h3 className="text-sm font-bold mb-2">{t('admin.resources.gpuPresets')}</h3>
            <div className="gs-card">
              {gpuRows.length === 0
                ? <p className="text-muted text-xs">{t('admin.resources.noneOfKind')}</p>
                : <Table caption={t('admin.resources.gpuPresets')} columns={gpuColumns} rows={gpuRows} rowKey={(p) => p.id} sort={table.sort} dir={table.dir} onSort={table.toggleSort} />}
              <p className="text-muted text-2xs mt-3">{t('admin.resources.presetVramNote')}</p>
            </div>
          </section>
        </div>
      )}
      <Dialog open={createOpen} wide title={t('admin.resources.newPreset')} onClose={() => setCreateOpen(false)}>
        <CreatePresetForm onDone={() => setCreateOpen(false)} />
      </Dialog>
      <Dialog open={!!editPreset} wide title={t('admin.resources.editPreset') + (editPreset ? ' - ' + editPreset.name : '')} onClose={() => setEditPreset(null)}>
        {editPreset && <EditPresetForm preset={editPreset} onDone={() => setEditPreset(null)} />}
      </Dialog>
    </div>
  );
}

function EditPresetForm({ preset, onDone }: { preset: ResourcePreset; onDone: () => void }) {
  const { t } = useTranslation();
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
        onSuccess: () => { guard.clear(); pushToast('success', t('admin.resources.presetUpdated', { name })); onDone(); },
        onError: (e) => { const m = humanizeError(asApiError(e)); setServerError(m); pushToast('error', m); },
      },
    );


  const [serverError, setServerError] = useState<string | null>(null);
  const guard = useFormGuard();
  return (
    <form className="gs-card" noValidate {...guard.props} onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <div className="grid gap-3">
        <label className="text-sm font-semibold">{t('common.name')}<input className="gs-input mt-1 w-full" value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" /></label>
        {isGpu ? (
          <div className="grid grid-cols-3 gap-3">
            <label className="text-sm font-semibold">{t('admin.resources.mode')}
              <Select className="gs-input mt-1 w-full" value={mode} onChange={(e) => setMode(e.target.value as 'fractional' | 'exclusive')}>
                <option value="fractional">{t('admin.resources.modeSharedOption')}</option>
                <option value="exclusive">{t('admin.resources.modeExclusiveOption')}</option>
              </Select>
            </label>
            <label className="text-sm font-semibold">{t('admin.resources.vramFraction')}<input className="gs-input mt-1 w-full" type="number" value={mode === 'exclusive' ? '100' : fracPct} disabled={mode === 'exclusive'} onChange={(e) => setFracPct(e.target.value)} min={0} max={1048576} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setFracPct(String(Math.min(1048576, Math.max(0, Number(e.target.value) || 0))))} /></label>
            <label className="text-sm font-semibold">{t('admin.resources.cores')}<input className="gs-input mt-1 w-full" type="number" value={mode === 'exclusive' ? '100' : cores} disabled={mode === 'exclusive'} onChange={(e) => setCores(e.target.value)} min={0} max={100} step={1} inputMode="numeric" autoComplete="off" onBlur={(e) => setCores(String(Math.min(100, Math.max(0, Number(e.target.value) || 0))))} /></label>
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-3">
            <label className="text-sm font-semibold">CPU<input className="gs-input mt-1 w-full" type="number" value={cpu} onChange={(e) => setCpu(e.target.value)} min={0} max={512} step={1} inputMode="numeric" autoComplete="off" onBlur={(e) => setCpu(String(Math.min(512, Math.max(0, Number(e.target.value) || 0))))} /></label>
            <label className="text-sm font-semibold">MEM(GiB)<input className="gs-input mt-1 w-full" type="number" value={mem} onChange={(e) => setMem(e.target.value)} min={0} max={8192} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setMem(String(Math.min(8192, Math.max(0, Number(e.target.value) || 0))))} /></label>
            <label className="text-sm font-semibold">DISK(GiB)<input className="gs-input mt-1 w-full" type="number" value={disk} onChange={(e) => setDisk(e.target.value)} min={0} max={65536} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setDisk(String(Math.min(65536, Math.max(0, Number(e.target.value) || 0))))} /></label>
          </div>
        )}
      </div>
      {serverError && <p role="alert" className="text-danger text-xs mt-3">{serverError}</p>}
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={name.trim() ? [] : [t('common.name')]} />
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!name.trim() || update.isPending}>
          {update.isPending ? t('admin.resources.saving') : t('common.save')}</button>
        <button type="button" className="gs-btn" onClick={onDone}>{t('common.cancel')}</button>
      </div>
    </form>
  );
}

// New preset (모달).
function CreatePresetForm({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
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
        onSuccess: () => { guard.clear(); pushToast('success', t('admin.resources.presetCreated', { name })); onDone(); },
        onError: (e) => { const m = humanizeError(asApiError(e)); setServerError(m); pushToast('error', m); },
      },
    );


  const [serverError, setServerError] = useState<string | null>(null);
  const guard = useFormGuard();
  return (
      <form className="gs-card" noValidate {...guard.props} onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <div className="grid gap-3">
        <Field label={t('admin.resources.kind')}>
          {(ids) => (
            <Select {...ids} className="gs-input w-full" value={kind} onChange={(e) => setKind(e.target.value as 'compute' | 'gpu')}>
              <option value="gpu">{t('admin.resources.kindGpuOption')}</option>
              <option value="compute">{t('admin.resources.kindComputeOption')}</option>
            </Select>
          )}
        </Field>
        <Field label={t('common.name')} required>
          {(ids) => <input {...ids} className="gs-input w-full" value={name} onChange={(e) => setName(e.target.value)} placeholder={kind === 'gpu' ? 'GPU M (1/4)' : t('admin.resources.presetNamePlaceholderCompute')} autoComplete="off" />}
        </Field>
        {kind === 'compute' ? (
          <div className="grid grid-cols-3 gap-3">
            <label className="text-sm font-semibold">CPU (core)<input className="gs-input mt-1 w-full" type="number" value={cpu} onChange={(e) => setCpu(e.target.value)} min={0} max={512} step={1} inputMode="numeric" autoComplete="off" onBlur={(e) => setCpu(String(Math.min(512, Math.max(0, Number(e.target.value) || 0))))} /></label>
            <label className="text-sm font-semibold">MEM (GiB)<input className="gs-input mt-1 w-full" type="number" value={memGb} onChange={(e) => setMemGb(e.target.value)} min={0} max={8192} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setMemGb(String(Math.min(8192, Math.max(0, Number(e.target.value) || 0))))} /></label>
            <label className="text-sm font-semibold">DISK (GiB)<input className="gs-input mt-1 w-full" type="number" value={diskGb} onChange={(e) => setDiskGb(e.target.value)} min={0} max={65536} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setDiskGb(String(Math.min(65536, Math.max(0, Number(e.target.value) || 0))))} /></label>
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-3">
            <label className="text-sm font-semibold">{t('admin.resources.mode')}
              <Select className="gs-input mt-1 w-full" value={mode} onChange={(e) => setMode(e.target.value as 'fractional' | 'exclusive')}>
                <option value="fractional">{t('admin.resources.modeSharedOption')}</option>
                <option value="exclusive">{t('admin.resources.modeExclusiveOption')}</option>
              </Select>
            </label>
            <label className="text-sm font-semibold">{t('admin.resources.vramFraction')}<input className="gs-input mt-1 w-full" type="number" value={mode === 'exclusive' ? '100' : fracPct} disabled={mode === 'exclusive'} onChange={(e) => setFracPct(e.target.value)} min={0} max={1048576} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setFracPct(String(Math.min(1048576, Math.max(0, Number(e.target.value) || 0))))} /></label>
            <label className="text-sm font-semibold">{t('admin.resources.cores')}<input className="gs-input mt-1 w-full" type="number" value={mode === 'exclusive' ? '100' : cores} disabled={mode === 'exclusive'} onChange={(e) => setCores(e.target.value)} min={0} max={100} step={1} inputMode="numeric" autoComplete="off" onBlur={(e) => setCores(String(Math.min(100, Math.max(0, Number(e.target.value) || 0))))} /></label>
          </div>
        )}
        <p className="text-muted text-xs">{t(kind === 'gpu' ? 'admin.resources.presetGpuNote' : 'admin.resources.presetComputeNote')}</p>
      </div>
      {serverError && <p role="alert" className="text-danger text-xs mt-3">{serverError}</p>}
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={valid ? [] : blockers} />
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!valid || create.isPending}>
          {create.isPending ? t('admin.resources.creating') : t('common.create')}</button>
        <button type="button" className="gs-btn" onClick={onDone}>{t('common.cancel')}</button>
      </div>
      </form>
  );
}

// Resource policies got their own page (/admin/policies): three tabs in one screen buried them.
export function AdminPolicies() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const view = params.get('view') === 'requests' ? 'requests' : 'policies';
  const setView = (v: string) => setParams((prev) => {
    const next = new URLSearchParams(prev);
    if (v === 'policies') next.delete('view'); else next.set('view', v);
    return next;
  }, { replace: true });
  const { data: incoming = [] } = useResourceRequests('incoming');
  const pendingCount = incoming.filter((r) => r.status === 'pending').length;
  const [policyOpen, setPolicyOpen] = useState(false);
  const [editPolicy, setEditPolicy] = useState<ResourcePolicy | null>(null);

  return (
    <div>
      <PageHeader
        title={t('admin.resources.tabPolicy')}
        description={t('admin.resources.policyNote')}
        actions={<button type="button" className="gs-btn gs-btn-primary" onClick={() => setPolicyOpen(true)}><Plus size={15} weight="bold" aria-hidden="true" />{t('admin.resources.addPolicy')}</button>}
      />
      <Dialog
        open={policyOpen}
        wide
        title={t('admin.resources.newPolicy')}
        onClose={() => setPolicyOpen(false)}
      >
        <PolicyCreateForm onDone={() => setPolicyOpen(false)} />
      </Dialog>
      <Dialog
        open={!!editPolicy}
        wide
        title={t('admin.resources.editPolicy') + (editPolicy ? ' - ' + scopeLabel(editPolicy.scope) : '')}
        onClose={() => setEditPolicy(null)}
      >
        {editPolicy && <EditPolicyForm policy={editPolicy} onDone={() => setEditPolicy(null)} />}
      </Dialog>
      <Tabs
        ariaLabel={t('admin.resources.tabPolicy')}
        items={[
          { key: 'policies', label: t('admin.resources.tabPolicy') },
          { key: 'requests', label: t('quota.adminTab'), count: pendingCount },
        ]}
        active={view}
        onChange={setView}
      />
      {view === 'policies' ? <PolicyTab onEdit={setEditPolicy} /> : <QuotaRequestsPanel />}
    </div>
  );
}

// Incoming quota-increase requests: approve upserts the requester's user-scope policy.
function QuotaRequestsPanel() {
  const { t } = useTranslation();
  const { data: rows = [], isLoading } = useResourceRequests('incoming');
  const decide = useDecideResourceRequest();
  const prompt = usePrompt();
  const pushToast = useUiStore((s) => s.pushToast);
  const line = (r: { cpu?: number | null; mem_gb?: number | null; storage_gb?: number | null }) =>
    [r.cpu != null ? `CPU ${r.cpu}` : null, r.mem_gb != null ? `MEM ${r.mem_gb}GiB` : null,
     r.storage_gb != null ? `DISK ${r.storage_gb}GB` : null].filter(Boolean).join(' · ');
  const onApprove = (id: string) => decide.mutate({ id, approve: true }, {
    onSuccess: () => pushToast('success', t('quota.approved')),
    onError: (e) => pushToast('error', humanizeError(asApiError(e))),
  });
  const onReject = async (id: string) => {
    const reason = await prompt({ title: t('quota.rejectTitle'), label: t('common.reason'), required: true });
    if (reason == null) return;
    decide.mutate({ id, approve: false, reason }, {
      onSuccess: () => pushToast('success', t('quota.rejected')),
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };
  return (
    <div className="gs-card">
      {isLoading ? <p className="text-muted text-sm">{t('common.loading')}</p>
        : rows.length === 0 ? <p className="text-muted text-sm">{t('quota.noIncoming')}</p>
        : (
          <ul className="divide-y divide-border text-sm">
            {rows.map((r) => (
              <li key={r.id} className="py-2.5 flex items-center gap-3 flex-wrap">
                <Timestamp value={r.created_at} className="gs-num text-xs text-muted shrink-0" />
                <b className="shrink-0">{r.requester_name ?? r.user_id}</b>
                <span className="gs-num">{line(r)}</span>
                <span className="text-muted text-xs truncate max-w-[240px]" title={r.note}>{r.note}</span>
                {r.status === 'pending' ? (
                  <span className="ml-auto flex gap-2">
                    <button type="button" className="gs-btn gs-btn-sm" disabled={decide.isPending} onClick={() => onApprove(r.id)}>{t('common.approve')}</button>
                    <button type="button" className="gs-btn gs-btn-sm gs-btn-danger" disabled={decide.isPending} onClick={() => onReject(r.id)}>{t('common.reject')}</button>
                  </span>
                ) : (
                  <span className="ml-auto"><StatusPill kind={r.status} label={t(`enum.reqStatus.${r.status}`, { defaultValue: r.status })} /></span>
                )}
              </li>
            ))}
          </ul>
        )}
    </div>
  );
}

function PolicyTab({ onEdit }: { onEdit: (p: ResourcePolicy) => void }) {
  const { t } = useTranslation();
  const [scope, setScope] = useState<PolicyScope | ''>('');
  const { data, isLoading, isError, error, refetch } = usePolicies(scope || undefined);
  const del = useDeletePolicy();
  const pushToast = useUiStore((s) => s.pushToast);
  const confirm = useConfirm();
  // Resolve scope_id to a human name (org / group / user); the raw id stays in the tooltip.
  const orgs = useOrganizations().data ?? [];
  const groups = useProjects().data ?? [];
  const users = useUsers({}).data ?? [];
  const targetLabel = (p: ResourcePolicy): string | undefined => {
    if (p.scope === 'org') return orgs.find((o) => o.id === p.scope_id)?.name;
    if (p.scope === 'group') {
      const g = groups.find((x) => x.id === p.scope_id);
      if (!g) return undefined;
      const org = orgs.find((o) => o.id === (g as { org_id?: string }).org_id);
      return org ? `${org.name} / ${g.name}` : g.name;
    }
    if (p.scope === 'user') {
      const u = users.find((x) => x.id === p.scope_id);
      return u ? `${u.name} (${u.email})` : undefined;
    }
    return undefined;
  };

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
    { key: 'scope', header: t('admin.resources.colScope'), render: (p) => <span className="gs-tag">{scopeLabel(p.scope)}</span> },
    { key: 'scope_id', header: t('admin.resources.colTarget'), render: (p) => {
      if (p.scope === 'global') return <span className="text-xs">{t('admin.resources.globalTarget')}</span>;
      const label = targetLabel(p);
      return label
        ? <span className="text-xs" title={p.scope_id}>{label}</span>
        : <span className="font-mono text-2xs text-muted">{p.scope_id}</span>;
    } },
    { key: 'max_concurrent', header: t('admin.resources.colConcurrent'), render: (p) => p.max_concurrent },
    { key: 'max_queued', header: t('admin.resources.colMaxQueued'), render: (p) => p.max_queued },
    { key: 'cpu', header: t('admin.resources.colMaxCpu'), render: (p) => (p.limits.cpu ? `${p.limits.cpu} vCPU` : '-') },
    { key: 'mem_gb', header: t('admin.resources.colMaxMem'), render: (p) => (p.limits.mem_gb ? `${p.limits.mem_gb} GiB` : '-') },
    { key: 'storage_gb', header: t('admin.resources.colMaxStorage'), render: (p) => (p.limits.storage_gb > 0 ? `${p.limits.storage_gb} GiB` : t('admin.resources.unlimited')) },
    { key: 'volume_gb', header: t('admin.resources.colMaxVolume'), render: (p) => ((p.limits.volume_gb ?? 0) > 0 ? `${p.limits.volume_gb} GiB` : t('admin.resources.unlimited')) },
    { key: 'gpu_cores', header: t('admin.resources.colMaxCores'), render: (p) => (p.limits.gpu_cores ? `${p.limits.gpu_cores}%` : '-') },
    { key: 'gpu_mem', header: t('admin.resources.colMaxVram'), render: (p) => formatVram(p.limits.gpu_mem_mb) },
    { key: 'actions', header: t('admin.resources.colActions'), render: (p) => (
      <div className="flex flex-nowrap gap-2">
        <button type="button" className="gs-btn gs-btn-sm" onClick={() => onEdit(p)}>{t('common.edit')}</button>
        <button type="button" className="gs-btn gs-btn-sm gs-btn-danger" disabled={del.isPending} onClick={() => onDelete(p)}>{t('common.delete')}</button>
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
      <div className="gs-card mb-4 flex gap-3 flex-wrap items-center">
        <span className="text-sm font-semibold text-muted">{t('admin.resources.scopeFilter')}</span>
        <Select className="gs-input w-auto" value={scope} onChange={(e) => setScope(e.target.value as PolicyScope | '')}>
          <option value="">{t('common.all')}</option>
          <option value="global">{t('enum.scope.global')}</option>
          <option value="org">{t('enum.scope.org')}</option>
          <option value="group">{t('enum.scope.group')}</option>
          <option value="user">{t('enum.scope.user')}</option>
        </Select>
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
        {isError ? (
          <ErrorState error={error} onRetry={() => refetch()} />
        ) : isLoading ? (
          <TableSkeleton rows={4} columns={5} />
        ) : rows.length === 0 ? (
          table.isFiltered
            ? <NoResults query={table.query} onClear={table.clear} />
            : <EmptyState icon={<Tag size={26} />} title={t('admin.resources.emptyPolicies')} />
        ) : (
          <Table
            caption={t('admin.resources.policyTitle')}
            columns={columns}
            rows={rows}
            rowKey={(p) => p.id}
            sort={table.sort}
            dir={table.dir}
            onSort={table.toggleSort}
          />
        )}
        <Pagination page={table.page} pageSize={CATALOGUE_PAGE} total={sorted.length} onPage={table.setPage} />
        <p className="text-muted text-2xs mt-3">{t('admin.resources.policyMergeNote')}</p>
      </div>
    </div>
  );
}

// Policy editing, at /admin/policies/:policyId/edit.

// A duplicate policy 409 deserves a concrete sentence, not "conflicts with an existing item".
function policyConflictMessage(t: (k: string) => string, e: unknown, scope: PolicyScope | 'global'): string | null {
  const err = asApiError(e);
  if (err.code !== 'conflict') return null;
  const key = scope === 'org' ? 'admin.resources.policyExistsOrg'
    : scope === 'group' ? 'admin.resources.policyExistsGroup'
    : scope === 'user' ? 'admin.resources.policyExistsUser'
    : 'admin.resources.policyExistsGlobal';
  return t(key);
}

export function EditPolicyPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { policyId = '' } = useParams();
  const policy = usePolicy(policyId).data;
  return (
    <div className="w-full max-w-3xl">
      <PageHeader
        title={t('admin.resources.editPolicy') + (policy ? ' - ' + (scopeLabel(policy.scope)) : '')}
        crumbs={[{ label: t('admin.resources.tabPolicy'), to: '/admin/policies' }, { label: t('admin.resources.editPolicy') }]}
      />
      {policy ? <EditPolicyForm policy={policy} onDone={() => navigate('/admin/policies')} /> : <p className="text-muted">{t('admin.resources.policyNotFound')}</p>}
    </div>
  );
}

function EditPolicyForm({ policy, onDone }: { policy: ResourcePolicy; onDone: () => void }) {
  const { t } = useTranslation();
  const update = useUpdatePolicy();
  const pushToast = useUiStore((s) => s.pushToast);
  const [maxConcurrent, setMaxConcurrent] = useState(String(policy.max_concurrent ?? 0));
  const [maxQueued, setMaxQueued] = useState(String(policy.max_queued ?? 0));
  const [maxRuntime, setMaxRuntime] = useState(String(policy.max_runtime_min ?? 0));
  const [idle, setIdle] = useState(String(policy.idle_timeout_sec ?? 0));
  const [cpu, setCpu] = useState(String(policy.limits.cpu ?? 0));
  const [memGb, setMemGb] = useState(String(policy.limits.mem_gb ?? 0));
  // VRAM is stored in MB but edited in GB (48 = one PRO 5000 card); shown to one decimal.
  const [gpuMemGb, setGpuMemGb] = useState(String(Math.round(((policy.limits.gpu_mem_mb ?? 0) / 1024) * 10) / 10));
  const [gpuCores, setGpuCores] = useState(String(policy.limits.gpu_cores ?? 0));
  const [storageGb, setStorageGb] = useState(String(policy.limits.storage_gb ?? 0));
  const [volumeGb, setVolumeGb] = useState(String(policy.limits.volume_gb ?? 0));
  // Whether a tenant holding a dedicated node pool may spill onto the shared pool when its own is
  // full. Absent means yes; a tenant with no dedicated pool always keeps the shared pool.
  const [sharedPool, setSharedPool] = useState(policy.limits.shared_pool !== false);

  const submit = () =>
    update.mutate(
      {
        id: policy.id,
        max_concurrent: Number(maxConcurrent), max_queued: Number(maxQueued),
        max_runtime_min: Number(maxRuntime), idle_timeout_sec: Number(idle),
        limits: { cpu: Number(cpu), mem_gb: Number(memGb), gpu_mem_mb: Math.round(Number(gpuMemGb) * 1024), gpu_cores: Number(gpuCores), storage_gb: Number(storageGb), volume_gb: Number(volumeGb), shared_pool: sharedPool },
      },
      {
        onSuccess: () => { guard.clear(); pushToast('success', t('admin.resources.policyUpdated')); onDone(); },
        onError: (e) => {
          const m = policyConflictMessage(t, e, policy.scope) ?? humanizeError(asApiError(e));
          setServerError(m); pushToast('error', m);
        },
      },
    );


  const [serverError, setServerError] = useState<string | null>(null);
  const guard = useFormGuard();
  return (
    <form className="gs-card" noValidate {...guard.props} onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <div>
        <div className="mb-4">
          <h3 className="gs-form-sec gs-form-sec-lead">{t('admin.resources.secSession')}</h3>
          <div className="grid grid-cols-2 gap-3">
        <label className="text-sm font-semibold">{t('admin.resources.maxConcurrent')}<input className="gs-input mt-1 w-full" type="number" value={maxConcurrent} onChange={(e) => setMaxConcurrent(e.target.value)} min={0} max={1000} step={1} inputMode="numeric" autoComplete="off" onBlur={(e) => setMaxConcurrent(String(Math.min(1000, Math.max(0, Number(e.target.value) || 0))))} /></label>
        <label className="text-sm font-semibold">{t('admin.resources.maxQueued')}<input className="gs-input mt-1 w-full" type="number" value={maxQueued} onChange={(e) => setMaxQueued(e.target.value)} min={0} max={1000} step={1} inputMode="numeric" autoComplete="off" onBlur={(e) => setMaxQueued(String(Math.min(1000, Math.max(0, Number(e.target.value) || 0))))} /></label>
        <label className="text-sm font-semibold">{t('admin.resources.maxRuntime')}<input className="gs-input mt-1 w-full" type="number" value={maxRuntime} onChange={(e) => setMaxRuntime(e.target.value)} min={0} max={43200} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setMaxRuntime(String(Math.min(43200, Math.max(0, Number(e.target.value) || 0))))} /></label>
        <label className="text-sm font-semibold">{t('admin.resources.idleTimeout')}<input className="gs-input mt-1 w-full" type="number" value={idle} onChange={(e) => setIdle(e.target.value)} min={0} max={604800} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setIdle(String(Math.min(604800, Math.max(0, Number(e.target.value) || 0))))} /></label>
          </div>
        </div>
        <div className="mb-4">
          <h3 className="gs-form-sec">{t('admin.resources.secGpu')}</h3>
          <div className="grid grid-cols-2 gap-3">
        <label className="text-sm font-semibold">{t('admin.resources.maxVramSum')}<input className="gs-input mt-1 w-full" type="number" value={gpuMemGb} onChange={(e) => setGpuMemGb(e.target.value)} min={0} max={100000} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setGpuMemGb(String(Math.min(100000, Math.max(0, Number(e.target.value) || 0))))} /></label>
        <label className="text-sm font-semibold">{t('admin.resources.maxCoresSum')}<input className="gs-input mt-1 w-full" type="number" value={gpuCores} onChange={(e) => setGpuCores(e.target.value)} min={0} max={100000} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setGpuCores(String(Math.min(100000, Math.max(0, Number(e.target.value) || 0))))} /></label>
          </div>
        </div>
        <div className="mb-4">
          <h3 className="gs-form-sec">{t('admin.resources.secCompute')}</h3>
          <div className="grid grid-cols-2 gap-3">
        <label className="text-sm font-semibold">{t('admin.resources.maxCpuSum')}<input className="gs-input mt-1 w-full" type="number" value={cpu} onChange={(e) => setCpu(e.target.value)} min={0} max={100000} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setCpu(String(Math.min(100000, Math.max(0, Number(e.target.value) || 0))))} /></label>
        <label className="text-sm font-semibold">{t('admin.resources.maxMemSum')}<input className="gs-input mt-1 w-full" type="number" value={memGb} onChange={(e) => setMemGb(e.target.value)} min={0} max={1000000} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setMemGb(String(Math.min(1000000, Math.max(0, Number(e.target.value) || 0))))} /></label>
        <label className="text-sm font-semibold">{t('admin.resources.maxStorageSum')}<input className="gs-input mt-1 w-full" type="number" value={storageGb} onChange={(e) => setStorageGb(e.target.value)} min={0} max={10000000} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setStorageGb(String(Math.min(10000000, Math.max(0, Number(e.target.value) || 0))))} /></label>
        <label className="text-sm font-semibold">{t('admin.resources.maxVolumeSum')}<input className="gs-input mt-1 w-full" type="number" value={volumeGb} onChange={(e) => setVolumeGb(e.target.value)} min={0} max={10000000} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setVolumeGb(String(Math.min(10000000, Math.max(0, Number(e.target.value) || 0))))} /></label>
          </div>
        </div>
        <div>
          <h3 className="gs-form-sec">{t('admin.resources.secPlacement')}</h3>
        <label className="text-sm font-semibold flex items-center gap-2">
          <input type="checkbox" checked={sharedPool} onChange={(e) => setSharedPool(e.target.checked)} />
          {t('admin.resources.sharedPoolFallback')}
        </label>
        <p className="text-muted text-2xs">{t('admin.resources.sharedPoolFallbackHint')}</p>
        </div>
      </div>
      {serverError && <p role="alert" className="text-danger text-xs mt-3">{serverError}</p>}
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={[]} />
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={update.isPending}>
          {update.isPending ? t('admin.resources.saving') : t('common.save')}</button>
        <button type="button" className="gs-btn" onClick={onDone}>{t('common.cancel')}</button>
      </div>
    </form>
  );
}

// New resource policy, at /admin/policies/new.
function PolicyCreateForm({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const create = useCreatePolicy();
  const pushToast = useUiStore((s) => s.pushToast);
  const [scope, setScope] = useState<PolicyScope>('group');
  const [scopeId, setScopeId] = useState('');
  // Offer the candidate targets for the chosen scope - organizations, groups, or users.
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
  const [gpuMemGb, setGpuMemGb] = useState('48');   // GB; converted to MB on submit
  const [gpuCores, setGpuCores] = useState('300');
  const [storageGb, setStorageGb] = useState('500');
  const [volumeGb, setVolumeGb] = useState('500');
  const [sharedPool, setSharedPool] = useState(true);

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
        limits: { cpu: Number(cpu), mem_gb: Number(memGb), gpu_mem_mb: Math.round(Number(gpuMemGb) * 1024), gpu_cores: Number(gpuCores), storage_gb: Number(storageGb), volume_gb: Number(volumeGb), shared_pool: sharedPool },
      },
      {
        onSuccess: () => { guard.clear(); pushToast('success', t('admin.resources.policyCreated', { scope, target: scopeId })); onDone(); },
        onError: (e) => {
          const m = policyConflictMessage(t, e, scope) ?? humanizeError(asApiError(e));
          setServerError(m); pushToast('error', m);
        },
      },
    );
  };


  const [serverError, setServerError] = useState<string | null>(null);
  const guard = useFormGuard();
  return (
      <form className="gs-card" noValidate {...guard.props} onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <div className="grid gap-3">
        <div>
        <div className="mb-4">
          <h3 className="gs-form-sec gs-form-sec-lead">{t('admin.resources.secTarget')}</h3>
          <div className="grid grid-cols-2 gap-3">
          <label className="text-sm font-semibold">
            {t('admin.resources.scope')}
            <Select className="gs-input mt-1 w-full" value={scope} onChange={(e) => { setScope(e.target.value as PolicyScope); setScopeId(''); }}>
              <option value="global">{t('admin.resources.globalPolicy')}</option>
              <option value="org">{t('enum.scope.org')}</option>
              <option value="group">{t('enum.scope.group')}</option>
              <option value="user">{t('enum.scope.user')}</option>
            </Select>
          </label>
          <label className="text-sm font-semibold">
            {t('admin.resources.target')}
            {isGlobal ? (
              <div className="gs-input mt-1 w-full text-muted bg-surface-2">{t('admin.resources.globalTarget')}</div>
            ) : (
              <Select className="gs-input mt-1 w-full" value={effScopeId} onChange={(e) => setScopeId(e.target.value)}>
                {targets.length === 0 && <option value="">{t('admin.resources.noTarget')}</option>}
                {targets.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
              </Select>
            )}
          </label>
          </div>
        </div>
        <div className="mb-4">
          <h3 className="gs-form-sec">{t('admin.resources.secSession')}</h3>
          <div className="grid grid-cols-2 gap-3">
          <label className="text-sm font-semibold">
            {t('admin.resources.maxConcurrent')}
            <input className="gs-input mt-1 w-full" type="number" value={maxConcurrent} onChange={(e) => setMaxConcurrent(e.target.value)} min={0} max={1000} step={1} inputMode="numeric" autoComplete="off" onBlur={(e) => setMaxConcurrent(String(Math.min(1000, Math.max(0, Number(e.target.value) || 0))))} />
          </label>
          <label className="text-sm font-semibold">
            {t('admin.resources.maxQueued')}
            <input className="gs-input mt-1 w-full" type="number" value={maxQueued} onChange={(e) => setMaxQueued(e.target.value)} min={0} max={1000} step={1} inputMode="numeric" autoComplete="off" onBlur={(e) => setMaxQueued(String(Math.min(1000, Math.max(0, Number(e.target.value) || 0))))} />
          </label>
          <label className="text-sm font-semibold">
            {t('admin.resources.maxRuntime')}
            <input className="gs-input mt-1 w-full" type="number" value={maxRuntime} onChange={(e) => setMaxRuntime(e.target.value)} min={0} max={43200} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setMaxRuntime(String(Math.min(43200, Math.max(0, Number(e.target.value) || 0))))} />
          </label>
          <label className="text-sm font-semibold">
            {t('admin.resources.idleTimeout')}
            <input className="gs-input mt-1 w-full" type="number" value={idle} onChange={(e) => setIdle(e.target.value)} min={0} max={604800} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setIdle(String(Math.min(604800, Math.max(0, Number(e.target.value) || 0))))} />
          </label>
          </div>
        </div>
        <div className="mb-4">
          <h3 className="gs-form-sec">{t('admin.resources.secGpu')}</h3>
          <div className="grid grid-cols-2 gap-3">
          <label className="text-sm font-semibold">
            {t('admin.resources.maxVramSum')}
            <input className="gs-input mt-1 w-full" type="number" value={gpuMemGb} onChange={(e) => setGpuMemGb(e.target.value)} min={0} max={100000} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setGpuMemGb(String(Math.min(100000, Math.max(0, Number(e.target.value) || 0))))} />
          </label>
          <label className="text-sm font-semibold">
            {t('admin.resources.maxCoresSum')}
            <input className="gs-input mt-1 w-full" type="number" value={gpuCores} onChange={(e) => setGpuCores(e.target.value)} min={0} max={100000} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setGpuCores(String(Math.min(100000, Math.max(0, Number(e.target.value) || 0))))} />
          </label>
          </div>
        </div>
        <div className="mb-4">
          <h3 className="gs-form-sec">{t('admin.resources.secCompute')}</h3>
          <div className="grid grid-cols-2 gap-3">
          <label className="text-sm font-semibold">
            {t('admin.resources.maxCpuSum')}
            <input className="gs-input mt-1 w-full" type="number" value={cpu} onChange={(e) => setCpu(e.target.value)} min={0} max={100000} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setCpu(String(Math.min(100000, Math.max(0, Number(e.target.value) || 0))))} />
          </label>
          <label className="text-sm font-semibold">
            {t('admin.resources.maxMemSum')}
            <input className="gs-input mt-1 w-full" type="number" value={memGb} onChange={(e) => setMemGb(e.target.value)} min={0} max={1000000} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setMemGb(String(Math.min(1000000, Math.max(0, Number(e.target.value) || 0))))} />
          </label>
          <label className="text-sm font-semibold">
            {t('admin.resources.maxStorageSum')}
            <input className="gs-input mt-1 w-full" type="number" value={storageGb} onChange={(e) => setStorageGb(e.target.value)} min={0} max={10000000} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setStorageGb(String(Math.min(10000000, Math.max(0, Number(e.target.value) || 0))))} />
          </label>
          <label className="text-sm font-semibold">
            {t('admin.resources.maxVolumeSum')}
            <input className="gs-input mt-1 w-full" type="number" value={volumeGb} onChange={(e) => setVolumeGb(e.target.value)} min={0} max={10000000} step="any" inputMode="numeric" autoComplete="off" onBlur={(e) => setVolumeGb(String(Math.min(10000000, Math.max(0, Number(e.target.value) || 0))))} />
          </label>
          </div>
        </div>
        <div>
          <h3 className="gs-form-sec">{t('admin.resources.secPlacement')}</h3>
          <label className="text-sm font-semibold flex items-center gap-2">
            <input type="checkbox" checked={sharedPool} onChange={(e) => setSharedPool(e.target.checked)} />
            {t('admin.resources.sharedPoolFallback')}
          </label>
          <p className="text-muted text-2xs">{t('admin.resources.sharedPoolFallbackHint')}</p>
        </div>
        </div>
        <p className="text-muted text-xs">{t('admin.resources.uniqueNote')}</p>
      </div>
      {serverError && <p role="alert" className="text-danger text-xs mt-3">{serverError}</p>}
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={valid ? [] : blockers} />
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!valid || create.isPending}>
          {create.isPending ? t('admin.resources.creating') : t('common.create')}</button>
        <button type="button" className="gs-btn" onClick={onDone}>{t('common.cancel')}</button>
      </div>
      </form>
  );
}

// Route wrapper (/admin/policies/new): the same form as a full page, for deep links.
export function CreatePolicyPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  return (
    <div className="w-full max-w-3xl">
      <PageHeader
        title={t('admin.resources.newPolicy')}
        crumbs={[{ label: t('admin.resources.tabPolicy'), to: '/admin/policies' }, { label: t('admin.resources.newPolicy') }]}
      />
      <PolicyCreateForm onDone={() => navigate('/admin/policies')} />
    </div>
  );
}
