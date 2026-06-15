import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';

// Node inventory, GPU devices, cordon/uncordon/drain, and registration.
// These use the loose accessor so the response envelopes can be typed locally.
const raw = api as unknown as {
  GET: (path: string, init?: { params?: { query?: Record<string, unknown>; path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
  POST: (path: string, init?: { body?: unknown; params?: { path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
};

export type NodeStatus = 'ready' | 'busy' | 'cordoned' | 'offline';
export type GpuMode = 'exclusive' | 'fractional' | 'mig';

export interface GpuNode {
  id: string;
  hostname: string;
  cluster_id?: string | null;
  cluster_name?: string | null;
  status: NodeStatus;
  cpu: number;
  mem_gb: number;
  region: string;
  gpu_mode: GpuMode;
  device_count: number;
  heartbeat_at?: string;
}

export interface GpuDevice {
  id: string;
  node_id: string;
  model: string;
  mode: GpuMode;
  status: string;
  gpu_uuid: string;
  total_mem_mb: number;
  used_mem_mb: number;
  free_mem_mb: number;
  total_cores: number;
  used_cores: number;
  free_cores: number;
  bound_sessions: { session_id: string; gpu_mem_mb: number; gpu_cores: number }[];
}

export const nodeKeys = {
  all: ['nodes'] as const,
  devices: (nodeId?: string) => ['gpu-devices', nodeId ?? 'all'] as const,
};

// GET /nodes/{id} — one node, for deep links from the drain and device pages.
export function useNode(id?: string) {
  return useQuery({
    queryKey: ['node', id ?? ''],
    enabled: !!id,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/nodes/{node_id}', { params: { path: { node_id: id as string } } });
      return data as GpuNode;
    },
  });
}

// GET /nodes — node.read is super_admin only, so other callers pass enabled=false.
export function useNodes(filter: { status?: NodeStatus; region?: string } = {}, opts?: { enabled?: boolean }) {
  const query: Record<string, unknown> = { page: 1, size: 100 };
  if (filter.status) query.status = filter.status;
  if (filter.region) query.region = filter.region;
  return useQuery({
    queryKey: [...nodeKeys.all, filter],
    enabled: opts?.enabled ?? true,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/nodes', { params: { query } });
      return (data as { data?: GpuNode[] } | undefined)?.data ?? [];
    },
    refetchInterval: 20000,
  });
}

// GET /gpu-devices — the devices on each node, with their VRAM and core occupancy.
export function useGpuDevices(nodeId?: string) {
  const query: Record<string, unknown> = { page: 1, size: 200 };
  if (nodeId) query.node_id = nodeId;
  return useQuery({
    queryKey: nodeKeys.devices(nodeId),
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/gpu-devices', { params: { query } });
      return (data as { data?: GpuDevice[] } | undefined)?.data ?? [];
    },
  });
}

// POST /nodes/{id}/cordon — cordon with true, uncordon with false. Draining is an option.
export function useCordonNode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ nodeId, cordon, reason }: { nodeId: string; cordon: boolean; reason?: string }) => {
      const { data } = await raw.POST('/api/v1/nodes/{node_id}/cordon', {
        params: { path: { node_id: nodeId } },
        body: { cordon, reason },
      });
      return data as GpuNode;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: nodeKeys.all }),
  });
}

// POST /nodes/{id}/drain — drain a node, either rescheduling or force-terminating its sessions.
export function useDrainNode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ nodeId, mode, reason }: { nodeId: string; mode?: 'reschedule' | 'force_terminate'; reason?: string }) => {
      const { data } = await raw.POST('/api/v1/nodes/{node_id}/drain', {
        params: { path: { node_id: nodeId } },
        body: { mode: mode ?? 'reschedule', reason },
      });
      return data as { node_id: string; status: string; drain_id: string; affected_sessions: string[] };
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: nodeKeys.all }),
  });
}
