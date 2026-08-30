// The standard error envelope, and the mapping from an error code to a message the user can act on.
import i18n from '@/i18n';

export interface ApiError {
  code: string;
  message: string;
  status?: number;
  // The backend sends a free-form dict; validation_failed carries {errors: [pydantic errors]}.
  details?: Record<string, unknown>;
  requestId?: string;
}

export function toApiError(body: unknown, status?: number): ApiError {
  const e = (body as { error?: Record<string, unknown> })?.error ?? {};
  return {
    code: (e.code as string) ?? 'internal_error',
    message: (e.message as string) ?? i18n.t('error.unknown'),
    status,
    details: e.details as ApiError['details'],
    requestId: e.request_id as string | undefined,
  };
}

/**
 * Translate an error code. Codes without a translation fall through to the server's message, which
 * is already actionable — callers such as the session wizard branch on `code` for anything richer.
 */
export function humanizeError(err: ApiError): string {
  const key = `error.${err.code}`;
  const base = i18n.exists(key) ? i18n.t(key) : err.message;
  // Pydantic validation errors carry the offending field — surface the first one so the user
  // knows WHICH value to fix, not just that something was invalid.
  // Quota rejections carry which resource and the used/limit — surface them so the user knows it is
  // their own aggregate cap (e.g. GPU cores) that is full, not the cluster.
  if (err.code === 'quota_exceeded' && err.details) {
    const d = err.details as { resource?: string; limit?: number; used?: number; request?: number };
    if (d.resource != null && d.limit != null) {
      return i18n.t('error.quota_exceeded_detail', {
        resource: d.resource, used: d.used ?? 0, limit: d.limit, request: d.request ?? 0, defaultValue: base,
      });
    }
  }
  if (err.code === 'validation_failed') {
    const first = (err.details?.errors as { loc?: unknown[]; msg?: string }[] | undefined)?.[0];
    if (first) {
      const field = (first.loc ?? []).filter((p) => p !== 'body').join('.');
      if (field || first.msg) return `${base} (${[field, first.msg].filter(Boolean).join(': ')})`;
    }
  }
  return base;
}

// react-query types its errors as `Error`, but the client middleware throws the ApiError envelope
// for any non-success response, so that is what `catch` and `onError` actually receive. A direct
// `as ApiError` is rejected (TS2352) because the shapes do not overlap, hence this narrowing cast.
export function asApiError(e: unknown): ApiError {
  return e as ApiError;
}
