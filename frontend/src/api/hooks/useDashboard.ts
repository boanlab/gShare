import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';

// Dashboard aggregates (GET /dashboard/summary).
export function useDashboardSummary() {
  return useQuery({
    queryKey: ['dashboard', 'summary'],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/dashboard/summary');
      return data;
    },
  });
}
