import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import type { Session } from '@/api/types';
import { api } from '@/api/client';

export const sessionKeys = {
  all: ['sessions'] as const,
  list: (f: Record<string, unknown>) => ['sessions', 'list', f] as const,
  detail: (id: string) => ['sessions', 'detail', id] as const,
};

// A session change moves more than the session list: the dashboard figures, GPU availability,
// the wallet's holds and burn, and the admin monitor all derive from it. Invalidate them together
// so navigating straight from an action to another screen never shows the pre-action state.
export function invalidateSessionAdjacent(qc: QueryClient) {
  qc.invalidateQueries({ queryKey: sessionKeys.all });
  qc.invalidateQueries({ queryKey: ['dashboard'] });
  qc.invalidateQueries({ queryKey: ['gpu-availability'] });
  qc.invalidateQueries({ queryKey: ['wallet'] });
  qc.invalidateQueries({ queryKey: ['monitor'] });
}

// Terminate a session (DELETE /sessions/{id}), from either the list or the detail view. The list is
// refreshed afterwards.
export function useTerminateSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await api.DELETE('/api/v1/sessions/{session_id}', { params: { path: { session_id: id } } });
    },
    onSuccess: () => invalidateSessionAdjacent(qc),
  });
}

// Terminate several sessions at once (POST /sessions/bulk-terminate). Stopping a sweep one row at
// a time is the single most repetitive thing a user does here.
export function useBulkTerminateSessions() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (ids: string[]) => {
      const { data } = await api.POST('/api/v1/sessions/bulk-terminate', { body: { session_ids: ids } });
      return data;
    },
    onSuccess: () => invalidateSessionAdjacent(qc),
  });
}

// Session lifecycle — pause, resume, restart — via POST /sessions/{id}/{stop|start|restart}.
function useSessionAction(action: 'stop' | 'start' | 'restart') {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { data } = await api.POST(`/api/v1/sessions/{session_id}/${action}` as '/api/v1/sessions/{session_id}/stop', {
        params: { path: { session_id: id } },
      });
      return data;
    },
    onSuccess: (_d, id) => {
      qc.invalidateQueries({ queryKey: sessionKeys.detail(id) });
      invalidateSessionAdjacent(qc);
    },
  });
}
export const useStopSession = () => useSessionAction('stop');       // running -> paused; billing stops
export const useStartSession = () => useSessionAction('start');     // paused -> running
export const useRestartSession = () => useSessionAction('restart'); // stop followed by start

export function useSessions(filter: { status?: string; page?: number } = {}) {
  return useQuery({
    queryKey: sessionKeys.list(filter),
    refetchOnMount: 'always',
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/sessions', { params: { query: filter } });
      // Envelope: { data, pagination }. Older callers want the array.
      const env = data as unknown as { data?: Session[] } | Session[] | undefined;
      return Array.isArray(env) ? env : env?.data ?? [];
    },
  });
}

// Session detail: polls while the session is not terminated, as a fallback for a missing SSE
// connection.
export function useSession(id: string) {
  return useQuery({
    queryKey: sessionKeys.detail(id),
    refetchOnMount: 'always',
    // Typed via the Session alias so the detail view sees the fields layered on top of the
    // generated SessionRead (see api/types.ts).
    queryFn: async (): Promise<Session | undefined> => {
      const { data } = await api.GET('/api/v1/sessions/{session_id}', {
        params: { path: { session_id: id } },
      });
      return data;
    },
    refetchInterval: (q) =>
      ['terminated', 'error'].includes(q.state.data?.status ?? '') ? false : 4000,
  });
}

export interface SessionTimelineEvent {
  id: string;
  kind: string;    // created|queued|promoted|preparing|running|paused|resumed|terminated|error
  reason?: string | null;
  message?: string | null;
  at?: string | null;
}

// The lifecycle timeline shown at the foot of the detail screen.
export function useSessionTimeline(id: string) {
  return useQuery({
    queryKey: ['sessions', 'timeline', id],
    refetchInterval: 15000,
    queryFn: async () => {
      const { data } = await (api as unknown as {
        GET: (p: string, o?: object) => Promise<{ data?: { data?: SessionTimelineEvent[] } }>;
      }).GET('/api/v1/sessions/{session_id}/timeline', { params: { path: { session_id: id } } });
      return data?.data ?? [];
    },
  });
}

export function useSessionConnections(id: string, enabled = true) {
  return useQuery({
    queryKey: ['sessions', 'connections', id],
    enabled,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/sessions/{session_id}/connections', {
        params: { path: { session_id: id } },
      });
      return data ?? [];
    },
  });
}

// Measured live usage of the caller's own session (owner or admin): cadvisor CPU/MEM plus
// HAMi per-session VRAM and GPU-core utilisation, for the detail page's usage panel.
export interface OwnSessionUsage {
  cpu_cores: number | null;
  mem_bytes: number | null;
  gpu_core_pct: number | null;
  vram_bytes: number | null;
}
export interface OwnSessionUsageSeries {
  range: string;
  step: number;
  metrics: Record<'cpu_cores' | 'mem_mib' | 'vram_mib' | 'gpu_core_pct',
    { unit: string; points: [number, number | null][] }>;
}
const usageRaw = api as unknown as {
  GET: (p: string, o?: { params?: { path?: Record<string, string>; query?: Record<string, unknown> } }) => Promise<{ data?: unknown }>;
};
export function useOwnSessionUsage(id?: string) {
  return useQuery({
    queryKey: ['session', id ?? '', 'usage'],
    enabled: !!id,
    refetchInterval: 10000,
    queryFn: async () => {
      const { data } = await usageRaw.GET('/api/v1/sessions/{session_id}/usage',
        { params: { path: { session_id: id as string } } });
      return data as OwnSessionUsage;
    },
  });
}
export function useOwnSessionUsageSeries(id: string | undefined, range: string) {
  return useQuery({
    queryKey: ['session', id ?? '', 'usage-series', range],
    enabled: !!id,
    refetchInterval: range === '15m' ? 15000 : 30000,
    queryFn: async () => {
      const { data } = await usageRaw.GET('/api/v1/sessions/{session_id}/usage/timeseries',
        { params: { path: { session_id: id as string }, query: { range } } });
      return data as OwnSessionUsageSeries;
    },
  });
}
