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
  type: 'topup' | 'hold' | 'consume' | 'refund' | 'settle' | 'adjust' | 'storage';
  amount: string;
  balance_after: string;
  ref?: string | null;
  ref_name?: string | null;
  created_at: string;
  /** > 1 when the row rolls up a session's per-minute consume entries. */
  entry_count?: number;
  period_start?: string | null;
  live?: boolean;
  period_end?: string | null;
  /** The session's billing is closed; its zero-amount settle marker folded into this row. */
  settled?: boolean;
}

function unwrapList(body: unknown): LedgerTxn[] {
  // The transactions endpoint returns a BARE ARRAY; envelope shapes are kept for compatibility.
  if (Array.isArray(body)) return body as LedgerTxn[];
  const b = body as { data?: LedgerTxn[]; items?: LedgerTxn[] } | undefined;
  return b?.data ?? b?.items ?? [];
}

// GET /credits/wallets/{wallet_id}/transactions — the ledger, in chronological order.
export function useWalletTransactions(walletId?: string) {
  return useQuery({
    queryKey: ['wallet', 'transactions', walletId],
    enabled: !!walletId,
    // Live billing rows: the worker mints a row per minute, so refetch keeps amounts within
    // seconds of the ledger while the per-second feel comes from the client-side duration tick.
    refetchInterval: 15000,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/credits/wallets/{wallet_id}/transactions', {
        // Rolled up per session: a day of billing is one line, not 1,440.
        params: { path: { wallet_id: walletId as string }, query: { size: 50, group: 'session' } },
      });
      return unwrapList(data);
    },
  });
}

// GET /credits/topup-requests — for a member the backend scopes this to the requests THEY raised,
// so the wallet can show the status of a system top-up next to allocation requests.
export interface MyTopupRequest {
  id: string;
  amount: string;
  status: string;
  note?: string | null;
  decided_reason?: string | null;  // why it was rejected — shown to the requester
  created_at?: string;
}
export function useMyTopupRequests() {
  return useQuery({
    queryKey: ['topup-requests', 'mine'],
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/credits/topup-requests', { params: { query: { page: 1, size: 100 } } });
      return ((data as { data?: MyTopupRequest[] } | undefined)?.data ?? []);
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

// GET /credits/wallets/{wallet_id}/spend-daily — daily spend for the usage chart, aggregated
// server-side (a chart from page one of the ledger would silently truncate).
export interface SpendDay { date: string; amount: number }
export function useSpendDaily(walletId: string | undefined, from: string, to: string) {
  return useQuery({
    queryKey: ['wallet', 'spend-daily', walletId, from, to],
    enabled: !!walletId && !!from && !!to,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/credits/wallets/{wallet_id}/spend-daily', {
        params: {
          path: { wallet_id: walletId as string },
          // Buckets follow the viewer's clock, not UTC.
          query: { from, to, tz_offset_min: -new Date().getTimezoneOffset() },
        },
      });
      return (data ?? []) as SpendDay[];
    },
  });
}
