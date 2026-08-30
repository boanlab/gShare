import { type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useDocumentTitle } from '@/hooks/useDocumentTitle';

export interface Crumb {
  label: string;
  to?: string;   // absent marks the current screen
}

/**
 * Screen heading: one `<h1>`, breadcrumb trail, actions, and the document title. `crumbs` is
 * expected on anything below the top level of a console.
 *
 * There is deliberately no "last updated / refresh" line: every screen either polls, streams over
 * SSE, or refetches on focus, so the timestamp only ever told the user how stale react-query's
 * cache was, and the button duplicated what the browser reload already does.
 */
export function PageHeader({
  title, crumbs, description, actions,
}: {
  title: string;
  crumbs?: Crumb[];
  description?: ReactNode;
  actions?: ReactNode;
}) {
  useDocumentTitle(title);

  return (
    <header className="mb-6">
      {crumbs && crumbs.length > 0 && <Breadcrumbs crumbs={crumbs} />}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0">
          <h1 className="text-[22px] leading-tight font-bold tracking-[-0.02em] truncate">{title}</h1>
          {description && <p className="text-muted text-sm mt-1.5 max-w-[68ch]">{description}</p>}
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
      <ol className="flex items-center gap-1.5 text-xs text-muted flex-wrap">
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
