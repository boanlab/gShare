import { type ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useDocumentTitle } from '@/hooks/useDocumentTitle';

export interface Crumb {
  label: string;
  to?: string;   // absent marks the current screen
}

/**
 * Screen heading: one `<h1>`, breadcrumb trail, data freshness line, actions, and the document
 * title. `crumbs` is expected on anything below the top level of a console.
 */
export function PageHeader({
  title, crumbs, description, actions, updatedAt, onRefresh, isFetching,
}: {
  title: string;
  crumbs?: Crumb[];
  description?: ReactNode;
  actions?: ReactNode;
  /** When the data on screen was last fetched. Renders a freshness line with a refresh control. */
  updatedAt?: number | null;
  onRefresh?: () => void;
  isFetching?: boolean;
}) {
  const { t } = useTranslation();
  useDocumentTitle(title);

  return (
    <header className="mb-5">
      {crumbs && crumbs.length > 0 && <Breadcrumbs crumbs={crumbs} />}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0">
          <h1 className="text-xl font-extrabold truncate">{title}</h1>
          {description && <p className="text-muted text-[13px] mt-1">{description}</p>}
          {(updatedAt !== undefined || onRefresh) && (
            <p className="text-muted text-[11.5px] mt-1 flex items-center gap-2">
              <span aria-live="polite">
                {isFetching
                  ? t('common.refreshing')
                  : updatedAt
                    ? t('common.updatedAt', { time: new Date(updatedAt).toLocaleTimeString() })
                    : t('common.notLoadedYet')}
              </span>
              {onRefresh && (
                <button
                  type="button"
                  className="gs-btn gs-btn-sm"
                  onClick={onRefresh}
                  disabled={isFetching}
                  title={t('common.refresh')}
                >
                  ↻ {t('common.refresh')}
                </button>
              )}
            </p>
          )}
        </div>
        {actions && <div className="flex items-center gap-2 flex-wrap">{actions}</div>}
      </div>
    </header>
  );
}

function Breadcrumbs({ crumbs }: { crumbs: Crumb[] }) {
  const { t } = useTranslation();
  return (
    <nav data-breadcrumb aria-label={t('common.breadcrumb')} className="mb-2">
      <ol className="flex items-center gap-1.5 text-[12px] text-muted flex-wrap">
        {crumbs.map((c, i) => (
          <li key={`${c.label}-${i}`} className="flex items-center gap-1.5">
            {c.to ? (
              <Link to={c.to} className="hover:text-text hover:underline">{c.label}</Link>
            ) : (
              <span aria-current="page" className="text-text font-semibold">{c.label}</span>
            )}
            {i < crumbs.length - 1 && <span aria-hidden="true">/</span>}
          </li>
        ))}
      </ol>
    </nav>
  );
}

/** Link up to the parent screen. */
export function BackLink({ to, label }: { to: string; label?: string }) {
  const { t } = useTranslation();
  const location = useLocation();
  return (
    <Link to={to} state={{ from: location.pathname }} className="gs-btn gs-btn-sm">
      ← {label ?? t('common.back')}
    </Link>
  );
}
