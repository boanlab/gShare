import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, idemKey } from '@/api/client';
import { nodeKeys } from '@/api/hooks/useNodes';

// Node pools: a named set of nodes in one cluster, either shared (usable by everyone) or dedicated
// (usable only by the organizations / groups holding a grant). Placement prefers a tenant's
// group-granted pool, then its org-granted pool, then the shared pool. These endpoints are not
// yet in the generated types, so they use the loose accessor and type their envelopes locally.
const raw = api as unknown as {
  GET: (path: string, init?: { params?: { query?: Record<string, unknown>; path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
  POST: (path: string, init?: { body?: unknown; params?: { path?: Record<string, string> }; headers?: Record<string, string> }) => Promise<{ data?: unknown }>;
  PATCH: (path: string, init?: { body?: unknown; params?: { path?: Record<string, string> }; headers?: Record<string, string> }) => Promise<{ data?: unknown }>;
  PUT: (path: string, init?: { body?: unknown; params?: { path?: Record<string, string> }; headers?: Record<string, string> }) => Promise<{ data?: unknown }>;
  DELETE: (path: string, init?: { params?: { path?: Record<string, string> }; headers?: Record<string, string> }) => Promise<{ data?: unknown }>;
};

export type PoolKind = 'shared' | 'dedicated';
export type PoolGrantScope = 'org' | 'group';

export interface NodePoolNode {
  id: string;
  hostname: string;
  status: string;
  device_count: number;
}

export interface NodePoolGrant {
  id: string;
  scope: PoolGrantScope;
  scope_id: string;
  /** Display name of the organization or group the grant targets. */
  name: string;
  created_at: string;
}

export interface NodePool {
  id: string;
  cluster_id: string;
  cluster_name?: string | null;
  name: string;
  description?: string | null;
  kind: PoolKind;
  node_count: number;
  nodes: NodePoolNode[];
  grants: NodePoolGrant[];
}

export const nodePoolKeys = {
  all: ['node-pools'] as const,
  list: (clusterId?: string) => ['node-pools', clusterId ?? 'all'] as const,
};

// GET /admin/node-pools — pool.read: super_admin sees every pool; an org_admin sees only the pools
// carrying a grant for one of their organizations (or a group in them).
export function useNodePools(clusterId?: string, opts?: { enabled?: boolean }) {
  const query: Record<string, unknown> = {};
  if (clusterId) query.cluster_id = clusterId;
  return useQuery({
    queryKey: nodePoolKeys.list(clusterId),
    enabled: opts?.enabled ?? true,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/node-pools', { params: { query } });
      return (data as { data?: NodePool[] } | undefined)?.data ?? [];
    },
  });
}

function invalidatePoolsAndNodes(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: nodePoolKeys.all });
  qc.invalidateQueries({ queryKey: nodeKeys.all });
  qc.invalidateQueries({ queryKey: ['node'] });
}

// POST /admin/node-pools — pool.manage (super_admin). (cluster_id, name) is unique.
export function useCreateNodePool() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { cluster_id: string; name: string; description?: string; kind: PoolKind }) => {
      const { data } = await raw.POST('/api/v1/node-pools', { body, headers: { 'Idempotency-Key': idemKey() } });
      return data as NodePool;
    },
    onSuccess: () => invalidatePoolsAndNodes(qc),
  });
}

// PATCH /admin/node-pools/{id} — rename, describe, or switch kind. pool.manage.
export function useUpdateNodePool() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...body }: { id: string; name?: string; description?: string; kind?: PoolKind }) => {
      const { data } = await raw.PATCH('/api/v1/node-pools/{pool_id}', {
        params: { path: { pool_id: id } },
        body,
        headers: { 'Idempotency-Key': idemKey() },
      });
      return data as NodePool;
    },
    onSuccess: () => invalidatePoolsAndNodes(qc),
  });
}

// DELETE /admin/node-pools/{id} — nodes go back to unassigned, grants cascade. 409 while a live
// session holds a card on one of the pool's nodes. pool.manage.
export function useDeleteNodePool() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await raw.DELETE('/api/v1/node-pools/{pool_id}', {
        params: { path: { pool_id: id } },
        headers: { 'Idempotency-Key': idemKey() },
      });
    },
    onSuccess: () => invalidatePoolsAndNodes(qc),
  });
}

// PUT /admin/nodes/{node_id}/pool — move a node into a pool (null = shared / unassigned). The pool
// must be in the node's cluster. pool.manage.
export function useSetNodePool() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ nodeId, pool_id }: { nodeId: string; pool_id: string | null }) => {
      const { data } = await raw.PUT('/api/v1/nodes/{node_id}/pool', {
        params: { path: { node_id: nodeId } },
        body: { pool_id },
        headers: { 'Idempotency-Key': idemKey() },
      });
      return data;
    },
    onSuccess: () => invalidatePoolsAndNodes(qc),
  });
}

// POST /admin/node-pools/{id}/grants — pool.grant. super_admin: any org or group; org_admin: only a
// group in an organization they administer, and only on a pool that already carries that
// organization's grant.
export function useGrantPool() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ poolId, ...body }: { poolId: string; scope: PoolGrantScope; scope_id: string }) => {
      const { data } = await raw.POST('/api/v1/node-pools/{pool_id}/grants', {
        params: { path: { pool_id: poolId } },
        body,
        headers: { 'Idempotency-Key': idemKey() },
      });
      return data as NodePoolGrant;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: nodePoolKeys.all }),
  });
}

// DELETE /admin/node-pools/{id}/grants/{grant_id} — pool.grant, same sub-assignment rule.
export function useRevokePoolGrant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ poolId, grantId }: { poolId: string; grantId: string }) => {
      await raw.DELETE('/api/v1/node-pools/{pool_id}/grants/{grant_id}', {
        params: { path: { pool_id: poolId, grant_id: grantId } },
        headers: { 'Idempotency-Key': idemKey() },
      });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: nodePoolKeys.all }),
  });
}
