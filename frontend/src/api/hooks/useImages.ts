import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, idemKey } from '@/api/client';
import type { components } from '@/api/schema';

// The image and template registry, plus image builds.
// The list response envelope is { data, pagination }.

export const imageKeys = {
  all: ['images'] as const,
  list: (f: object) => ['images', 'list', f] as const,
  builds: (f: object) => ['image-builds', 'list', f] as const,
  build: (id: string) => ['image-builds', 'detail', id] as const,
};

export interface ImageFilter {
  kind?: 'image' | 'template' | 'iso';
  q?: string;
  tag?: string;
  public?: boolean;            // true lists public images only, which is what the wizard shows
  page?: number;
  size?: number;
}

// PATCH /images/{id} is not in the generated schema, so it uses the loose accessor.
const rawImg = api as unknown as {
  PATCH: (path: string, init?: { body?: unknown; params?: { path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
};

// GET /images — the catalogue, by registry and tag.
export function useImages(filter: ImageFilter = {}) {
  return useQuery({
    queryKey: imageKeys.list(filter),
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/images', { params: { query: filter } });
      return data ?? { data: [], pagination: { page: 1, size: 20, total: 0, total_pages: 0 } };
    },
  });
}

export interface ImportImageBody {
  source_type: 'registry' | 'url';
  source: string;
  name: string;
  kind: 'image' | 'template' | 'iso';
  registry_auth?: { username?: string; password?: string; token?: string };
  tags?: Record<string, string>;
  cuda_version?: string;        // the image's CUDA version, e.g. '12.4'; empty means unspecified
}

// POST /images/import — pull from an external registry or URL; asynchronous, returns 202.
export function useImportImage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: ImportImageBody) => {
      const { data } = await api.POST('/api/v1/images/import', { body });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: imageKeys.all }),
  });
}

export interface UpdateImageBody {
  name?: string;
  public?: boolean;
  cuda_version?: string | null;
  supported_gpus?: string[];
}

// PATCH /images/{id} — toggle public, and edit the name, CUDA version, and supported GPUs.
// super_admin only.
export function useUpdateImage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...body }: { id: string } & UpdateImageBody) => {
      const { data } = await rawImg.PATCH('/api/v1/images/{image_id}', { params: { path: { image_id: id } }, body });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: imageKeys.all }),
  });
}

export interface ImageBuildFilter {
  group_id?: string;
  status?: string;
  source?: 'dockerfile' | 'git';
  page?: number;
  size?: number;
  sort?: string;
}

// GET /image-builds — the build list, with status and the resulting image_ref.
// Polls while any build is still in progress.
export function useImageBuilds(filter: ImageBuildFilter = {}) {
  return useQuery({
    queryKey: imageKeys.builds(filter),
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/image-builds', { params: { query: filter } });
      return data ?? { data: [], pagination: { page: 1, size: 20, total: 0, total_pages: 0 } };
    },
    refetchInterval: (q) => {
      const rows = (q.state.data?.data ?? []) as { status?: string }[];
      const terminal = ['succeeded', 'failed', 'cancelled'];
      const pending = rows.some((r) => !terminal.includes(r.status ?? ''));
      return pending ? 5000 : false;
    },
  });
}

export interface CreateBuildBody {
  group_id: string;
  name: string;
  source: 'dockerfile' | 'git';
  dockerfile?: string;
  git_url?: string;
  git_ref?: string;
  context?: string;
  build_args?: Record<string, string>;
  target_tag?: string;
}

// POST /image-builds — start a build from a Dockerfile or a git source; asynchronous, returns 202.
export function useCreateBuild() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: CreateBuildBody) => {
      // git_ref and context have backend defaults (main and .), so they are optional here and cast
      // to satisfy the schema, which marks them required.
      const { data } = await api.POST('/api/v1/image-builds', {
        body: body as components['schemas']['BuildCreate'],
        headers: { 'Idempotency-Key': idemKey() },
      });
      return data;
    },
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ['image-builds'] }),
  });
}
