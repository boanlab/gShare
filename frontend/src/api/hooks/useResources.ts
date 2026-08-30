import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';

// CRUD for the offering catalogue, presets, and resource policies (quotas).
// These use the loose accessor so the response envelopes can be typed locally.
const raw = api as unknown as {
  GET: (path: string, init?: { params?: { query?: Record<string, unknown>; path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
  POST: (path: string, init?: { body?: unknown }) => Promise<{ data?: unknown }>;
  PATCH: (path: string, init?: { body?: unknown; params?: { path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
  DELETE: (path: string, init?: { params?: { path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
};

export type ResourceClass = 'gpu' | 'cpu';
export type OfferingStatus = 'active' | 'inactive';

export interface Offering {
  id: string;
  name: string;
  resource_class: ResourceClass;
  gpu_model: string | null;
  gpu_mem_mb: number;
  gpu_cores: number;
  cpu?: number;
  mem_gb?: number;
  credit_per_hour: string;
  status: OfferingStatus;
  min_cuda?: string | null;       // minimum CUDA, e.g. '12.8'; GPU offerings only
}

export type PresetKind = 'compute' | 'gpu';
export interface ResourcePreset {
  id: string;
  name: string;
  kind: PresetKind;
  cpu: number | null;
  mem_gb: number | null;
  disk_gb: number | null;
  gpu_frac: number | null;
  gpu_cores: number | null;
  mode: string | null;
  gpu_mem_mb: number | null;
}

export type PolicyScope = 'global' | 'org' | 'group' | 'user';

export interface ResourcePolicy {
  id: string;
  scope: PolicyScope;
  scope_id: string;
  max_concurrent: number;
  max_queued: number;
  max_runtime_min: number;
  idle_timeout_sec: number;
  cpu_session_max_concurrent?: number;
  cpu_session_max_runtime_min?: number;
  cpu_session_idle_timeout_sec?: number;
  limits: { cpu: number; mem_gb: number; gpu_mem_mb: number; gpu_cores: number; storage_gb: number; volume_gb?: number; shared_pool?: boolean };
}

export const resourceKeys = {
  offerings: ['offerings'] as const,
  presets: ['resource-presets'] as const,
  policies: (scope?: string) => ['resource-policies', scope ?? 'all'] as const,
};

// Free GPU capacity per model, which the session wizard uses to disable tiers that cannot fit.
// GET /sessions/gpu-availability, member and above.
export interface GpuDeviceAvail {
  free_mem_mb: number;
  free_cores: number;
  total_mem_mb: number;
  total_cores: number;
  mode: string;
}
export interface GpuModelAvail {
  gpu_model: string;
  card_mem_mb: number;
  devices: GpuDeviceAvail[];
}
export function useGpuAvailability(opts: { fleet?: boolean } = {}) {
  return useQuery({
    queryKey: ['gpu-availability', opts.fleet ? 'fleet' : 'mine'],
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/sessions/gpu-availability', opts.fleet ? { params: { query: { fleet: true } } } : undefined);
      return (data as { data?: GpuModelAvail[] } | undefined)?.data ?? [];
    },
    refetchInterval: 15000,   // availability moves, so refresh periodically
    // Free capacity is the one number that must never be stale when you look at it: interval
    // refetches pause while the tab is in the background (an admin cordoning a node in another
    // tab), and the global 30s staleTime would otherwise suppress the refetch on focus.
    staleTime: 0,
    refetchOnWindowFocus: 'always',
  });
}

// GET /offerings/{id} — one offering, for deep links from the edit page.
export function useOffering(id?: string) {
  return useQuery({
    queryKey: ['offering', id ?? ''],
    enabled: !!id,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/offerings/{offering_id}', { params: { path: { offering_id: id as string } } });
      return data as Offering;
    },
  });
}

// GET /resource-presets/{id} — one preset.
export function usePreset(id?: string) {
  return useQuery({
    queryKey: ['resource-preset', id ?? ''],
    enabled: !!id,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/resource-presets/{preset_id}', { params: { path: { preset_id: id as string } } });
      return data as ResourcePreset;
    },
  });
}

// GET /resource-policies/{id} — one policy.
export function usePolicy(id?: string) {
  return useQuery({
    queryKey: ['resource-policy', id ?? ''],
    enabled: !!id,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/resource-policies/{policy_id}', { params: { path: { policy_id: id as string } } });
      return data as ResourcePolicy;
    },
  });
}

// GET /offerings
export function useOfferings() {
  return useQuery({
    queryKey: resourceKeys.offerings,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/offerings', { params: { query: { page: 1, size: 100 } } });
      return (data as { data?: Offering[] } | undefined)?.data ?? [];
    },
  });
}

export interface CreateOfferingBody {
  name: string;
  resource_class?: ResourceClass;
  gpu_model?: string | null;
  gpu_mem_mb?: number;
  gpu_cores?: number;
  cpu?: number;
  mem_gb?: number;
  credit_per_hour: string;
  status?: OfferingStatus;
  min_cuda?: string | null;
}

// POST /offerings (super_admin)
export function useCreateOffering() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: CreateOfferingBody) => {
      const { data } = await raw.POST('/api/v1/offerings', { body });
      return data as Offering;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: resourceKeys.offerings }),
  });
}

// PATCH /offerings/{id} — update the name, model, specification, rate, or status.
export function useUpdateOffering() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...body }: {
      id: string; name?: string; gpu_model?: string | null; gpu_mem_mb?: number; gpu_cores?: number;
      cpu?: number; mem_gb?: number; disk_gb?: number; credit_per_hour?: string; status?: OfferingStatus;
      min_cuda?: string | null;
    }) => {
      const { data } = await raw.PATCH('/api/v1/offerings/{offering_id}', { params: { path: { offering_id: id } }, body });
      return data as Offering;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: resourceKeys.offerings }),
  });
}

// DELETE /offerings/{id} — 409 while a session still references it.
export function useDeleteOffering() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await raw.DELETE('/api/v1/offerings/{offering_id}', { params: { path: { offering_id: id } } });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: resourceKeys.offerings }),
  });
}

// GET /resource-presets
export function usePresets(kind?: PresetKind) {
  return useQuery({
    queryKey: [...resourceKeys.presets, kind ?? 'all'],
    queryFn: async () => {
      const query: Record<string, unknown> = { page: 1, size: 100 };
      if (kind) query.kind = kind;
      const { data } = await raw.GET('/api/v1/resource-presets', { params: { query } });
      return (data as { data?: ResourcePreset[] } | undefined)?.data ?? [];
    },
  });
}

export interface CreatePresetBody {
  name: string;
  kind: PresetKind;
  cpu?: number;
  mem_gb?: number;
  disk_gb?: number;
  gpu_frac?: number;
  gpu_cores?: number;
  mode?: string;
}

// POST /resource-presets
export function useCreatePreset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: CreatePresetBody) => {
      const { data } = await raw.POST('/api/v1/resource-presets', { body });
      return data as ResourcePreset;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: resourceKeys.presets }),
  });
}

// PATCH /resource-presets/{id} — update the fields belonging to the preset's kind; kind itself is
// immutable.
export function useUpdatePreset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...body }: { id: string } & Partial<Omit<CreatePresetBody, 'kind'>>) => {
      const { data } = await raw.PATCH('/api/v1/resource-presets/{preset_id}', { params: { path: { preset_id: id } }, body });
      return data as ResourcePreset;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: resourceKeys.presets }),
  });
}

// DELETE /resource-presets/{id}
export function useDeletePreset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await raw.DELETE('/api/v1/resource-presets/{preset_id}', { params: { path: { preset_id: id } } });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: resourceKeys.presets }),
  });
}

// GET /resource-policies
export function usePolicies(scope?: PolicyScope) {
  const query: Record<string, unknown> = { page: 1, size: 100 };
  if (scope) query.scope = scope;
  return useQuery({
    queryKey: resourceKeys.policies(scope),
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/resource-policies', { params: { query } });
      return (data as { data?: ResourcePolicy[] } | undefined)?.data ?? [];
    },
  });
}

export interface CreatePolicyBody {
  scope: PolicyScope;
  scope_id: string;
  max_concurrent: number;
  max_queued: number;
  max_runtime_min: number;
  idle_timeout_sec: number;
  limits: { cpu: number; mem_gb: number; gpu_mem_mb: number; gpu_cores: number; storage_gb: number; volume_gb?: number; shared_pool?: boolean };
}

// POST /resource-policies — (scope,scope_id) UNIQUE.
export function useCreatePolicy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: CreatePolicyBody) => {
      const { data } = await raw.POST('/api/v1/resource-policies', { body });
      return data as ResourcePolicy;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['resource-policies'] }),
  });
}

export type UpdatePolicyBody = Partial<Pick<CreatePolicyBody, 'max_concurrent' | 'max_queued' | 'max_runtime_min' | 'idle_timeout_sec' | 'limits'>>;

// PATCH /resource-policies/{id}
export function useUpdatePolicy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...body }: { id: string } & UpdatePolicyBody) => {
      const { data } = await raw.PATCH('/api/v1/resource-policies/{policy_id}', { params: { path: { policy_id: id } }, body });
      return data as ResourcePolicy;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['resource-policies'] }),
  });
}

export interface EffectivePolicy {
  has_policy: boolean;
  scope?: PolicyScope;
  max_concurrent?: number | null;
  max_queued?: number | null;
  limits?: { gpu_mem_mb: number; gpu_cores: number; cpu: number; mem_gb: number; storage_gb: number; volume_gb?: number };
  used?: { gpu_mem_mb: number; gpu_cores: number; cpu: number; mem_gb: number; storage_gb: number; volume_gb?: number; active: number; queued: number };
  remaining?: { gpu_mem_mb: number | null; gpu_cores: number | null; cpu: number | null; mem_gb: number | null; storage_gb: number; volume_gb?: number | null };
}

// GET /resource-policies/effective — the caller's effective policy with current usage and headroom,
// which the session wizard renders as limits.
export function useEffectivePolicy(groupId?: string) {
  return useQuery({
    queryKey: ['policy-effective', groupId ?? ''],
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/resource-policies/effective', {
        params: { query: groupId ? { group_id: groupId } : {} },
      });
      return (data as EffectivePolicy) ?? { has_policy: false };
    },
  });
}

// DELETE /resource-policies/{id} — the global policy is super_admin only; the rest admit
// super_admin, org_admin, and group_admin.
export function useDeletePolicy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await raw.DELETE('/api/v1/resource-policies/{policy_id}', { params: { path: { policy_id: id } } });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['resource-policies'] }),
  });
}
