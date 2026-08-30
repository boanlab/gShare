import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';

// In-app notifications: list, mark one read, mark all read.
// Uses the loose accessor so the envelope can be typed as the local Notification.
const raw = api as unknown as {
  GET: (path: string, init?: { params?: { query?: Record<string, unknown> } }) => Promise<{ data?: unknown }>;
  POST: (path: string, init?: { params?: { path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
  DELETE: (path: string, init?: { params?: { path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
};

export interface Notification {
  id: string;
  type: string;                 // the notification kind, as the backend reports it
  title: string;
  body?: string;
  ref?: Record<string, unknown>;  // deep-link context: session_id, request_id, cluster_id, and so on
  params?: Record<string, unknown>; // structured values for the console's own locale template
  read_at: string | null;
  deleted_at?: string | null;
  created_at: string;
}

function unwrapList(body: unknown): Notification[] {
  const b = body as { data?: Notification[]; items?: Notification[] } | undefined;
  return b?.data ?? b?.items ?? [];
}

export const notificationKeys = {
  all: ['notifications'] as const,
};

// Full history for the my-page log: dismissed (soft-deleted) rows included.
export function useNotificationLog() {
  return useQuery({
    queryKey: [...notificationKeys.all, 'log'],
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/notifications', { params: { query: { size: 100, include_deleted: true } } });
      return unwrapList(data);
    },
  });
}

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


// DELETE /notifications/{id}.
export function useDeleteNotification() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await raw.DELETE('/api/v1/notifications/{id}', { params: { path: { id } } });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: notificationKeys.all }),
  });
}

// DELETE /notifications — clears the caller's whole list.
export function useDeleteAllNotifications() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      await raw.DELETE('/api/v1/notifications');
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: notificationKeys.all }),
  });
}
