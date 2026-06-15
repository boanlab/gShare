// The standard error envelope, and the mapping from an error code to a message the user can act on.
import i18n from '@/i18n';

export interface ApiError {
  code: string;
  message: string;
  status?: number;
  details?: { field: string; reason: string }[];
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
  return i18n.exists(key) ? i18n.t(key) : err.message;
}

// react-query types its errors as `Error`, but the client middleware throws the ApiError envelope
// for any non-success response, so that is what `catch` and `onError` actually receive. A direct
// `as ApiError` is rejected (TS2352) because the shapes do not overlap, hence this narrowing cast.
export function asApiError(e: unknown): ApiError {
  return e as ApiError;
}
