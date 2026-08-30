import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';

// Aggregates for the admin console: cluster-wide KPIs plus the supporting dashboard cards.
// schema.d.ts has no types for the admin paths, so these use the loose accessor.
const raw = api as unknown as {
  GET: (path: string, init?: { params?: { query?: Record<string, unknown> } }) => Promise<{ data?: unknown }>;
};

export interface ClusterMetrics {
  as_of: string;
  nodes: { total: number; ready: number; busy: number; cordoned: number; offline: number };
  gpu: {
    device_total: number;
    vram_total_mb: number;
    vram_used_mb: number;
    vram_load_pct: number;
    avg_utilization_pct: number;
    empty_gpu_count: number;
  };
  sessions: { running: number; queued: number };
  credit: { consumed_last_24h: string; active_holds: string };
  // Fleet host compute: promised to active sessions vs node capacity (quota-governed, not billed).
  compute?: {
    cpu: { used: number; total: number };
    mem_gb: { used: number; total: number };
    disk_gb: { used: number; total: number };
  };
}

// Mirrors GET /dashboard/summary (see backend app/api/schemas/dashboard.py) — the same endpoint
// the user dashboard reads. Fields here must exist in the API; a stale shape renders as zeros.
export interface DashboardSummary {
  credit: { available: number | null; balance: number | null; reserved: number | null };
  sessions: { running: number; active: number };
  vram: { used_mb: number; total_mb: number };
  regions: { model: string; total: number; free: number }[];
  allocation: { instances: { used: number; total: number }; vram: { used_mb: number; total_mb: number } };
  compute: {
    cpu: { used: number; limit?: number | null };
    mem_gb: { used: number; limit?: number | null };
    disk_gb: { used: number; limit?: number | null };
  };
}

// GET /metrics/cluster — cluster-wide KPIs. node.read is super_admin only, so anyone else would
// get a 403; enabled=false stops the call being made at all.
export function useClusterMetrics(query: { region?: string } = {}, opts?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ['metrics', 'cluster', query],
    enabled: opts?.enabled ?? true,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/metrics/cluster', { params: { query } });
      return data as ClusterMetrics;
    },
    refetchInterval: 15000,
  });
}

// GET /dashboard/summary — aggregates for the active context, feeding the credit-usage cards.
export function useDashboardSummary() {
  return useQuery({
    queryKey: ['dashboard', 'summary'],
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/dashboard/summary');
      return data as DashboardSummary;
    },
  });
}
