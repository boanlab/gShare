import { useTranslation } from 'react-i18next';
import { useUiStore, type Toast } from '@/store/uiStore';

const KIND_CLASS: Record<Toast['kind'], string> = {
  info: 'bg-surface text-text border-border',
  success: 'bg-free-soft text-free border-free',
  warn: 'bg-warn-soft text-warn border-warn',
  error: 'bg-danger-soft text-danger border-danger',
};

// Host that renders the toast stack.
export function ToastHost() {
  const { t: translate } = useTranslation();
  const toasts = useUiStore((s) => s.toasts);
  const dismiss = useUiStore((s) => s.dismissToast);
  const dismissLabel = translate('common.dismiss');
  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`rounded-card border px-4 py-3 shadow-card text-[13px] font-semibold flex items-center gap-3 ${KIND_CLASS[t.kind]}`}
          role="status"
        >
          <span className="flex-1 min-w-0">{t.message}</span>
          {t.action && (
            <button
              type="button"
              className="gs-btn gs-btn-sm shrink-0"
              onClick={() => { t.action?.run(); dismiss(t.id); }}
            >
              {t.action.label}
            </button>
          )}
          <button
            type="button"
            className="shrink-0 opacity-60 hover:opacity-100"
            onClick={() => dismiss(t.id)}
            aria-label={dismissLabel}
            title={dismissLabel}
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}
