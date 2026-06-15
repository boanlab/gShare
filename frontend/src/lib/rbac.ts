// Role ordering. "A+" means A or anything above it.
export const ROLE_ORDER = [
  'guest',
  'member',
  'group_admin',
  'org_admin',
  'super_admin',
] as const;

export type Role = (typeof ROLE_ORDER)[number];

export function atLeast(have?: string | null, need?: string): boolean {
  if (!have || !need) return false;
  return ROLE_ORDER.indexOf(have as never) >= ROLE_ORDER.indexOf(need as never);
}
