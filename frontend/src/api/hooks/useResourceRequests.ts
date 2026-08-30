import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';

// Per-user compute-quota requests: member asks, group admin approves, the backend upserts a
// user-scope policy carrying only the granted keys (everything else stays inherited).
const raw = api as unknown as {
  GET: (p: string, o?: { params?: { query?: Record<string, unknown> } }) => Promise<{ data?: unknown }>;
  POST: (p: string, o?: { body?: unknown; params?: { path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
};

export interface ResourceRequestRow {
  id: string;
  user_id: string;
  requester_name?: string | null;
  group_id?: string | null;
  cpu?: number | null;
  mem_gb?: number | null;
  storage_gb?: number | null;
  note: string;
  status: string;
  decided_reason?: string | null;
  created_at?: string;
}

const keys = { box: (b: string) => ['resource-requests', b] as const };

export function useResourceRequests(box: 'mine' | 'incoming') {
  return useQuery({
    queryKey: keys.box(box),
    refetchInterval: 15000,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/resource-policies/requests', { params: { query: { box } } });
      return ((data as { data?: ResourceRequestRow[] } | undefined)?.data ?? []);
    },
  });
}

export function useCreateResourceRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { group_id?: string | null; cpu?: number; mem_gb?: number; storage_gb?: number; gpu_mem_mb?: number; gpu_cores?: number; note: string }) => {
      const { data } = await raw.POST('/api/v1/resource-policies/requests', { body });
      return data as ResourceRequestRow;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['resource-requests'] }),
  });
}

export function useDecideResourceRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, approve, reason }: { id: string; approve: boolean; reason?: string }) => {
      const path = approve
        ? '/api/v1/resource-policies/requests/{request_id}/approve'
        : '/api/v1/resource-policies/requests/{request_id}/reject';
      const { data } = await raw.POST(path, {
        params: { path: { request_id: id } },
        ...(approve ? {} : { body: { reason } }),
      });
      return data as ResourceRequestRow;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['resource-requests'] }),
  });
}
