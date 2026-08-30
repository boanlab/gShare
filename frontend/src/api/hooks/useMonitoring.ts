import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { Series } from '@/components/TimeSeriesChart';

// Whitelisted metric panels, proxied by gshare-api (the console never sends PromQL).
const raw = api as unknown as {
  GET: (p: string, o?: { params?: { query?: Record<string, unknown> } }) => Promise<{ data?: unknown }>;
};

export type RangeKey = '15m' | '1h' | '6h' | '24h' | '7d';

export interface PanelData { panel: string; unit: string; range?: string; step?: number; series: Series[] }

/** Poll cadence per range: a 15m window is worth refreshing often, a 7d window is not. */
const AUTO_MS: Record<RangeKey, number> = { '15m': 15000, '1h': 30000, '6h': 60000, '24h': 300000, '7d': 900000 };

export function useMonitoringStatus() {
  return useQuery({
    queryKey: ['monitoring', 'status'],
    refetchInterval: 60000,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/monitoring/status');
      return (data ?? { available: false, targets: 0 }) as { available: boolean; targets: number; url?: string };
    },
  });
}

export function usePanel(panel: string, range: RangeKey, node?: string, live = true, gpu?: string) {
  return useQuery({
    queryKey: ['monitoring', 'ts', panel, range, node ?? '', gpu ?? ''],
    refetchInterval: live ? AUTO_MS[range] : false,
    queryFn: async () => {
      const query: Record<string, unknown> = { panel, range };
      if (node) query.node = node;
      if (gpu) query.gpu = gpu;
      const { data } = await raw.GET('/api/v1/monitoring/timeseries', { params: { query } });
      return (data ?? { panel, unit: '', series: [] }) as PanelData;
    },
  });
}

export interface GpuInventoryRow {
  id: string; gpu_uuid?: string | null; model?: string | null; node?: string | null;
  mode?: string | null; desired_mode?: string | null; status?: string | null;
  total_mem_mb?: number | null; used_mem_mb?: number | null; used_cores?: number | null;
  sessions: { id: string; name?: string | null; status: string; gpu_mem_mb?: number | null; gpu_cores?: number | null }[];
}

/** The ledger's per-card view (mode, allocation, holding sessions) — DCGM cannot attribute a
 *  shared card's usage to a session, so attribution comes from the control plane. */
export function useGpuInventory(live = true) {
  return useQuery({
    queryKey: ['monitoring', 'gpu-inventory'],
    refetchInterval: live ? 20000 : false,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/monitoring/gpu-inventory');
      return ((data as { data?: GpuInventoryRow[] } | undefined)?.data ?? []);
    },
  });
}

export function useInstant(panel: string, node?: string, live = true) {
  return useQuery({
    queryKey: ['monitoring', 'instant', panel, node ?? ''],
    refetchInterval: live ? 20000 : false,
    queryFn: async () => {
      const query: Record<string, unknown> = { panel };
      if (node) query.node = node;
      const { data } = await raw.GET('/api/v1/monitoring/instant', { params: { query } });
      return (data ?? { panel, unit: '', series: [] }) as PanelData;
    },
  });
}

// Per-session usage sparklines (monitor detail page): the four measured metrics over a range.
export interface SessionUsageSeries {
  range: RangeKey;
  step: number;
  metrics: Record<'cpu_cores' | 'mem_mib' | 'vram_mib' | 'gpu_core_pct',
    { unit: string; points: [number, number | null][] }>;
}
export function useSessionUsageSeries(id: string | undefined, range: RangeKey) {
  return useQuery({
    queryKey: ['monitoring', 'session-usage-series', id ?? '', range],
    enabled: !!id,
    refetchInterval: AUTO_MS[range],
    queryFn: async () => {
      const { data } = await (api as unknown as {
        GET: (p: string, o?: { params?: { path?: Record<string, string>; query?: Record<string, unknown> } }) => Promise<{ data?: unknown }>;
      }).GET('/api/v1/monitoring/sessions/{session_id}/usage/timeseries', {
        params: { path: { session_id: id as string }, query: { range } },
      });
      return data as SessionUsageSeries;
    },
  });
}
