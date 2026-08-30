import { useTranslation } from 'react-i18next';
import { Meter, type MeterVariant } from '@/components/Meter';
import { TimeSeriesChart } from '@/components/TimeSeriesChart';
import { HelpTip } from '@/components/HelpTip';
import type { ReactNode } from 'react';

// The one session-usage surface, shared by the admin monitor detail and the user's own session
// page: four measured metrics (cadvisor CPU/MEM, HAMi VRAM/GPU-core), each as a current reading
// over its limit, a pressure bar, and a history line. Plain blocks inside the caller's card —
// the numbers carry the hierarchy, not extra boxes.

export interface UsageLimits {
  isGpu: boolean;
  cpu?: number | null;
  mem_gb?: number | null;
  gpu_mem_mb?: number | null;
  gpu_cores?: number | null;
}
export interface UsageNow {
  cpu_cores: number | null;
  mem_bytes: number | null;
  gpu_core_pct: number | null;
  vram_bytes: number | null;
}
export interface UsageSeries {
  metrics: Record<'cpu_cores' | 'mem_mib' | 'vram_mib' | 'gpu_core_pct',
    { unit: string; points: [number, number | null][] }>;
}
export const USAGE_RANGES = ['15m', '1h', '6h'] as const;
export type UsageRange = (typeof USAGE_RANGES)[number];

function MetricBlock({ label, reading, pct, unit, points, chart = true }: {
  label: ReactNode;
  reading: string;
  pct: number | null;
  unit: string;
  points: [number, number | null][];
  chart?: boolean;
}) {
  const variant: MeterVariant = pct == null ? 'primary' : pct >= 90 ? 'danger' : pct >= 70 ? 'warn' : 'primary';
  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs font-semibold text-muted">{label}</span>
        <span className="gs-num text-sm font-bold whitespace-nowrap">{reading}</span>
      </div>
      <Meter value={pct ?? 0} variant={variant} className="mt-1.5" />
      {chart && (
      <div className="mt-2.5 rounded-ctl border border-border bg-surface-2/40 px-2 pt-2.5 pb-2.5">
        {points.length > 0 ? (
          <TimeSeriesChart
            series={[{ labels: {}, points }]}
            unit={unit}
            height={190}
            seriesLabel={() => (typeof label === 'string' ? label : '')}
            timeOnly
            hideXAxis
          />
        ) : (
          <div className="h-[190px] grid place-items-center text-2xs text-muted">-</div>
        )}
      </div>
      )}
    </div>
  );
}

export function SessionUsagePanel({ limits, usage, series, range, onRange, charts = true }: {
  limits: UsageLimits;
  usage?: UsageNow;
  series?: UsageSeries;
  range: UsageRange;
  onRange: (r: UsageRange) => void;
  /** false = the user detail's lean mode: readings + pressure bars, no history charts. */
  charts?: boolean;
}) {
  const { t } = useTranslation();
  const dash = '-';
  const pctOf = (val: number | null | undefined, limit: number | null | undefined) =>
    val != null && limit ? Math.min(100, (val / limit) * 100) : null;
  const m = series?.metrics;
  const timeBase = m?.cpu_cores.points ?? [];
  const zeroFilled = (pts?: [number, number | null][]): [number, number | null][] => {
    if (pts && pts.length > 0) return pts.map(([t, v]) => [t, v ?? 0]);
    return timeBase.map(([t]) => [t, 0]);
  };
  // Current reading: the instant probe first, else the newest history point, else 0 — the
  // "value / quota" line must never collapse to a bare dash while the quota is known.
  const lastOf = (pts?: [number, number | null][]): number | null => {
    if (!pts) return null;
    for (let i = pts.length - 1; i >= 0; i -= 1) if (pts[i][1] != null) return pts[i][1];
    return null;
  };
  const cpuNow = usage?.cpu_cores ?? lastOf(m?.cpu_cores.points) ?? 0;
  const memGib = usage?.mem_bytes != null ? usage.mem_bytes / 2 ** 30
    : (() => { const v = lastOf(m?.mem_mib.points); return v != null ? v / 1024 : 0; })();
  const vramGib = usage?.vram_bytes != null ? usage.vram_bytes / 2 ** 30
    : (lastOf(m?.vram_mib.points) ?? 0) / 1024;
  const corePct = usage?.gpu_core_pct ?? lastOf(m?.gpu_core_pct.points) ?? 0;
  return (
    <>
      <div className="flex items-center justify-between gap-3 mb-4 flex-wrap">
        <h2 className="font-bold">{t('admin.monitor.usageTitle')}</h2>
        {charts && (
        <div className="flex gap-1" role="group" aria-label={t('admin.monitoring.range')}>
          {USAGE_RANGES.map((r) => (
            <button key={r} type="button" onClick={() => onRange(r)}
              className={`gs-btn gs-btn-sm ${range === r ? 'gs-btn-primary' : ''}`}>
              {t(`admin.monitor.range.${r}`)}
            </button>
          ))}
        </div>
        )}
      </div>
      <div className="grid gap-x-8 gap-y-7 sm:grid-cols-2">
        <MetricBlock
          chart={charts}
          label="CPU"
          reading={limits.cpu != null ? `${cpuNow.toFixed(2)} / ${limits.cpu} vCPU` : dash}
          pct={pctOf(cpuNow, limits.cpu)}
          unit="cores"
          points={m?.cpu_cores.points ?? []}
        />
        <MetricBlock
          chart={charts}
          label={t('admin.monitor.usageMem')}
          reading={limits.mem_gb != null ? `${memGib.toFixed(1)} / ${limits.mem_gb} GiB` : dash}
          pct={pctOf(memGib, limits.mem_gb)}
          unit="mib"
          points={m?.mem_mib.points ?? []}
        />
        {limits.isGpu && limits.gpu_cores != null && (
          <MetricBlock
            chart={charts}
            label={(
              <span className="inline-flex items-center gap-1">
                {t('admin.monitor.usageGpuCore')}
                <HelpTip text={t('admin.monitor.gpuCoreHelp')} />
              </span>
            )}
            reading={`${Math.round(corePct)}%`}
            pct={pctOf(corePct, limits.gpu_cores)}
            unit="percent"
            points={zeroFilled(m?.gpu_core_pct.points)}
          />
        )}
        {limits.isGpu && limits.gpu_mem_mb != null && (
          <MetricBlock
            chart={charts}
            label="VRAM"
            reading={`${vramGib.toFixed(1)} / ${(limits.gpu_mem_mb / 1024).toFixed(0)} GB`}
            pct={pctOf(vramGib, limits.gpu_mem_mb / 1024)}
            unit="mib"
            points={zeroFilled(m?.vram_mib.points)}
          />
        )}
      </div>
      <p className="text-2xs text-muted mt-3">{t('admin.monitor.usageHint')}</p>
    </>
  );
}
