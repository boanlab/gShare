import { useMemo, useState } from 'react';
import { Select } from '@/components/Select';
import { useTranslation } from 'react-i18next';
import { useGpuDevices, useNodes, useSetDeviceMode, type GpuMode } from '@/api/hooks/useNodes';
import { PageHeader } from '@/components/PageHeader';
import { Table, type Column } from '@/components/Table';
import { StatusPill } from '@/components/StatusPill';
import { useUiStore } from '@/store/uiStore';
import { humanizeError, asApiError } from '@/lib/errors';

interface DeviceRow {
  id: string;
  gpu_uuid?: string | null;
  node_id?: string | null;
  model?: string | null;
  mode?: string | null;
  desired_mode?: string | null;
  mode_state?: string | null;
  status?: string | null;
  total_mem_mb?: number | null;
  used_mem_mb?: number | null;
  used_cores?: number | null;
  session_count?: number | null;
}

const MODES: GpuMode[] = ['fractional', 'exclusive', 'mig'];

// Card-level GPU administration: every physical card in the cluster, its pool/mode, occupancy,
// and the per-card target-pool control (drain-then-switch is driven by the backend).
export function AdminGpus() {
  const { t } = useTranslation();
  const pushToast = useUiStore((s) => s.pushToast);
  const devices = (useGpuDevices().data ?? []) as DeviceRow[];
  const nodes = useNodes().data ?? [];
  const setMode = useSetDeviceMode();
  const [modelFilter, setModelFilter] = useState('');

  const nodeName = useMemo(() => {
    const m: Record<string, string> = {};
    for (const n of nodes as { id: string; hostname: string }[]) m[n.id] = n.hostname;
    return m;
  }, [nodes]);
  const models = useMemo(() => [...new Set(devices.map((d) => d.model ?? '-'))], [devices]);
  const rows = modelFilter ? devices.filter((d) => (d.model ?? '-') === modelFilter) : devices;

  const modeLabel = (m?: string | null) => (m ? t(`enum.gpuMode.${m}`, { defaultValue: m }) : '-');
  const onMode = (d: DeviceRow, mode: GpuMode) => {
    if (mode === (d.desired_mode ?? d.mode)) return;
    setMode.mutate({ deviceId: d.id, desired_mode: mode }, {
      onSuccess: () => pushToast('success', t('admin.gpus.modeQueued', { mode: modeLabel(mode) })),
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };

  const columns: Column<DeviceRow>[] = [
    {
      key: 'model', header: t('admin.gpus.colModel'), sortBy: (d) => d.model ?? '',
      render: (d) => <b>{d.model ?? '-'}</b>,
    },
    {
      key: 'node', header: t('admin.gpus.colNode'), sortBy: (d) => nodeName[d.node_id ?? ''] ?? '',
      render: (d) => <span className="text-muted">{nodeName[d.node_id ?? ''] ?? d.node_id ?? '-'}</span>,
    },
    {
      key: 'mode', header: t('admin.gpus.colMode'), sortBy: (d) => d.mode ?? '',
      render: (d) => (
        <span className="inline-flex items-center gap-1.5">
          {modeLabel(d.mode)}
          {d.desired_mode && d.desired_mode !== d.mode && (
            <span className="gs-tag text-warn" title={t('admin.gpus.transitionHint')}>
              → {modeLabel(d.desired_mode)}
            </span>
          )}
        </span>
      ),
    },
    {
      key: 'vram', header: t('admin.gpus.colVram'), align: 'right',
      sortBy: (d) => (d.used_mem_mb ?? 0) / Math.max(1, d.total_mem_mb ?? 1),
      render: (d) => (
        <span className="gs-num">
          {((d.used_mem_mb ?? 0) / 1024).toFixed(1)} / {((d.total_mem_mb ?? 0) / 1024).toFixed(0)} GiB
        </span>
      ),
    },
    {
      key: 'cores', header: t('admin.gpus.colCores'), align: 'right', hideOnMobile: true,
      sortBy: (d) => d.used_cores ?? 0,
      render: (d) => <span className="gs-num">{d.used_cores ?? 0}%</span>,
    },
    {
      key: 'state', header: t('common.status'),
      render: (d) => (
        <span className="inline-flex items-center gap-1.5">
          <StatusPill kind={d.status ?? 'unknown'} label={t(`enum.status.${d.status}`, { defaultValue: d.status ?? '-' })} />
          {d.mode_state && d.mode_state !== 'ready' && (
            <span className="gs-tag text-warn">{t(`enum.modeState.${d.mode_state}`, { defaultValue: d.mode_state })}</span>
          )}
        </span>
      ),
    },
    {
      key: 'target', header: t('admin.gpus.colTarget'), align: 'right',
      render: (d) => (
        <Select
          className="gs-input w-auto text-xs py-1"
          value={d.desired_mode ?? d.mode ?? 'fractional'}
          disabled={setMode.isPending}
          aria-label={t('admin.gpus.colTarget')}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => onMode(d, e.target.value as GpuMode)}
        >
          {MODES.map((m) => <option key={m} value={m}>{modeLabel(m)}</option>)}
        </Select>
      ),
    },
  ];

  return (
    <div>
      <PageHeader title={t('admin.gpus.title')} description={t('admin.gpus.subtitle')} />
      <div className="gs-card">
        <div className="flex items-center gap-3 mb-3 flex-wrap">
          <Select className="gs-input w-auto" value={modelFilter} aria-label={t('admin.gpus.colModel')}
            onChange={(e) => setModelFilter(e.target.value)}>
            <option value="">{t('admin.gpus.allModels')}</option>
            {models.map((m) => <option key={m} value={m}>{m}</option>)}
          </Select>
          <span className="text-muted text-xs ml-auto">{t('admin.gpus.total', { count: rows.length })}</span>
        </div>
        <Table columns={columns} rows={rows} rowKey={(d) => d.id} empty={t('admin.gpus.empty')} />
      </div>
    </div>
  );
}
