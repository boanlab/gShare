import { useEffect, useRef, useState } from 'react';
import type { Session } from '@/api/types';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, idemKey } from '@/api/client';
import { useAuthStore } from '@/auth/authStore';

// Monitoring for sessions, the queue, and nodes.
// Live updates come from SSE (/sessions/events), with polling of GET /sessions and /queue as the
// fallback.

const BASE = (import.meta.env.VITE_API_BASE as string) ?? '/api/v1';

export const monitorKeys = {
  sessions: (f: object) => ['monitor', 'sessions', f] as const,
  queue: (f: object) => ['monitor', 'queue', f] as const,
  nodes: (f: object) => ['monitor', 'nodes', f] as const,
  cluster: ['monitor', 'cluster'] as const,
};

export interface SessionMonitorFilter {
  status?: string;
  group_id?: string;
  owner_id?: string;
  node_id?: string;
  page?: number;
  size?: number;
}

// GET /sessions — every session within the caller's administrative scope. Polls every 4 seconds
// while SSE is disconnected.
export function useAllSessions(filter: SessionMonitorFilter = {}, livePaused = false) {
  return useQuery({
    queryKey: monitorKeys.sessions(filter),
    queryFn: async () => {
      // The server accepts only status, group_id, page, and size; owner_id and node_id are not
      // supported.
      const { data } = await api.GET('/api/v1/sessions', {
        params: { query: { status: filter.status, group_id: filter.group_id, page: filter.page, size: filter.size, scope: 'all' } },
      });
      const env = data as unknown as { data?: Session[] } | Session[] | undefined;
      return Array.isArray(env) ? env : env?.data ?? [];
    },
    refetchInterval: livePaused ? 4000 : false,
    placeholderData: (prev) => prev,
  });
}

// GET /queue — the whole queue, with priorities and waiting times.
export function useAdminQueue(
  filter: { status?: string; group_id?: string; page?: number; size?: number } = {},
  livePaused = false,
) {
  return useQuery({
    queryKey: monitorKeys.queue(filter),
    queryFn: async () => {
      // The server accepts only group_id, page, and size; status is not supported.
      const { data } = await api.GET('/api/v1/queue', {
        params: { query: { group_id: filter.group_id, page: filter.page, size: filter.size } },
      });
      return data?.data ?? [];
    },
    refetchInterval: livePaused ? 5000 : false,
    placeholderData: (prev) => prev,
  });
}

// GET /nodes — node inventory: status, device count, and heartbeat.
// node.read is super_admin only, so other callers pass enabled=false.
export function useNodes(filter: { status?: string; region?: string; gpu_mode?: string } = {}, opts?: { enabled?: boolean }) {
  return useQuery({
    queryKey: monitorKeys.nodes(filter),
    enabled: opts?.enabled ?? true,
    queryFn: async () => {
      // The server accepts only status and region; gpu_mode is not supported.
      const { data } = await api.GET('/api/v1/nodes', {
        params: { query: { status: filter.status, region: filter.region } },
      });
      return data?.data ?? [];
    },
    refetchInterval: 15000,
    placeholderData: (prev) => prev,
  });
}

// GET /metrics/cluster — cluster-wide aggregates over GPUs, VRAM, nodes, and credits.
// super_admin only.
export function useClusterMetrics(query: { region?: string } = {}, opts?: { enabled?: boolean }) {
  return useQuery({
    queryKey: [...monitorKeys.cluster, query],
    enabled: opts?.enabled ?? true,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/metrics/cluster', { params: { query } });
      return data;
    },
    refetchInterval: 15000,
  });
}

// POST /sessions/{id}/force-terminate — an administrator terminates a session. A reason is
// required, and the session settles normally.
export function useForceTerminate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ sessionId, reason }: { sessionId: string; reason: string }) => {
      const { data } = await api.POST('/api/v1/sessions/{session_id}/force-terminate', {
        params: { path: { session_id: sessionId } },
        // The justification the console requires — recorded in the audit log server-side.
        body: { reason } as never,
        headers: { 'Idempotency-Key': idemKey() },
      });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['monitor'] }),
  });
}

// PATCH /queue/{id} — adjust a queued session's priority, which is the only way to reorder.
export function useSetQueuePriority() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ entryId, priority }: { entryId: string; priority: number }) => {
      const { data } = await api.PATCH('/api/v1/queue/{queue_entry_id}', {
        params: { path: { queue_entry_id: entryId } },
        body: { priority },
      });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['monitor', 'queue'] }),
  });
}

export interface MonitorSseOptions {
  scope?: 'group' | 'org' | 'all';
  scope_id?: string;
  group_id?: string;
}

// Subscribe to the /sessions/events SSE stream, invalidating the monitor queries whenever a session
// or the queue changes. Returns { connected }; when false, the caller falls back to polling.
export function useSessionsStream(opts: MonitorSseOptions = {}) {
  const qc = useQueryClient();
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const optsKey = JSON.stringify(opts);

  useEffect(() => {
    const { accessToken, activeProjectId } = useAuthStore.getState();
    const params = new URLSearchParams();
    if (accessToken) params.set('access_token', accessToken);
    if (opts.scope) params.set('scope', opts.scope);
    if (opts.scope_id) params.set('scope_id', opts.scope_id);
    const pid = opts.group_id ?? activeProjectId;
    if (pid) params.set('group_id', pid);

    const url = `${BASE}/sessions/events?${params.toString()}`;
    const es = new EventSource(url);
    esRef.current = es;

    const refresh = () => {
      qc.invalidateQueries({ queryKey: ['monitor', 'sessions'] });
      qc.invalidateQueries({ queryKey: ['monitor', 'queue'] });
      qc.invalidateQueries({ queryKey: ['sessions'] });
      qc.invalidateQueries({ queryKey: ['dashboard'] });
      qc.invalidateQueries({ queryKey: ['gpu-availability'] });
      qc.invalidateQueries({ queryKey: ['wallet'] });
    };

    es.onopen = () => setConnected(true);
    es.addEventListener('session.status_changed', refresh);
    es.addEventListener('session.assigned', refresh);
    es.addEventListener('queue.updated', refresh);
    es.onmessage = refresh; // an unnamed message triggers a refresh too
    es.onerror = () => {
      setConnected(false); // the caller falls back to polling
    };

    return () => {
      es.close();
      esRef.current = null;
      setConnected(false);
    };
  }, [optsKey, qc]); // eslint-disable-line react-hooks/exhaustive-deps

  return { connected };
}

// GET /monitoring/sessions/{id}/usage — measured live usage for the monitor drawer:
// cadvisor CPU/MEM plus HAMi per-session VRAM and GPU-core utilization.
export interface SessionUsage {
  cpu_cores: number | null;
  mem_bytes: number | null;
  gpu_core_pct: number | null;
  vram_bytes: number | null;
}
export function useSessionUsage(id?: string) {
  return useQuery({
    queryKey: ['monitor', 'session-usage', id ?? ''],
    enabled: !!id,
    refetchInterval: 10000,
    queryFn: async () => {
      const { data } = await (api as unknown as {
        GET: (p: string, i?: { params?: { path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
      }).GET('/api/v1/monitoring/sessions/{session_id}/usage', { params: { path: { session_id: id as string } } });
      return data as SessionUsage;
    },
  });
}
