import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';

// In-app notifications: list, mark one read, mark all read.
// Uses the loose accessor so the envelope can be typed as the local Notification.
const raw = api as unknown as {
  GET: (path: string, init?: { params?: { query?: Record<string, unknown> } }) => Promise<{ data?: unknown }>;
  POST: (path: string, init?: { params?: { path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
};

export interface Notification {
  id: string;
  type: string;                 // the notification kind, as the backend reports it
  title: string;
  body?: string;
  ref?: Record<string, unknown>;  // deep-link context: session_id, request_id, cluster_id, and so on
  read_at: string | null;
  created_at: string;
}

function unwrapList(body: unknown): Notification[] {
  const b = body as { data?: Notification[]; items?: Notification[] } | undefined;
  return b?.data ?? b?.items ?? [];
}

export const notificationKeys = {
  all: ['notifications'] as const,
};

// GET /notifications, optionally filtered to unread. Polls every 30 seconds for near-live updates.
export function useNotifications(unreadOnly = false) {
  return useQuery({
    queryKey: [...notificationKeys.all, { unreadOnly }],
    queryFn: async () => {
      const query: Record<string, unknown> = { size: 50 };
      if (unreadOnly) query.unread = true;
      const { data } = await raw.GET('/api/v1/notifications', { params: { query } });
      return unwrapList(data);
    },
    refetchInterval: 30_000,
  });
}

// POST /notifications/{id}/read.
export function useMarkRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await raw.POST('/api/v1/notifications/{id}/read', { params: { path: { id } } });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: notificationKeys.all }),
  });
}

// POST /notifications/read-all.
export function useMarkAllRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      await raw.POST('/api/v1/notifications/read-all');
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: notificationKeys.all }),
  });
}
