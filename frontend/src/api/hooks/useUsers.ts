import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';

// Listing, creating (inviting), and updating users, plus their global roles.
// Uses the loose accessor so the response envelopes can be typed locally.
const raw = api as unknown as {
  GET: (path: string, init?: { params?: { query?: Record<string, unknown>; path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
  POST: (path: string, init?: { body?: unknown; headers?: Record<string, string>; params?: { path?: Record<string, string> } }) => Promise<{ data?: unknown; error?: unknown }>;
  PATCH: (path: string, init?: { body?: unknown; params?: { path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
  PUT: (path: string, init?: { body?: unknown; params?: { path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
  DELETE: (path: string, init?: { params?: { path?: Record<string, string>; query?: Record<string, unknown> } }) => Promise<{ data?: unknown }>;
};

export type UserStatus = 'invited' | 'active' | 'suspended';
// Global roles; currently super_admin is the only one. Granting several uses global_roles.
export type GlobalRole = 'super_admin' | null;

export interface UserMembership {
  group_id: string;
  group_name: string;
  org_id: string;
  org_name: string;
  role: string;
}

export interface UserOrgAdmin {
  org_id: string;
  org_name: string;
}

export interface AdminUser {
  id: string;
  email: string;
  name: string;
  status: UserStatus;
  global_role: GlobalRole;        // the derived primary role
  global_roles?: string[];        // every global role granted; there may be several
  created_at: string;
  memberships?: UserMembership[];
  org_admins?: UserOrgAdmin[];    // organizations this user administers
}

export interface UserListFilter {
  q?: string;
  status?: UserStatus | '';
  org_id?: string;
  group_id?: string;
  page?: number;
  size?: number;
}

export interface CreateUserBody {
  email: string;
  name: string;
  group_id: string;   // the group to join, required; the organization follows from it
  status?: 'active' | 'suspended';
  initial_role?: string;
  password: string;   // initial password, which must be changed at first login
}

export const userKeys = {
  all: ['users'] as const,
  list: (f: UserListFilter) => ['users', 'list', f] as const,
};

// GET /users/{id} — one user, for deep links from the edit and delete pages.
export function useUser(id?: string) {
  return useQuery({
    queryKey: ['user', id ?? ''],
    enabled: !!id,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/users/{user_id}', { params: { path: { user_id: id as string } } });
      return data as AdminUser;
    },
  });
}

export interface UsersPage {
  data: AdminUser[];
  pagination: { page: number; size: number; total: number };
}

// GET /users — paginated, filterable by q, status, and org_id; the full envelope, so list
// screens can page server-side (2000 students never fit in one response).
export function useUsersPage(filter: UserListFilter = {}) {
  const query: Record<string, unknown> = { page: filter.page ?? 1, size: filter.size ?? 50 };
  if (filter.q) query.q = filter.q;
  if (filter.status) query.status = filter.status;
  if (filter.org_id) query.org_id = filter.org_id;
  if (filter.group_id) query.group_id = filter.group_id;
  return useQuery({
    queryKey: userKeys.list(filter),
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/users', { params: { query } });
      const page = (data ?? {}) as Partial<UsersPage>;
      return {
        data: page.data ?? [],
        pagination: page.pagination ?? { page: filter.page ?? 1, size: filter.size ?? 50, total: page.data?.length ?? 0 },
      } as UsersPage;
    },
  });
}

// Data-only convenience for pickers (bounded by `size`; use useUsersPage for list screens).
export function useUsers(filter: UserListFilter = {}) {
  const query = useUsersPage(filter);
  return { ...query, data: query.data?.data };
}

export interface BulkUserRow { email: string; name: string }
export interface BulkCreateBody { group_id: string; initial_role?: string; rows: BulkUserRow[] }
export interface BulkRowResult {
  row: number;
  email: string;
  status: 'created' | 'exists' | 'invalid';
  user_id?: string;
  initial_password?: string;
  code?: string;
}
export interface BulkCreateResponse {
  results: BulkRowResult[];
  summary: { requested: number; created: number; exists: number; invalid: number };
}

// POST /users/bulk — roster import: up to 200 rows per call, partial success by design.
// Initial passwords come back exactly once in the response (the import screen turns them into a
// downloadable credentials CSV). Idempotent per batch on the Idempotency-Key.
export function useBulkCreateUsers() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ body, idem }: { body: BulkCreateBody; idem: string }) => {
      const { data, error } = await raw.POST('/api/v1/users/bulk', {
        body,
        headers: { 'Idempotency-Key': idem },
      });
      if (error) throw error;
      return data as unknown as BulkCreateResponse;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: userKeys.all }),
  });
}

// POST /users — create a user (admin-set initial password; changed at first login).
export function useCreateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: CreateUserBody) => {
      const { data } = await raw.POST('/api/v1/users', { body });
      return data as AdminUser;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: userKeys.all }),
  });
}

// PATCH /users/{id} — name, email, status, and password reset. Which fields a caller may change is
// enforced by the backend, per role.
export function useUpdateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...body }: {
      id: string;
      name?: string;
      email?: string;
      status?: 'active' | 'suspended';
      password?: string;
    }) => {
      const { data } = await raw.PATCH('/api/v1/users/{user_id}', { params: { path: { user_id: id } }, body });
      return data as AdminUser;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: userKeys.all }),
  });
}

// PUT /users/{id}/department — set or move a user's group. super_admin or org_admin; null removes
// them from every group.
export function useSetUserDepartment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, group_id }: { id: string; group_id: string | null }) => {
      const { data } = await raw.PUT('/api/v1/users/{user_id}/department', {
        params: { path: { user_id: id } },
        body: { group_id },
      });
      return data as AdminUser;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: userKeys.all }),
  });
}

// DELETE /users/{id} — soft delete by default; hard=true is super_admin only.
export function useDeleteUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id }: { id: string }) => {
      await raw.DELETE('/api/v1/users/{user_id}', { params: { path: { user_id: id } } });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: userKeys.all }),
  });
}

// PUT /users/{id}/global-role — grant global roles. super_admin only, and several may be granted.
export function useSetGlobalRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, global_roles }: { id: string; global_roles: string[] }) => {
      const { data } = await raw.PUT('/api/v1/users/{user_id}/global-role', {
        params: { path: { user_id: id } },
        body: { global_roles },
      });
      return data as AdminUser;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: userKeys.all }),
  });
}

// GET /users/{id}/usage — live resource footprint for the admin drawer.
export interface UserUsage {
  sessions: { active: number; running: number; paused: number; queued: number };
  host: { cpu: number; mem_gb: number };
  gpu: { allocations: number; gpu_mem_mb: number; gpu_cores: number };
  volumes: { count: number; quota_gb: number; used_gb: number };
  wallet: { balance: number; reserved: number };
}

export function useUserUsage(id?: string) {
  return useQuery({
    queryKey: ['user', id ?? '', 'usage'],
    enabled: !!id,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/users/{user_id}/usage', { params: { path: { user_id: id as string } } });
      return data as UserUsage;
    },
  });
}
