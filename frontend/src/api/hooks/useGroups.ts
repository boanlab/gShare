import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';

// CRUD for organizations, projects, and memberships.
// A project is called a group in the UI. These use the loose accessor so the response envelopes can
// be typed locally.
const raw = api as unknown as {
  GET: (path: string, init?: { params?: { query?: Record<string, unknown>; path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
  POST: (path: string, init?: { body?: unknown; params?: { path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
  PATCH: (path: string, init?: { body?: unknown; params?: { path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
  DELETE: (path: string, init?: { params?: { path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
};

export type OrgStatus = 'active' | 'inactive';
export type ProjectStatus = 'active' | 'inactive' | 'archived';
export type MembershipRole = 'org_admin' | 'group_admin' | 'member' | 'guest';

export interface Organization {
  id: string;
  name: string;
  status: OrgStatus;
  created_at: string;
}

export interface Project {
  id: string;
  org_id: string;
  org_name?: string | null;
  name: string;
  status: ProjectStatus;
  created_at: string;
  wallet_id?: string;
  member_count?: number;
}

export interface Membership {
  id: string;
  user_id: string;
  user_name: string;
  group_id: string | null;
  org_id?: string | null;
  role: MembershipRole;
  expires_at?: string | null;
  created_at: string;
}

export interface OrgAdmin {
  id: string;
  user_id: string;
  user_name: string;
  email?: string | null;
  org_id: string;
  role: 'org_admin';
  created_at: string;
}

export const groupKeys = {
  orgs: ['organizations'] as const,
  projects: (orgId?: string) => ['projects', orgId ?? 'all'] as const,
  memberships: (projectId: string) => ['memberships', projectId] as const,
  orgAdmins: (orgId: string) => ['org-admins', orgId] as const,
};

// GET /organizations/{id} — one organization, for deep links from the edit and admin pages.
export function useOrganization(id?: string) {
  return useQuery({
    queryKey: ['organization', id ?? ''],
    enabled: !!id,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/organizations/{org_id}', { params: { path: { org_id: id as string } } });
      return data as Organization;
    },
  });
}

// GET /projects/{id} — one group.
export function useProject(id?: string) {
  return useQuery({
    queryKey: ['project', id ?? ''],
    enabled: !!id,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/projects/{group_id}', { params: { path: { group_id: id as string } } });
      return data as Project;
    },
  });
}

// GET /organizations — org.read admits only super_admin and org_admin, so without that authority
// enabled=false stops the call being made.
export function useOrganizations(opts?: { enabled?: boolean }) {
  return useQuery({
    queryKey: groupKeys.orgs,
    enabled: opts?.enabled ?? true,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/organizations', { params: { query: { page: 1, size: 100 } } });
      return (data as { data?: Organization[] } | undefined)?.data ?? [];
    },
  });
}

// POST /organizations
export function useCreateOrganization() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { name: string; status?: OrgStatus; create_node_pool?: boolean }) => {
      const { data } = await raw.POST('/api/v1/organizations', { body });
      return data as Organization;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: groupKeys.orgs }),
  });
}

// PATCH /organizations/{id} — update the name or status. super_admin only.
export function useUpdateOrganization() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...body }: { id: string; name?: string; status?: OrgStatus }) => {
      const { data } = await raw.PATCH('/api/v1/organizations/{org_id}', { params: { path: { org_id: id } }, body });
      return data as Organization;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: groupKeys.orgs }),
  });
}

// DELETE /organizations/{id} — soft delete. super_admin only, and 409 while live groups remain.
export function useDeleteOrganization() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await raw.DELETE('/api/v1/organizations/{org_id}', { params: { path: { org_id: id } } });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: groupKeys.orgs }),
  });
}

// GET /organizations/{id}/admins — the organization's administrators. super_admin, or that
// organization's org_admin.
export function useOrgAdmins(orgId: string | null) {
  return useQuery({
    queryKey: groupKeys.orgAdmins(orgId ?? ''),
    enabled: !!orgId,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/organizations/{org_id}/admins', {
        params: { path: { org_id: orgId as string } },
      });
      return (data as { data?: OrgAdmin[] } | undefined)?.data ?? [];
    },
  });
}

// POST /organizations/{id}/admins — appoint an organization administrator. super_admin only.
export function useAddOrgAdmin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ orgId, user_id }: { orgId: string; user_id: string }) => {
      const { data } = await raw.POST('/api/v1/organizations/{org_id}/admins', {
        params: { path: { org_id: orgId } },
        body: { user_id },
      });
      return data as OrgAdmin;
    },
    onSuccess: (_d, v) => qc.invalidateQueries({ queryKey: groupKeys.orgAdmins(v.orgId) }),
  });
}

// DELETE /organizations/{id}/admins/{user_id} — remove an organization administrator.
// super_admin only.
export function useRemoveOrgAdmin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ orgId, userId }: { orgId: string; userId: string }) => {
      await raw.DELETE('/api/v1/organizations/{org_id}/admins/{user_id}', {
        params: { path: { org_id: orgId, user_id: userId } },
      });
    },
    onSuccess: (_d, v) => qc.invalidateQueries({ queryKey: groupKeys.orgAdmins(v.orgId) }),
  });
}

// GET /projects — optionally filtered by org_id. A member sees only the projects they belong to.
export function useProjects(orgId?: string) {
  const query: Record<string, unknown> = { page: 1, size: 100 };
  if (orgId) query.org_id = orgId;
  return useQuery({
    queryKey: groupKeys.projects(orgId),
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/projects', { params: { query } });
      return (data as { data?: Project[] } | undefined)?.data ?? [];
    },
  });
}

// POST /projects — create a group, along with its default wallet.
export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { org_id: string; name: string; status?: ProjectStatus; create_project_wallet?: boolean; create_node_pool?: boolean; default_member_credit?: string }) => {
      const { data } = await raw.POST('/api/v1/projects', { body });
      return data as Project;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  });
}

// PATCH /projects/{id} — update the name or the archived status.
export function useUpdateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...body }: { id: string; name?: string; status?: ProjectStatus; default_member_credit?: string }) => {
      const { data } = await raw.PATCH('/api/v1/projects/{group_id}', { params: { path: { group_id: id } }, body });
      return data as Project;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  });
}

// DELETE /projects/{id} — soft delete a group. super_admin or org_admin, and 409 while live
// sessions remain.
export function useDeleteProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await raw.DELETE('/api/v1/projects/{group_id}', { params: { path: { group_id: id } } });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  });
}

// GET /projects/{id}/memberships
export function useMemberships(projectId: string | null) {
  return useQuery({
    queryKey: groupKeys.memberships(projectId ?? ''),
    enabled: !!projectId,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/projects/{group_id}/memberships', {
        params: { path: { group_id: projectId as string } },
      });
      return (data as { data?: Membership[] } | undefined)?.data ?? [];
    },
  });
}

// POST /projects/{id}/memberships — add a member or grant a role. A guest must carry expires_at.
export function useAddMembership() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      projectId,
      ...body
    }: {
      projectId: string;
      user_id: string;
      role: MembershipRole;
      expires_at?: string;
      grant_credit?: string;
    }) => {
      const { data } = await raw.POST('/api/v1/projects/{group_id}/memberships', {
        params: { path: { group_id: projectId } },
        body,
      });
      return data as Membership;
    },
    onSuccess: (_d, v) => qc.invalidateQueries({ queryKey: groupKeys.memberships(v.projectId) }),
  });
}

// PATCH /projects/{id}/memberships/{mid} — change a member's role.
export function useUpdateMembership() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ projectId, membershipId, role }: { projectId: string; membershipId: string; role: MembershipRole }) => {
      const { data } = await raw.PATCH('/api/v1/projects/{group_id}/memberships/{membership_id}', {
        params: { path: { group_id: projectId, membership_id: membershipId } },
        body: { role },
      });
      return data as Membership;
    },
    onSuccess: (_d, v) => qc.invalidateQueries({ queryKey: groupKeys.memberships(v.projectId) }),
  });
}

// DELETE /projects/{id}/memberships/{mid} — remove a member. Removing the last admin is 409.
export function useRemoveMembership() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ projectId, membershipId }: { projectId: string; membershipId: string }) => {
      await raw.DELETE('/api/v1/projects/{group_id}/memberships/{membership_id}', {
        params: { path: { group_id: projectId, membership_id: membershipId } },
      });
    },
    onSuccess: (_d, v) => qc.invalidateQueries({ queryKey: groupKeys.memberships(v.projectId) }),
  });
}
