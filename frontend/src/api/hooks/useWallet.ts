import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, idemKey } from '@/api/client';

// The transactions and topup-requests responses are envelopes, so these use the loose accessor.
const raw = api as unknown as {
  GET: (path: string, init?: { params?: { query?: Record<string, unknown>; path?: Record<string, string> } }) => Promise<{ data?: unknown }>;
  POST: (path: string, init?: { body?: unknown; headers?: Record<string, string> }) => Promise<{ data?: unknown }>;
};

// The caller's wallet: balance, reserved, and available.
export function useWallet() {
  return useQuery({
    queryKey: ['wallet', 'me'],
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/credits/wallets/me');
      return data;
    },
  });
}

export interface LedgerTxn {
  id: string;
  type: 'topup' | 'hold' | 'consume' | 'refund' | 'settle' | 'adjust';
  amount: string;
  balance_after: string;
  ref?: string | null;
  created_at: string;
}

function unwrapList(body: unknown): LedgerTxn[] {
  const b = body as { data?: LedgerTxn[]; items?: LedgerTxn[] } | undefined;
  return b?.data ?? b?.items ?? [];
}

// GET /credits/wallets/{wallet_id}/transactions — the ledger, in chronological order.
export function useWalletTransactions(walletId?: string) {
  return useQuery({
    queryKey: ['wallet', 'transactions', walletId],
    enabled: !!walletId,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/credits/wallets/{wallet_id}/transactions', {
        params: { path: { wallet_id: walletId as string }, query: { size: 50 } },
      });
      return unwrapList(data);
    },
  });
}

// POST /credits/topup-requests — raise a top-up request, which waits for approval. Idempotency key
// required.
export function useTopupRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { wallet_id?: string; amount: string; note?: string }) => {
      const { data } = await raw.POST('/api/v1/credits/topup-requests', {
        body,
        headers: { 'Idempotency-Key': idemKey() },
      });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['wallet'] }),
  });
}
