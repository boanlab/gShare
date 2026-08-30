import { useEffect, useMemo, useState } from 'react';
import { Select } from '@/components/Select';
import { SelectMenu } from '@/components/SelectMenu';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router-dom';
import { PageHeader } from '@/components/PageHeader';
import { Tabs } from '@/components/Tabs';
import { Table, type Column } from '@/components/Table';
import { StatusPill } from '@/components/StatusPill';
import {
  ChartLegend, TimeSeriesChart, colorForIndex, formatValue, type Series,
} from '@/components/TimeSeriesChart';
import {
  useGpuInventory, useMonitoringStatus, usePanel, type GpuInventoryRow, type RangeKey,
} from '@/api/hooks/useMonitoring';
import { useNodes } from '@/api/hooks/useNodes';

const RANGES: RangeKey[] = ['15m', '1h', '6h', '24h', '7d'];

const GPU_PANELS = [
  { id: 'gpu_util', h: 190 },
  { id: 'gpu_mem_used', h: 190 },
  { id: 'gpu_temp', h: 160 },
  { id: 'gpu_power', h: 160 },
  { id: 'gpu_sm_clock', h: 160 },
  { id: 'gpu_xid', h: 140 },
] as const;

const HOST_PANELS = [
  { id: 'host_cpu', h: 190 },
  { id: 'host_mem', h: 190 },
  { id: 'host_disk', h: 160 },
  { id: 'host_net_rx', h: 160 },
  { id: 'host_net_tx', h: 160 },
  { id: 'host_pods', h: 140 },
] as const;

const shortModel = (m?: string | null) => (m ?? '').replace(/^NVIDIA\s+/, '');
/** One card, named the way the inventory table names it. */
const gpuLabel = (s: Series) => `${shortModel(s.labels.modelName)} #${Number(s.labels.gpu ?? 0) + 1}`;
const nodeLabel = (s: Series) => s.labels.node ?? '-';

function Panel({ panel, height, range, node, gpu, live, seriesLabel, colorOf }: {
  panel: string; height: number; range: RangeKey; node?: string; gpu?: string; live: boolean;
  seriesLabel: (s: Series, i: number) => string;
  colorOf: (s: Series, i: number) => string;
}) {
  const { t } = useTranslation();
  const { data, isLoading, isError } = usePanel(panel, range, node, live, gpu);
  const series = data?.series ?? [];
  return (
    <section className="gs-panel p-4">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="gs-h2 text-sm">{t(`admin.monitoring.panel.${panel}`)}</h2>
        {series.length > 0 && (
          // The newest sample, so a panel still answers "what is it now" without hovering.
          <span className="gs-num text-2xs text-muted">
            {series.map((s) => formatValue(s.points.at(-1)?.[1], data?.unit ?? '')).join(' · ')}
          </span>
        )}
      </div>
      <div className="mt-2">
        {isError ? (
          <p className="text-danger text-xs">{t('admin.monitoring.panelError')}</p>
        ) : isLoading ? (
          <div className="animate-pulse bg-surface-2 rounded-card" style={{ height }} />
        ) : series.length === 0 ? (
          <p className="text-muted text-xs">{t('admin.monitoring.noSeries')}</p>
        ) : (
          <TimeSeriesChart
            series={series}
            unit={data?.unit ?? ''}
            height={height}
            seriesLabel={seriesLabel}
            colorOf={colorOf}
          />
        )}
      </div>
    </section>
  );
}

// System-wide telemetry: GPU and host metrics from Prometheus, plus the ledger's view of which
// session holds which card. super_admin only (monitoring.read).
export function AdminMonitoring() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') === 'host' ? 'host' : 'gpu';
  const range = (RANGES.includes(params.get('range') as RangeKey) ? params.get('range') : '1h') as RangeKey;
  const node = params.get('node') || undefined;
  const gpu = params.get('gpu') || undefined;
  const [live, setLive] = useState(true);
  const set = (patch: Record<string, string | null>) => setParams((prev) => {
    const next = new URLSearchParams(prev);
    for (const [k, v] of Object.entries(patch)) {
      if (!v) next.delete(k); else next.set(k, v);
    }
    return next;
  }, { replace: true });

  const status = useMonitoringStatus().data;
  const nodesQuery = useNodes();
  // GPU tab: only nodes that actually hold a GPU belong in the picker — DCGM has nothing to say
  // about a CPU/master/storage node. The host tab keeps every node (node-exporter covers them all).
  const allNodes = useMemo(
    () => (nodesQuery.data ?? []) as { id: string; hostname: string; device_count?: number | null }[],
    [nodesQuery.data],
  );
  const nodes = useMemo(
    () => (tab === 'gpu' ? allNodes.filter((n) => (n.device_count ?? 0) > 0) : allNodes),
    [allNodes, tab],
  );
  // A node picked on one tab may not exist on the other (e.g. a CPU node after switching to GPU).
  useEffect(() => {
    if (node && !nodes.some((n) => n.hostname === node)) set({ node: null, gpu: null });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, nodes.length]);
  const inventoryQuery = useGpuInventory(live);
  // Stable identity: the ?? [] fallback allocated a new array on every render, which re-ran every
  // memo below it (and rebuilt the colour map, and with it every chart).
  const inventory = useMemo(() => inventoryQuery.data ?? [], [inventoryQuery.data]);

  // Colour is keyed on identity, not order: a card (or node) keeps its colour across every panel,
  // which is what makes ONE legend for the whole tab correct.
  const gpuColors = useMemo(() => {
    const m: Record<string, string> = {};
    inventory.forEach((d, i) => { if (d.gpu_uuid) m[d.gpu_uuid] = colorForIndex(i); });
    return m;
  }, [inventory]);
  const nodeColors = useMemo(() => {
    const m: Record<string, string> = {};
    [...nodes].sort((a, b) => a.hostname.localeCompare(b.hostname))
      .forEach((n, i) => { m[n.hostname] = colorForIndex(i); });
    return m;
  }, [nodes]);

  // Cascading filter: picking a node narrows the card list; picking a card pins one series.
  const cardsOnNode = useMemo(
    () => inventory.filter((d) => !node || d.node === node),
    [inventory, node],
  );
  const visibleCards = useMemo(
    () => cardsOnNode.filter((d) => !gpu || d.gpu_uuid === gpu),
    [cardsOnNode, gpu],
  );

  const seriesLabel = tab === 'gpu' ? gpuLabel : nodeLabel;
  const colorOf = tab === 'gpu'
    ? (s: Series, i: number) => (s.uuid && gpuColors[s.uuid]) || colorForIndex(i)
    : (s: Series, i: number) => nodeColors[s.labels.node ?? ''] ?? colorForIndex(i);

  // Same query key as the first panel of the tab, so react-query serves it from the same request:
  // the legend is then generated from the very series the charts draw (identical names, identical
  // colours) instead of being re-derived from the inventory.
  const legendSource = usePanel(
    tab === 'gpu' ? 'gpu_util' : 'host_cpu', range, node, live, tab === 'gpu' ? gpu : undefined,
  ).data?.series ?? [];
  const legendItems = legendSource.map((s, i) => ({
    key: `${s.uuid ?? ''}${s.labels.node ?? ''}${i}`,
    label: tab === 'gpu' ? `${gpuLabel(s)} · ${s.labels.node ?? ''}` : nodeLabel(s),
    color: colorOf(s, i),
  }));

  const invCols: Column<GpuInventoryRow>[] = [
    { key: 'model', header: t('admin.monitoring.colCard'), render: (r) => (
      <span className="inline-flex items-center gap-1.5">
        <span className="w-2.5 h-[3px] rounded-tag shrink-0"
          style={{ background: (r.gpu_uuid && gpuColors[r.gpu_uuid]) || 'transparent' }} aria-hidden="true" />
        <b>{shortModel(r.model)}</b>
        <span className="text-muted text-2xs font-mono">{(r.gpu_uuid ?? '').slice(4, 12)}</span>
      </span>
    ) },
    { key: 'node', header: t('admin.monitoring.colNode'), render: (r) => <span className="text-muted">{r.node ?? '-'}</span> },
    { key: 'mode', header: t('admin.monitoring.colMode'), render: (r) => t(`enum.gpuMode.${r.mode}`, { defaultValue: r.mode ?? '-' }) },
    { key: 'vram', header: t('admin.monitoring.colAllocVram'), align: 'right', render: (r) => (
      <span className="gs-num">{((r.used_mem_mb ?? 0) / 1024).toFixed(1)} / {((r.total_mem_mb ?? 0) / 1024).toFixed(0)} GiB</span>
    ) },
    { key: 'cores', header: t('admin.monitoring.colAllocCores'), align: 'right', render: (r) => <span className="gs-num">{r.used_cores ?? 0}%</span> },
    { key: 'status', header: t('common.status'), render: (r) => (
      <StatusPill kind={r.status ?? 'unknown'} label={t(`enum.status.${r.status}`, { defaultValue: r.status ?? '-' })} />
    ) },
    { key: 'sessions', header: t('admin.monitoring.colSessions'), render: (r) => (
      r.sessions.length === 0 ? <span className="text-muted">-</span> : (
        <span className="inline-flex flex-wrap gap-1">
          {r.sessions.map((s) => (
            <span key={s.id} className="gs-tag" title={`${s.gpu_mem_mb ?? 0}MiB · ${s.gpu_cores ?? 0}%`}>
              {s.name ?? s.id}
            </span>
          ))}
        </span>
      )
    ) },
  ];

  const panels = tab === 'gpu' ? GPU_PANELS : HOST_PANELS;

  return (
    <div>
      <PageHeader title={t('admin.monitoring.title')} description={t('admin.monitoring.subtitle')} />

      {status && !status.available && (
        <p role="status" className="gs-card mb-4 border-warn text-sm">{t('admin.monitoring.unavailable')}</p>
      )}

      {/* Controls sit ABOVE the tabs so a range or a node survives switching GPU <-> host. */}
      <div className="gs-card mb-4 flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="text-xs font-semibold">{t('admin.monitoring.range')}</span>
        <div className="flex gap-1">
          {RANGES.map((r) => (
            <button key={r} type="button" onClick={() => set({ range: r === '1h' ? null : r })}
              className={`gs-btn gs-btn-sm ${r === range ? 'gs-btn-primary' : ''}`}>
              {r}
            </button>
          ))}
        </div>
        <label className="inline-flex items-center gap-2 text-xs text-muted">
          {t('admin.monitoring.node')}
          <Select className="gs-input w-auto" value={node ?? ''}
            onChange={(e) => set({ node: e.target.value || null, gpu: null })}>
            <option value="">{t('admin.monitoring.allNodes')}</option>
            {nodes.map((n) => <option key={n.id} value={n.hostname}>{n.hostname}</option>)}
          </Select>
        </label>
        {tab === 'gpu' && (
          <span className="inline-flex items-center gap-2 text-xs text-muted">
            {t('admin.monitoring.gpu')}
            <SelectMenu
              ariaLabel={t('admin.monitoring.gpu')}
              value={gpu ?? ''}
              disabled={cardsOnNode.length === 0}
              onChange={(v) => set({ gpu: v || null })}
              options={[
                { value: '', label: t('admin.monitoring.allGpus') },
                ...cardsOnNode.map((d) => ({
                  value: d.gpu_uuid ?? '',
                  label: shortModel(d.model),
                  hint: `${d.node ?? '-'} · ${(d.gpu_uuid ?? '').slice(4, 12)}`,
                })),
              ]}
            />
          </span>
        )}
        <label className="inline-flex items-center gap-2 text-xs text-muted ml-auto">
          <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} />
          {t('admin.monitoring.autoRefresh')}
        </label>
        {status?.available && (
          <span className="text-2xs text-muted">{t('admin.monitoring.targets', { count: status.targets })}</span>
        )}
      </div>

      <Tabs
        ariaLabel={t('admin.monitoring.title')}
        items={[
          { key: 'gpu', label: t('admin.monitoring.tabGpu') },
          { key: 'host', label: t('admin.monitoring.tabHost') },
        ]}
        active={tab}
        onChange={(v) => set({ tab: v === 'gpu' ? null : v })}
      />

      {/* One legend for the whole tab: every panel shares these series and these colours. */}
      {legendItems.length > 0 && (
        <div className="gs-card mt-4 py-2.5">
          <ChartLegend items={legendItems} />
        </div>
      )}

      <div className="grid xl:grid-cols-2 gap-4 mt-4">
        {panels.map((p) => (
          <Panel key={p.id} panel={p.id} height={p.h} range={range} node={node}
            gpu={tab === 'gpu' ? gpu : undefined} live={live}
            seriesLabel={seriesLabel} colorOf={colorOf} />
        ))}
      </div>

      {tab === 'gpu' && (
        <div className="gs-card mt-4">
          <h2 className="font-bold">{t('admin.monitoring.inventoryTitle')}</h2>
          <p className="gs-sub mt-1 mb-3">{t('admin.monitoring.inventoryNote')}</p>
          <Table columns={invCols} rows={visibleCards} rowKey={(r) => r.id} empty={t('admin.gpus.empty')} />
        </div>
      )}
    </div>
  );
}
