import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, idemKey } from '@/api/client';

// Hierarchical credit allocation and requests, from user through group and organization up to
// super_admin. Not described in schema.d.ts, so these use the loose accessor.
const raw = api as unknown as {
  GET: (p: string, o?: { params?: { query?: Record<string, unknown> } }) => Promise<{ data?: unknown }>;
  POST: (p: string, o?: { body?: unknown; params?: { path?: Record<string, string> }; headers?: Record<string, string> }) => Promise<{ data?: unknown }>;
};

function unwrap(d: unknown): Array<Record<string, unknown>> {
  if (Array.isArray(d)) return d as Array<Record<string, unknown>>;
  const o = d as { data?: unknown[]; items?: unknown[] } | undefined;
  return (o?.data ?? o?.items ?? []) as Array<Record<string, unknown>>;
}

export interface AllocRequest {
  id: string; requester_id: string; requester_name?: string | null; target_wallet_id: string;
  level: 'user' | 'group' | 'org'; fulfiller_scope: 'group' | 'org' | 'system';
  fulfiller_id?: string | null; fulfiller_name?: string | null; group_name?: string | null; amount: string; status: string; note?: string | null;
  parent_id?: string | null; created_at?: string;
}

export function useAllocationRequests(box: 'incoming' | 'mine') {
  return useQuery({
    queryKey: ['alloc-reqs', box],
    refetchInterval: 8000,
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/credits/allocation-requests', { params: { query: { box, size: 100 } } });
      return unwrap(data) as unknown as AllocRequest[];
    },
  });
}

export function useCreateAllocationRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { amount: string; level: 'user' | 'group' | 'org'; group_id?: string; org_id?: string; note?: string }) => {
      const { data } = await raw.POST('/api/v1/credits/allocation-requests', { body });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alloc-reqs'] }),
  });
}

function reqAction(action: 'approve' | 'reject' | 'escalate') {
  return function useReqAction() {
    const qc = useQueryClient();
    return useMutation({
      mutationFn: async ({ id, body }: { id: string; body?: unknown }) => {
        const { data } = await raw.POST(`/credits/allocation-requests/{id}/${action}` as string, {
          params: { path: { id } },
          body: body ?? {},
          headers: { 'Idempotency-Key': idemKey() },
        });
        return data;
      },
      onSuccess: () => { qc.invalidateQueries({ queryKey: ['alloc-reqs'] }); qc.invalidateQueries({ queryKey: ['alloc-wallets'] }); qc.invalidateQueries({ queryKey: ['alloc-scope'] }); },
    });
  };
}
export const useApproveRequest = reqAction('approve');
export const useRejectRequest = reqAction('reject');
export const useEscalateRequest = reqAction('escalate');

// Allocate or reclaim: the hierarchical form of a transfer, taking a source wallet, a target
// wallet, and an amount.
export function useAllocate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { from_wallet_id: string; to_wallet_id: string; amount: string; reason?: string }) => {
      const { data } = await raw.POST('/api/v1/credits/allocate', { body, headers: { 'Idempotency-Key': idemKey() } });
      return data;
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['alloc-wallets'] }); qc.invalidateQueries({ queryKey: ['alloc-scope'] }); },
  });
}

// Mint: a super_admin credits a wallet, usually an organization's, directly. This is the top of the
// system, issuing new credits with no source wallet (POST /credits/wallets/{id}/topup).
export function useTopupWallet() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ walletId, amount, note }: { walletId: string; amount: string; note?: string }) => {
      const { data } = await raw.POST('/api/v1/credits/wallets/{wallet_id}/topup' as string, {
        params: { path: { wallet_id: walletId } },
        body: { amount, note },
        headers: { 'Idempotency-Key': idemKey() },
      });
      return data;
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['alloc-scope'] }); qc.invalidateQueries({ queryKey: ['alloc-wallets'] }); },
  });
}

// Set a child wallet's monthly automatic refill, from the administrator one level up. 0 disables it.
export function useSetMonthlyGrant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ walletId, amount }: { walletId: string; amount: string }) => {
      const { data } = await raw.POST('/api/v1/credits/wallets/{id}/monthly-grant' as string, {
        params: { path: { id: walletId } },
        body: { amount },
      });
      return data;
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['alloc-scope'] }); qc.invalidateQueries({ queryKey: ['alloc-wallets'] }); },
  });
}

export interface ScopeWallet { wallet_id: string; balance: string; monthly_grant?: string; scope: 'org' | 'group' | 'user'; name: string }
export interface SystemTotal { wallet_id: string; balance: string; monthly_total: string; org_grant_sum: string; remaining: string }
export interface AllocationScope { pools: ScopeWallet[]; children: Record<string, ScopeWallet[]>; system?: SystemTotal | null }

// Role-scoped: the pools the caller can allocate from, each pool's targets with their names, and —
// for a super_admin — the system's monthly ceiling.
export function useAllocationScope() {
  return useQuery({
    queryKey: ['alloc-scope'],
    queryFn: async () => {
      const { data } = await raw.GET('/api/v1/credits/allocation-scope');
      return (data as AllocationScope) ?? { pools: [], children: {}, system: null };
    },
  });
}
