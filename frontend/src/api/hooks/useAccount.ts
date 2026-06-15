import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';

// Read the caller's profile (/auth/me) and edit it (PATCH /users/{id}).
const raw = api as unknown as {
  PATCH: (path: string, init?: { body?: unknown; params?: { path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
};

export interface MyMembership {
  group_id: string;
  project_name: string;
  org_id?: string | null;
  org_name?: string | null;
  role: string;
}

export interface MyProfile {
  id: string;
  email: string;
  name: string;
  global_role: string | null;
  global_roles?: string[];
  memberships?: MyMembership[];
}

export const accountKeys = { me: ['account', 'me'] as const };

// GET /auth/me — the current user plus their membership context.
export function useMyProfile() {
  return useQuery({
    queryKey: accountKeys.me,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/auth/me');
      // Accept either the { user, memberships } envelope or a flat User.
      const d = data as unknown as { user?: MyProfile } & Partial<MyProfile>;
      return (d?.user ?? (d as MyProfile)) ?? null;
    },
  });
}

// PATCH /users/{id} — edit your own profile, such as the display name.
export function useUpdateProfile(userId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { name?: string }) => {
      const { data } = await raw.PATCH('/api/v1/users/{user_id}', {
        body,
        params: { path: { user_id: userId } },
      });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: accountKeys.me }),
  });
}
