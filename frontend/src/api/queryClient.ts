import { QueryClient } from '@tanstack/react-query';

// The app-wide query client. It lives in its own module (not App.tsx) so the auth store can reach
// it without an import cycle: the cache MUST be wiped on login and logout, or the previous user's
// sessions/dashboard/policy stay rendered for up to staleTime for whoever signs in next on the
// same browser — a cross-account data leak on shared lab machines.

// Do not retry 401, 403, or 404; only 5xx responses get an exponential backoff.
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: (failureCount, error) => {
        const status = (error as { status?: number })?.status;
        if (status && [401, 403, 404].includes(status)) return false;
        return failureCount < 2;
      },
    },
  },
});
