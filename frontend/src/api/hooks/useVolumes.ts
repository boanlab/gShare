import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';

// POST and DELETE under /storage/volumes use the loose accessor.
const raw = api as unknown as {
  GET: (p: string, o?: { params?: { path?: Record<string, string>; query?: Record<string, unknown> } }) => Promise<{ data?: unknown }>;
  POST: (p: string, o?: { body?: unknown; params?: { path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
  DELETE: (p: string, o?: { params?: { path?: Record<string, string>; query?: Record<string, unknown> } }) => Promise<{ data?: unknown }>;
};

function unwrap(d: unknown): Array<Record<string, unknown>> {
  if (Array.isArray(d)) return d as Array<Record<string, unknown>>;
  const o = d as { data?: unknown[]; items?: unknown[] } | undefined;
  return (o?.data ?? o?.items ?? []) as Array<Record<string, unknown>>;
}

// The storage volume list.
export function useVolumes() {
  return useQuery({
    queryKey: ['volumes'],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/storage/volumes');
      return (data ?? []) as Array<Record<string, unknown>>;
    },
  });
}

export interface CreateVolumeBody {
  scope: 'user' | 'group';
  scope_id: string;
  type: 'home' | 'group' | 'dataset' | 'scratch';
  name: string;
  access_mode: 'RWO' | 'RWX' | 'ROX';
  quota_gb: number;
}

// GET /storage/volumes/{id} — one volume, for deep links from the sharing, expansion, and snapshot
// pages.
export function useVolume(id?: string) {
  return useQuery({
    queryKey: ['volume', id ?? ''],
    enabled: !!id,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/storage/volumes/{volume_id}', { params: { path: { volume_id: id as string } } });
      return data as Record<string, unknown>;
    },
  });
}

export interface StorageQuotaUsage {
  has_limit: boolean;
  limit_gb?: number;
  allocated_gb: number;
  remaining_gb?: number;
}

// GET /storage/volumes/quota-usage — the storage policy limit and usage for a scope, which the
// new-volume form turns into a warning.
export function useStorageQuotaUsage(scope: 'user' | 'group', scopeId: string | undefined) {
  return useQuery({
    queryKey: ['storage-quota-usage', scope, scopeId ?? ''],
    enabled: !!scopeId,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/storage/volumes/quota-usage', {
        params: { query: { scope, scope_id: scopeId as string } },
      });
      return (data as StorageQuotaUsage) ?? { has_limit: false, allocated_gb: 0 };
    },
  });
}

// GET /storage/volumes/pricing — the storage rate in credits per GB-hour, shown as an estimate on
// the new-volume form.
export function useVolumePricing() {
  return useQuery({
    queryKey: ['volume-pricing'],
    staleTime: 5 * 60_000,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/storage/volumes/pricing');
      return (data as { credit_per_gb_hour?: number }) ?? { credit_per_gb_hour: 0 };
    },
  });
}

// POST /storage/volumes — create a volume.
export function useCreateVolume() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: CreateVolumeBody) => {
      const { data } = await raw.POST('/api/v1/storage/volumes', { body });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['volumes'] }),
  });
}

// DELETE /storage/volumes/{id} — 409 while the volume is mounted by an active session.
export function useDeleteVolume() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      // The confirm token has to equal volume_id, which guards against deleting the wrong volume.
      await raw.DELETE('/api/v1/storage/volumes/{volume_id}', { params: { path: { volume_id: id }, query: { confirm: id } } });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['volumes'] }),
  });
}

// ── Sharing (permissions) ──
export function useVolumePermissions(volumeId: string | null) {
  return useQuery({
    queryKey: ['volume-perms', volumeId],
    enabled: !!volumeId,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/storage/volumes/{volume_id}/permissions', { params: { path: { volume_id: volumeId as string } } });
      return unwrap(data);
    },
  });
}
export function useGrantPermission(volumeId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { user_id?: string; email?: string; role: 'owner' | 'rw' | 'ro' }) => {
      const { data } = await raw.POST('/api/v1/storage/volumes/{volume_id}/permissions', { params: { path: { volume_id: volumeId } }, body });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['volume-perms', volumeId] }),
  });
}
export function useRevokePermission(volumeId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (userId: string) => {
      await raw.DELETE('/api/v1/storage/volumes/{volume_id}/permissions/{user_id}', { params: { path: { volume_id: volumeId, user_id: userId } } });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['volume-perms', volumeId] }),
  });
}

// ── Expansion (quota requests) ──
export function useCreateQuotaRequest(volumeId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (requested_gb: number) => {
      const { data } = await raw.POST('/api/v1/storage/volumes/{volume_id}/quota-requests', { params: { path: { volume_id: volumeId } }, body: { requested_gb } });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['volumes'] }),
  });
}

// ── Snapshots ──
export function useSnapshots(volumeId: string | null) {
  return useQuery({
    queryKey: ['volume-snaps', volumeId],
    enabled: !!volumeId,
    refetchInterval: 5000,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/storage/volumes/{volume_id}/snapshots', { params: { path: { volume_id: volumeId as string } } });
      return unwrap(data);
    },
  });
}
export function useCreateSnapshot(volumeId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data } = await raw.POST('/api/v1/storage/volumes/{volume_id}/snapshots', { params: { path: { volume_id: volumeId } } });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['volume-snaps', volumeId] }),
  });
}
export function useRestoreSnapshot(volumeId: string) {
  return useMutation({
    mutationFn: async (snapshotId: string) => {
      const { data } = await raw.POST('/api/v1/storage/volumes/{volume_id}/snapshots/{snapshot_id}/restore', { params: { path: { volume_id: volumeId, snapshot_id: snapshotId } } });
      return data;
    },
  });
}
export function useDeleteSnapshot(volumeId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (snapshotId: string) => {
      await raw.DELETE('/api/v1/storage/volumes/{volume_id}/snapshots/{snapshot_id}', { params: { path: { volume_id: volumeId, snapshot_id: snapshotId } } });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['volume-snaps', volumeId] }),
  });
}
