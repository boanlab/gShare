import { QueryClientProvider } from '@tanstack/react-query';
import { IconContext } from '@phosphor-icons/react';
import { queryClient } from '@/api/queryClient';
import { RouterProvider } from 'react-router-dom';
import { router } from '@/routes/router';
import { ToastHost } from '@/components/Toast';
import { ConfirmProvider } from '@/components/ConfirmDialog';
import { PromptProvider } from '@/components/PromptDialog';

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      {/* One icon family, one weight, one default size for the whole console. */}
      <IconContext.Provider value={{ size: 16, weight: 'regular' }}>
      <ConfirmProvider>
      <PromptProvider>
        <RouterProvider router={router} />
      </PromptProvider>
      </ConfirmProvider>
      <ToastHost />
      </IconContext.Provider>
    </QueryClientProvider>
  );
}
