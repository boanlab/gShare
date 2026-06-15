import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, idemKey } from '@/api/client';
import type { components } from '@/api/schema';

type ClusterRead = components['schemas']['ClusterRead'];

// POST and DELETE on /clusters use the loose accessor; the body carries kubeconfig_b64.
const raw = api as unknown as {
  POST: (path: string, init?: { body?: unknown; headers?: Record<string, string> }) => Promise<{ data?: unknown }>;
  PATCH: (path: string, init?: { body?: unknown; params?: { path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
  DELETE: (path: string, init?: { params?: { path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
};

// The cluster list behind the top bar's context switcher. cluster.read is super_admin only, so the
// query is gated with enabled.
export function useClusters(opts?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ['clusters'],
    enabled: opts?.enabled ?? true,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/clusters');
      return data?.data ?? [];
    },
  });
}

// GET /clusters/{id} — one cluster, for deep links from the edit page.
export function useCluster(id?: string) {
  return useQuery({
    queryKey: ['cluster', id ?? ''],
    enabled: !!id,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/clusters/{cluster_id}', { params: { path: { cluster_id: id as string } } });
      return data as ClusterRead;
    },
  });
}

// UTF-8-safe base64 for the kubeconfig YAML. btoa handles Latin-1 only, hence the
// encodeURIComponent round trip.
function toB64(s: string): string {
  return btoa(unescape(encodeURIComponent(s)));
}

// POST /clusters — register a cluster by bootstrapping from its kubeconfig. Idempotency key
// required. The body is { name, role (primary or standby), kubeconfig_b64 }; role defaults to
// primary for the single-cluster case.
export function useRegisterCluster() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { name: string; kubeconfig: string; role?: string }) => {
      const { data } = await raw.POST('/api/v1/clusters', {
        body: {
          name: body.name,
          role: body.role ?? 'primary',
          kubeconfig_b64: toB64(body.kubeconfig),
        },
        headers: { 'Idempotency-Key': idemKey() },
      });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['clusters'] }),
  });
}

// PATCH /clusters/{id} — update the name or role. Rotating the kubeconfig means re-registering.
export function useUpdateCluster() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...body }: { id: string; name?: string; role?: string }) => {
      const { data } = await raw.PATCH('/api/v1/clusters/{cluster_id}', {
        params: { path: { cluster_id: id } },
        body,
      });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['clusters'] }),
  });
}

// DELETE /clusters/{id}.
export function useDeleteCluster() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await raw.DELETE('/api/v1/clusters/{cluster_id}', { params: { path: { cluster_id: id } } });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['clusters'] }),
  });
}
