import { Link } from 'react-router-dom';
import { PageHeader } from '@/components/PageHeader';
import { CopyableId } from '@/components/CopyButton';
import { useTranslation } from 'react-i18next';
import { useSessions } from '@/api/hooks/useSessions';
import { useDashboardSummary } from '@/api/hooks/useDashboard';
import { formatCredit } from '@/lib/format';

const pct = (used?: number | null, total?: number | null) =>
  total && total > 0 ? Math.min(100, Math.round(((used ?? 0) / total) * 100)) : 0;
const gb = (mb?: number | null) => Math.round(((mb ?? 0) / 1024) * 10) / 10;

function Bar({ value, variant }: { value: number; variant?: 'gpu' | 'warn' | 'free' }) {
  return (
    <div className={`gs-bar ${variant ?? ''}`}>
      <i style={{ width: `${value}%` }} />
    </div>
  );
}

// The user dashboard: a summary of the caller's resources, credits, and GPU availability.
export function Dashboard() {
  const { t } = useTranslation();
  const { data: s, isFetching, refetch, dataUpdatedAt } = useDashboardSummary();
  const { data: sessions } = useSessions();

  const credit = s?.credit ?? { available: null, balance: null, reserved: null };
  const vram = s?.vram ?? { used_mb: 0, total_mb: 0 };
  const running = s?.sessions?.running ?? 0;
  const active = s?.sessions?.active ?? 0;
  const instLimit = s?.allocation?.instances?.total ?? Math.max(active, 1);
  const mySessions = (sessions ?? []).slice(0, 5);

  return (
    <div>
      <PageHeader
        title={t('dashboard.title')}
        description={t('dashboard.subtitle')}
        updatedAt={dataUpdatedAt || null}
        onRefresh={() => refetch()}
        isFetching={isFetching}
      />

      {/* Quick-start cards */}
      <div className="grid grid-cols-3 gap-4 mb-4 mt-4 max-[1100px]:grid-cols-1">
        {QUICK_START.map((c) => (
          <Link key={c.titleKey} to={c.to} className="gs-card hover:-translate-y-0.5 hover:border-primary transition block">
            <div className="font-bold">{t(c.titleKey)}</div>
            <p className="text-muted text-[12.5px] mt-1">{t(c.descKey)}</p>
          </Link>
        ))}
      </div>

      <div className="grid grid-cols-4 gap-4 mb-4 max-[1100px]:grid-cols-2">
        <div className="gs-card gs-stat">
          <div className="gs-label">◆ {t('dashboard.creditBalance')}</div>
          <div className="gs-value">{credit.available != null ? formatCredit(credit.available) : '—'} <small>{t('dashboard.available')}</small></div>
          <Bar value={pct(credit.available, credit.balance)} />
        </div>
        <div className="gs-card gs-stat">
          <div className="gs-label">▶ {t('dashboard.activeSessions')}</div>
          <div className="gs-value">{active} <small>/ {instLimit}</small></div>
          <Bar value={pct(active, instLimit)} variant="warn" />
        </div>
        <div className="gs-card gs-stat">
          <div className="gs-label">▦ {t('dashboard.gpuVram')}</div>
          <div className="gs-value">{gb(vram.used_mb)} <small>/ {gb(vram.total_mb)} GB</small></div>
          <Bar value={pct(vram.used_mb, vram.total_mb)} variant="gpu" />
        </div>
        <div className="gs-card gs-stat">
          <div className="gs-label">▶ {t('dashboard.runningSessions')}</div>
          <div className="gs-value">{running} <small>{t('dashboard.running')}</small></div>
          <Bar value={pct(running, instLimit)} variant="free" />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 max-[1100px]:grid-cols-1">
        <div className="gs-card">
          <h2 className="font-bold mb-1">{t('dashboard.regionAvailability')} <span className="text-muted text-[12px] font-medium">{t('dashboard.efficientPlacement')}</span></h2>
          {(s?.regions ?? []).length === 0 && <p className="text-muted text-[13px] py-2">{t('dashboard.noDevices')}</p>}
          {(s?.regions ?? []).map((r) => (
            <div key={r.model} className="flex items-center justify-between py-2.5 border-b border-border last:border-b-0">
              <div className="font-semibold text-[13px]">{r.model}</div>
              <div className="text-[13px]"><b>{r.free}</b><span className="text-muted">/{r.total}</span> {t('dashboard.free')}</div>
            </div>
          ))}
        </div>
        <div className="gs-card">
          <h2 className="font-bold mb-1">{t('dashboard.quota')}</h2>
          <div className="gs-qrow"><div className="gs-qn">{t('dashboard.instances')}</div><Bar value={pct(active, instLimit)} variant="warn" /><div className="gs-qv">{active} / {instLimit} {t('dashboard.instanceUnit')}</div></div>
          <div className="gs-qrow"><div className="gs-qn">{t('dashboard.gpuVram')}</div><Bar value={pct(vram.used_mb, vram.total_mb)} variant="gpu" /><div className="gs-qv">{gb(vram.used_mb)} / {gb(vram.total_mb)} GB</div></div>
          <div className="gs-qrow"><div className="gs-qn">{t('dashboard.runningLabel')}</div><Bar value={pct(running, instLimit)} variant="free" /><div className="gs-qv">{running} / {instLimit}</div></div>
        </div>
      </div>

      <div className="gs-card mt-4">
        <div className="flex items-center justify-between mb-2">
          <h2 className="font-bold">{t('dashboard.mySessions')}</h2>
          <Link to="/sessions" className="text-primary text-[13px] font-semibold">{t('dashboard.viewAll')}</Link>
        </div>
        {/* A preview of the newest few, not the list itself: "See all" is the way to find one,
            which is why the table carries no search box or sort of its own. */}
        {mySessions.length === 0 ? (
          <p className="text-muted text-[13px] py-2">{t('dashboard.noSessions')} <Link to="/sessions/new" className="text-primary font-semibold">{t('dashboard.startOne')}</Link></p>
        ) : (
          <table data-preview className="w-full text-[13px]" aria-label={t('dashboard.mySessions')}>
            <thead className="text-muted text-[12px] text-left">
              <tr><th className="py-1.5 font-semibold">{t('dashboard.colStatus')}</th><th className="font-semibold">{t('dashboard.colId')}</th><th className="font-semibold">{t('dashboard.colClass')}</th><th className="font-semibold">{t('dashboard.colMode')}</th></tr>
            </thead>
            <tbody>
              {mySessions.map((x) => (
                <tr key={x.id} className="border-t border-border">
                  <td className="py-2"><span className={`gs-pill ${x.status === 'running' ? 'bg-free-soft text-free' : 'bg-surface-2 text-muted'}`}>{x.status}</span></td>
                  <td className="text-[12px] text-muted"><CopyableId value={x.id} /></td>
                  <td>{x.resource_class}</td>
                  <td>{x.mode ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// The quick-start cards across the top of the dashboard.
const QUICK_START = [
  { to: '/sessions/new?class=gpu', titleKey: 'dashboard.quickGpuTitle', descKey: 'dashboard.quickGpuDesc' },
  { to: '/sessions/new?class=cpu', titleKey: 'dashboard.quickCpuTitle', descKey: 'dashboard.quickCpuDesc' },
  { to: '/data', titleKey: 'dashboard.quickDataTitle', descKey: 'dashboard.quickDataDesc' },
];
