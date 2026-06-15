import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, idemKey } from '@/api/client';
import { sessionKeys } from './useSessions';
import type { components } from '@/api/schema';
import type { CreateSessionBody } from '@/api/types';

type PreviewCostBody = components['schemas']['PreviewCostRequest'];

// One idempotency key per click of the wizard's Start button, so a double click cannot create two
// sessions.
export function useCreateSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: CreateSessionBody) => {
      const { data } = await api.POST('/api/v1/sessions', {
        body,
        headers: { 'Idempotency-Key': idemKey() },
      });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: sessionKeys.all }),
  });
}

// preview-cost: the estimated cost and availability shown on the review step.
export function usePreviewCost() {
  return useMutation({
    mutationFn: async (body: Partial<CreateSessionBody>) => {
      // The review step sends partial input, so the call only fires once the required fields are
      // filled; the server validates the rest.
      const { data } = await api.POST('/api/v1/sessions/preview-cost', { body: body as PreviewCostBody });
      return data;
    },
  });
}
