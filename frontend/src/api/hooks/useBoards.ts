import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';

// Notices (공지) and inquiries (문의): the two announcement/Q&A boards added in batch 5-11.
const raw = api as unknown as {
  GET: (p: string, o?: { params?: { path?: Record<string, string>; query?: Record<string, unknown> } }) => Promise<{ data?: unknown }>;
  POST: (p: string, o?: { params?: { path?: Record<string, string> }; body?: unknown }) => Promise<{ data?: unknown; error?: unknown }>;
  PATCH: (p: string, o?: { params?: { path?: Record<string, string> }; body?: unknown }) => Promise<{ data?: unknown; error?: unknown }>;
  DELETE: (p: string, o?: { params?: { path?: Record<string, string> } }) => Promise<{ data?: unknown; error?: unknown }>;
};

export interface Notice {
  id: string;
  scope: 'global' | 'group';
  group_id?: string | null;
  group_name?: string | null;
  title: string;
  body: string;
  pinned: boolean;
  author_id: string;
  author_name?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface Inquiry {
  id: string;
  title: string;
  body: string;
  status: 'open' | 'answered' | 'closed';
  author_id: string;
  author_name?: string | null;
  group_id?: string | null;
  reply_count: number;
  created_at?: string;
}

export interface InquiryDetail extends Inquiry {
  replies: { id: string; author_id: string; author_name?: string | null; body: string; created_at?: string }[];
}

function env<T>(body: unknown): T[] {
  const b = body as { data?: T[] } | undefined;
  return b?.data ?? [];
}

const keys = {
  notices: ['notices'] as const,
  inquiries: (box: string) => ['inquiries', box] as const,
  inquiry: (id: string) => ['inquiries', 'one', id] as const,
};

export function useNotices(view: 'user' | 'admin' = 'user') {
  return useQuery({
    queryKey: [...keys.notices, view],
    queryFn: async () => env<Notice>((await raw.GET('/api/v1/notices', {
      params: { query: { size: 100, ...(view === 'admin' ? { all: true } : {}) } },
    })).data),
  });
}

export function useCreateNotice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { scope: 'global' | 'group'; group_id?: string | null; title: string; body: string; pinned?: boolean; notify?: boolean }) => {
      const { data, error } = await raw.POST('/api/v1/notices', { body });
      if (error) throw error;
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.notices }),
  });
}

export function useUpdateNotice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...body }: { id: string; title?: string; body?: string; pinned?: boolean }) => {
      const { data, error } = await raw.PATCH('/api/v1/notices/{id}', { params: { path: { id } }, body });
      if (error) throw error;
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.notices }),
  });
}

export function useDeleteNotice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { error } = await raw.DELETE('/api/v1/notices/{id}', { params: { path: { id } } });
      if (error) throw error;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.notices }),
  });
}

export function useInquiries(box: 'mine' | 'incoming') {
  return useQuery({
    queryKey: keys.inquiries(box),
    queryFn: async () => env<Inquiry>((await raw.GET('/api/v1/inquiries', { params: { query: { box, size: 100 } } })).data),
  });
}

export function useInquiry(id?: string) {
  return useQuery({
    queryKey: keys.inquiry(id ?? ''),
    enabled: !!id,
    queryFn: async () => (await raw.GET('/api/v1/inquiries/{id}', { params: { path: { id: id as string } } })).data as InquiryDetail,
  });
}

export function useCreateInquiry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { title: string; body: string; to: 'group' | 'system' }) => {
      const { data, error } = await raw.POST('/api/v1/inquiries', { body });
      if (error) throw error;
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['inquiries'] }),
  });
}

export function useReplyInquiry(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { body: string; close?: boolean }) => {
      const { data, error } = await raw.POST('/api/v1/inquiries/{id}/replies', { params: { path: { id } }, body });
      if (error) throw error;
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['inquiries'] }),
  });
}

export function useCloseInquiry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { error } = await raw.POST('/api/v1/inquiries/{id}/close', { params: { path: { id } } });
      if (error) throw error;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['inquiries'] }),
  });
}
