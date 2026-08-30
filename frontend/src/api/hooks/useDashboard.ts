import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';

// Dashboard aggregates (GET /dashboard/summary).
export function useDashboardSummary() {
  return useQuery({
    queryKey: ['dashboard', 'summary'],
    // The landing screen: refetch on every visit (the 30s global staleTime would otherwise show
    // a just-terminated session), and keep it moving while it is open.
    refetchOnMount: 'always',
    refetchInterval: 15000,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/dashboard/summary');
      return data;
    },
  });
}
