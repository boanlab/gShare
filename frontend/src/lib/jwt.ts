// Parses the JWT claims (global_role, group_id, and the rest).

export interface JwtClaims {
  sub?: string;
  email?: string;
  global_role?: string | null;
  group_id?: string;
  must_change_password?: boolean;
  exp?: number;
  [k: string]: unknown;
}

export function parseJwt(token: string): JwtClaims {
  try {
    const payload = token.split('.')[1];
    const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'));
    return JSON.parse(json) as JwtClaims;
  } catch {
    return {};
  }
}
