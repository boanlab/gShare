import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';

// The audit log viewer, with filters and pagination.
// The response envelope is { data, pagination }, sorted by created_at descending.

export interface AuditFilter {
  actor_id?: string;
  actor_q?: string;   // search by actor name or email
  action?: string;
  target?: string;
  'at[gte]'?: string;
  'at[lt]'?: string;
  page?: number;
  size?: number;
  sort?: string;
}

export const auditKeys = {
  all: ['audit-logs'] as const,
  list: (f: AuditFilter) => ['audit-logs', 'list', f] as const,
};

// GET /audit-logs — audit events across permissions, credits, sessions, and policy.
export function useAuditLogs(filter: AuditFilter = {}) {
  return useQuery({
    queryKey: auditKeys.list(filter),
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/audit-logs', { params: { query: filter } });
      return data ?? { data: [], pagination: { page: 1, size: 20, total: 0, total_pages: 0 } };
    },
    placeholderData: (prev) => prev, // keeps the table from flashing between pages
  });
}
