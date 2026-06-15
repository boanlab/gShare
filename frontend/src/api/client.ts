// openapi-fetch wrapper: Bearer and X-Project-Id interceptors, logout on 401, and Idempotency-Key
// generation. Failures are converted into the ApiError envelope.
import createClient, { type Middleware } from 'openapi-fetch';
import type { paths } from './schema';
import { useAuthStore } from '@/auth/authStore';
import { toApiError } from '@/lib/errors';

const BASE = (import.meta.env.VITE_API_BASE as string) ?? '/api/v1';

// The generated schema.d.ts keys paths in full (/api/v1/...), because openapi.json carries no
// servers block. So openapi-fetch calls use '/api/v1/...' and baseUrl holds only the origin, which
// keeps /api/v1 from appearing twice. Hand-built URLs (SSE, authApi) use VITE_API_BASE directly.
const ORIGIN = BASE.replace(/\/api\/v1\/?$/, '');

export const api = createClient<paths>({ baseUrl: ORIGIN });

// Request: attach the bearer token and the active group context (X-Project-Id).
const authMiddleware: Middleware = {
  onRequest({ request }) {
    const { accessToken, activeProjectId } = useAuthStore.getState();
    if (accessToken) request.headers.set('Authorization', `Bearer ${accessToken}`);
    if (activeProjectId) request.headers.set('X-Project-Id', activeProjectId);
    return request;
  },
};

// A 401 means the token expired or is invalid, so log out. Any other failure throws an ApiError.
// There is no refresh path.
const errorMiddleware: Middleware = {
  async onResponse({ response }) {
    if (response.status === 401) {
      useAuthStore.getState().logout('expired');
      throw toApiError(await response.clone().json().catch(() => ({})), 401);
    }
    if (!response.ok) {
      const err = toApiError(await response.clone().json().catch(() => ({})), response.status);
      // The server demands a password change (403); send the user to that screen. This backs up the
      // claim-based guard in RequireAuth.
      if (err.code === 'password_change_required' && !window.location.pathname.endsWith('/change-password')) {
        window.location.assign('/change-password');
      }
      throw err;
    }
    return response;
  },
};

api.use(authMiddleware);
api.use(errorMiddleware);

/** An idempotency key: one per click for POSTs that create state or move funds.
 * crypto.randomUUID exists only in a secure context (HTTPS or localhost), so over plain HTTP we fall
 * back to getRandomValues and then Math.random. */
export const idemKey = (): string => {
  const c = globalThis.crypto as Crypto | undefined;
  if (c?.randomUUID) return c.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (ch) => {
    const r = (c?.getRandomValues ? c.getRandomValues(new Uint8Array(1))[0] : Math.floor(Math.random() * 256)) % 16;
    return (ch === 'x' ? r : (r % 4) + 8).toString(16);
  });
};
