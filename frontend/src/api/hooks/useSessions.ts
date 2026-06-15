import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';

export const sessionKeys = {
  all: ['sessions'] as const,
  list: (f: Record<string, unknown>) => ['sessions', 'list', f] as const,
  detail: (id: string) => ['sessions', 'detail', id] as const,
};

// Terminate a session (DELETE /sessions/{id}), from either the list or the detail view. The list is
// refreshed afterwards.
export function useTerminateSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await api.DELETE('/api/v1/sessions/{session_id}', { params: { path: { session_id: id } } });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: sessionKeys.all }),
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
    onSuccess: () => qc.invalidateQueries({ queryKey: sessionKeys.all }),
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
      qc.invalidateQueries({ queryKey: sessionKeys.all });
    },
  });
}
export const useStopSession = () => useSessionAction('stop');       // running -> paused; billing stops
export const useStartSession = () => useSessionAction('start');     // paused -> running
export const useRestartSession = () => useSessionAction('restart'); // stop followed by start

export function useSessions(filter: { status?: string; page?: number } = {}) {
  return useQuery({
    queryKey: sessionKeys.list(filter),
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/sessions', { params: { query: filter } });
      return data ?? [];
    },
  });
}

// Session detail: polls while the session is not terminated, as a fallback for a missing SSE
// connection.
export function useSession(id: string) {
  return useQuery({
    queryKey: sessionKeys.detail(id),
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/sessions/{session_id}', {
        params: { path: { session_id: id } },
      });
      return data;
    },
    refetchInterval: (q) =>
      ['terminated', 'error'].includes(q.state.data?.status ?? '') ? false : 4000,
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
