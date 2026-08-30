import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';

// Node inventory, GPU devices, cordon/uncordon/drain, and registration.
// These use the loose accessor so the response envelopes can be typed locally.
const raw = api as unknown as {
  GET: (path: string, init?: { params?: { query?: Record<string, unknown>; path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
  POST: (path: string, init?: { body?: unknown; params?: { path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
  PUT: (path: string, init?: { body?: unknown; params?: { path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
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
  disk_gb?: number;
  role?: string;
  region: string;
  gpu_mode: GpuMode;
  mode_counts?: Record<string, number>;
  device_count: number;
  // Host compute promised to sessions holding a live allocation on this node's cards.
  alloc_cpu?: number;
  alloc_mem_gb?: number;
  alloc_disk_gb?: number;
  // Sessions currently running on this node (CPU-class included).
  running_sessions?: number;
  heartbeat_at?: string;
  // Node pool membership; null means the node is unassigned and behaves as shared.
  pool_id?: string | null;
  pool_name?: string | null;
}

export interface GpuDevice {
  id: string;
  node_id: string;
  model: string;
  mode: GpuMode;
  // Per-card pool target and drain state (ready | draining | applying | error).
  desired_mode?: GpuMode | null;
  mode_state?: string;
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

// PUT /gpu-devices/{id}/mode — set a card's target pool; placement stops immediately and the
// change applies once the card drains (fractional↔exclusive is metadata-only).
export function useSetDeviceMode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ deviceId, desired_mode }: { deviceId: string; desired_mode: GpuMode | null }) => {
      const { data } = await raw.PUT('/api/v1/gpu-devices/{device_id}/mode', {
        params: { path: { device_id: deviceId } },
        body: { desired_mode },
      });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['gpu-devices'] }),
  });
}

// PUT /gpu-pools/{cluster_id} — set the cluster's MIG/hami-core split by target COUNT; the
// backend drains the emptiest candidates and drives the transitions.
export function useSetPoolTargets() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ clusterId, mig_cards }: { clusterId: string; mig_cards: number }) => {
      const { data } = await raw.PUT('/api/v1/gpu-pools/{cluster_id}', {
        params: { path: { cluster_id: clusterId } },
        body: { mig_cards },
      });
      return data as { moved: string[]; transitioning: number };
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['gpu-devices'] }); qc.invalidateQueries({ queryKey: nodeKeys.all }); },
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
