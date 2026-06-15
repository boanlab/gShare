import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, idemKey } from '@/api/client';

// Wallets, top-up requests, and the settlement report.
// Every POST that moves funds (topup, adjust, transfer, approve) requires an idempotency key.

export const billingKeys = {
  wallets: (f: object) => ['credits', 'wallets', f] as const,
  topupRequests: (f: object) => ['credits', 'topup-requests', f] as const,
  report: (q: object) => ['metrics', 'billing-report', q] as const,
};

export interface WalletFilter {
  owner_type?: 'user' | 'group';
  owner_id?: string;
  page?: number;
  size?: number;
}

// GET /credits/wallets — the wallet list, for billing and system administrators. Returns
// WalletRead[].
export function useWallets(filter: WalletFilter = {}) {
  return useQuery({
    queryKey: billingKeys.wallets(filter),
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/credits/wallets', { params: { query: filter } });
      return data ?? [];
    },
  });
}

export interface TopupRequestFilter {
  status?: 'pending' | 'approved' | 'rejected';
  wallet_id?: string;
  page?: number;
  size?: number;
}

// GET /credits/topup-requests — the top-up request inbox that drives the approval workflow.
export function useTopupRequests(filter: TopupRequestFilter = {}) {
  return useQuery({
    queryKey: billingKeys.topupRequests(filter),
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/credits/topup-requests', { params: { query: filter } });
      return data ?? { data: [] };
    },
  });
}

export interface TopupBody {
  walletId: string;
  amount: string;
  reason?: string;
  ref?: string;
}

// POST /credits/wallets/{id}/topup — credit a wallet. Idempotency key required; always positive.
export function useTopup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ walletId, ...body }: TopupBody) => {
      const { data } = await api.POST('/api/v1/credits/wallets/{wallet_id}/topup', {
        params: { path: { wallet_id: walletId } },
        body,
        headers: { 'Idempotency-Key': idemKey() },
      });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['credits'] }),
  });
}

export interface AdjustBody {
  walletId: string;
  amount: string; // non-zero; may be negative
  reason: string; // required for the audit trail
}

// POST /credits/wallets/{id}/adjust — an operator correction. Idempotency key and reason required.
export function useAdjust() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ walletId, ...body }: AdjustBody) => {
      const { data } = await api.POST('/api/v1/credits/wallets/{wallet_id}/adjust', {
        params: { path: { wallet_id: walletId } },
        body,
        headers: { 'Idempotency-Key': idemKey() },
      });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['credits'] }),
  });
}

export interface TransferBody {
  walletId: string; // from
  to_wallet_id: string;
  amount: string; // >0
  reason: string; // required for the audit trail
}

// POST /credits/wallets/{id}/transfer — move credits between wallets in one transaction.
// Idempotency key required.
export function useTransfer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ walletId, ...body }: TransferBody) => {
      const { data } = await api.POST('/api/v1/credits/wallets/{wallet_id}/transfer', {
        params: { path: { wallet_id: walletId } },
        body,
        headers: { 'Idempotency-Key': idemKey() },
      });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['credits'] }),
  });
}

// POST /credits/topup-requests/{id}/approve — approving performs the top-up in the same
// transaction. Idempotency key required.
export function useApproveTopupRequest() {
  const qc = useQueryClient();
  return useMutation({
    // The backend takes no request body here (a note is not accepted), so only the path and the
    // idempotency key are sent.
    mutationFn: async ({ id }: { id: string; note?: string }) => {
      const { data } = await api.POST('/api/v1/credits/topup-requests/{request_id}/approve', {
        params: { path: { request_id: id } },
        headers: { 'Idempotency-Key': idemKey() },
      });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['credits'] }),
  });
}

// POST /credits/topup-requests/{id}/reject — reject with a mandatory reason. No funds move.
export function useRejectTopupRequest() {
  const qc = useQueryClient();
  return useMutation({
    // The backend stores the reason on topup_request.decided_reason.
    mutationFn: async ({ id, reason }: { id: string; reason: string }) => {
      const { data } = await api.POST('/api/v1/credits/topup-requests/{request_id}/reject', {
        params: { path: { request_id: id } },
        body: { reason },
      });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['credits'] }),
  });
}

export interface BillingReportQuery {
  scope: 'org' | 'group' | 'wallet';
  scope_id?: string;
  from: string; // ISO 8601, required
  to: string; // ISO 8601, required
  group_by?: 'group' | 'offering' | 'wallet';
  format?: 'json' | 'csv';
}

// GET /metrics/billing-report — the settlement and billing report.
// Disabled until from and to are set, which avoids a 400 invalid_query_parameter.
export function useBillingReport(query: BillingReportQuery | undefined) {
  return useQuery({
    queryKey: billingKeys.report((query as unknown as Record<string, unknown>) ?? {}),
    enabled: !!query?.from && !!query?.to,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/metrics/billing-report', {
        params: {
          query: {
            scope: query!.scope,
            scope_id: query!.scope_id,
            from: query!.from,
            to: query!.to,
            group_by: query!.group_by,
            format: 'json',
          },
        },
      });
      return data;
    },
  });
}
