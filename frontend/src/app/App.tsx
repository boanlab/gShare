import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider } from 'react-router-dom';
import { router } from '@/routes/router';
import { ToastHost } from '@/components/Toast';
import { ConfirmProvider } from '@/components/ConfirmDialog';

// Do not retry 401, 403, or 404; only 5xx responses get an exponential backoff.
const queryClient = new QueryClient({
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

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ConfirmProvider>
        <RouterProvider router={router} />
      </ConfirmProvider>
      <ToastHost />
    </QueryClientProvider>
  );
}
