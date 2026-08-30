// Authentication calls: email-and-password login and password change. The token is a 24-hour HS256
// bearer; there is no refresh endpoint.
import { toApiError, type ApiError } from '@/lib/errors';

const BASE = import.meta.env.VITE_API_BASE ?? '/api/v1';

/** Throw the server's typed error envelope so callers can tell 429 rate-limit or a disabled
 * login path apart from a plain wrong password. */
async function throwEnvelope(res: Response): Promise<never> {
  let body: unknown = {};
  try { body = await res.json(); } catch { /* non-JSON body: keep the generic envelope */ }
  throw toApiError(body, res.status) as unknown as Error;
}
export type { ApiError };

export interface TokenResponse {
  access_token: string;
  token_type?: string;
  expires_in?: number;
}

/** Log in with an email and password. */
export async function passwordLogin(email: string, password: string): Promise<TokenResponse> {
  const res = await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) await throwEnvelope(res);
  return (await res.json()) as TokenResponse;
}

/** Change the password. On success a fresh token is issued with must_change_password cleared. */
export async function changePasswordRequest(
  accessToken: string,
  newPassword: string,
  currentPassword?: string,
): Promise<TokenResponse> {
  const res = await fetch(`${BASE}/auth/change-password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${accessToken}` },
    body: JSON.stringify({ new_password: newPassword, current_password: currentPassword }),
  });
  if (!res.ok) await throwEnvelope(res);
  return (await res.json()) as TokenResponse;
}
