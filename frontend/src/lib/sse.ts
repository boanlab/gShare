// Live updates over SSE (/sessions/{id}/events), with the caller falling back to polling.
import { useAuthStore } from '@/auth/authStore';

const BASE = import.meta.env.VITE_API_BASE ?? '/api/v1';

export interface SseHandlers<T = unknown> {
  onMessage?: (data: T, event: MessageEvent) => void;
  onError?: (err: Event) => void;
  onOpen?: () => void;
}

/**
 * Subscribe to a session's event stream. EventSource cannot set custom headers, so the token
 * travels in the query string, which the backend accepts.
 * Call the returned function to unsubscribe. On a connection failure the caller falls back to
 * polling.
 */
export function subscribeSessionEvents<T = unknown>(
  sessionId: string,
  handlers: SseHandlers<T>,
): () => void {
  const token = useAuthStore.getState().accessToken;
  const projectId = useAuthStore.getState().activeProjectId;
  const params = new URLSearchParams();
  if (token) params.set('access_token', token);
  if (projectId) params.set('group_id', projectId);

  const url = `${BASE}/sessions/${encodeURIComponent(sessionId)}/events?${params.toString()}`;
  const es = new EventSource(url);

  es.onopen = () => handlers.onOpen?.();
  es.onmessage = (ev) => {
    try {
      handlers.onMessage?.(JSON.parse(ev.data) as T, ev);
    } catch {
      // Ignore anything that does not parse.
    }
  };
  es.onerror = (err) => handlers.onError?.(err);

  return () => es.close();
}

/**
 * Subscribe to the caller's notifications (/notifications/events); onMessage fires on each ping.
 * EventSource cannot set headers, so the token travels as access_token. On a connection failure the
 * caller's 30-second poll takes over.
 */
export function subscribeNotifications(handlers: SseHandlers): () => void {
  const token = useAuthStore.getState().accessToken;
  const params = new URLSearchParams();
  if (token) params.set('access_token', token);
  const es = new EventSource(`${BASE}/notifications/events?${params.toString()}`);
  es.onopen = () => handlers.onOpen?.();
  es.onmessage = (ev) => handlers.onMessage?.(ev.data, ev);
  es.onerror = (err) => handlers.onError?.(err);
  return () => es.close();
}
