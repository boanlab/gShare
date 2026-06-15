import { useClusterMetrics, useDashboardSummary } from '@/api/hooks/useAdminDashboard';
import { PageHeader } from '@/components/PageHeader';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/auth/authStore';
import { formatVram } from '@/lib/format';

// The admin dashboard, backed by GET /metrics/cluster and /dashboard/summary.
// Cluster-wide utilisation, VRAM packing, sessions, node health, and credit usage as KPI cards.

function Stat({ label, value, unit, barPct, barKind, legend }: {
  label: string;
  value: string | number;
  unit?: string;
  barPct?: number;
  barKind?: 'gpu' | 'warn' | 'plain';
  legend?: string;
}) {
  const barColor = barKind === 'warn' ? 'bg-warn' : barKind === 'plain' ? 'bg-border' : 'bg-gpu';
  return (
    <div className="gs-card">
      <div className="text-[12px] text-muted font-semibold">{label}</div>
      <div className="text-2xl font-extrabold mt-1">
        {value}
        {unit && <small className="text-muted text-[13px] font-semibold ml-1">{unit}</small>}
      </div>
      {barPct != null && (
        <div className="mt-2 h-1.5 rounded-full bg-surface-2 overflow-hidden">
          <div className={`h-full ${barColor}`} style={{ width: `${Math.min(100, Math.max(0, barPct))}%` }} />
        </div>
      )}
      {legend && <div className="text-[11.5px] text-muted mt-1.5">{legend}</div>}
    </div>
  );
}

export function AdminDashboard() {
  const { t } = useTranslation();
  // /metrics/cluster is super_admin only, so other roles never make the call and see a scoped
  // summary instead.
  const isSuper = useAuthStore((s) => s.claims.global_role === 'super_admin');
  const { data: m, isLoading, isError, isFetching: metricsFetching, refetch: refetchMetrics, dataUpdatedAt: metricsUpdatedAt } = useClusterMetrics({}, { enabled: isSuper });
  const { data: summary, isFetching, refetch, dataUpdatedAt } = useDashboardSummary();

  const vramPct = m && m.gpu.vram_total_mb > 0 ? (m.gpu.vram_used_mb / m.gpu.vram_total_mb) * 100 : 0;
  const nodeReadyPct = m && m.nodes.total > 0 ? ((m.nodes.ready + m.nodes.busy) / m.nodes.total) * 100 : 0;
  const healthAlerts = m ? m.nodes.cordoned + m.nodes.offline : 0;

  // For an org_admin or group_admin: a summary of their own scope in place of the cluster KPIs.
  if (!isSuper) {
    const ru = summary?.resource_usage;
    return (
      <div>
        <PageHeader
          title={t('admin.dashboard.title')}
          description={t('admin.dashboard.scopedSubtitle')}
          updatedAt={dataUpdatedAt || null}
          onRefresh={() => refetch()}
          isFetching={isFetching}
        />
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <Stat label={`▶ ${t('admin.dashboard.runningSessions')}`} value={ru?.running_sessions ?? 0} />
          <Stat label={`⟳ ${t('admin.dashboard.queuedSessions')}`} value={ru?.queued_sessions ?? 0} />
          <Stat label={`◆ ${t('admin.dashboard.vramInUse')}`} value={ru ? formatVram(ru.gpu_mem_mb_in_use) : '-'} />
          <Stat label={`◴ ${t('admin.dashboard.activeToday')}`} value={ru?.active_minutes_today ?? 0} unit={t('admin.dashboard.minutes')} />
        </div>
        <p className="text-muted text-[12px] mt-4">{t('admin.dashboard.superOnly')}</p>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title={t('admin.dashboard.title')}
        description={t('admin.dashboard.globalSubtitle')}
        updatedAt={metricsUpdatedAt || null}
        onRefresh={() => { void refetchMetrics(); void refetch(); }}
        isFetching={metricsFetching}
      />

      {isLoading ? (
        <div className="gs-card"><p className="text-muted">{t('common.loading')}</p></div>
      ) : isError || !m ? (
        <div className="gs-card"><p className="text-danger">{t('admin.dashboard.loadFailed')}</p></div>
      ) : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <Stat label={`▦ ${t('admin.dashboard.gpuUtil')}`} value={m.gpu.avg_utilization_pct.toFixed(0)} unit="%" barPct={m.gpu.avg_utilization_pct} barKind="gpu" />
            <Stat label={`◆ ${t('admin.dashboard.vramPacking')}`} value={vramPct.toFixed(0)} unit="%" barPct={vramPct} barKind="gpu" legend={t('admin.dashboard.binpackEfficiency')} />
            <Stat label={`▶ ${t('admin.dashboard.activeSessions')}`} value={m.sessions.running} unit={`/ ${m.sessions.running + m.sessions.queued}`} barPct={nodeReadyPct} barKind="warn" legend={t('admin.dashboard.runningOfTotal')} />
            <Stat label={`⟳ ${t('admin.dashboard.queue')}`} value={m.sessions.queued} unit={t('admin.dashboard.waiting')} barPct={m.sessions.queued > 0 ? 40 : 0} barKind="plain" />
          </div>

          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mt-4">
            <Stat label={`◴ ${t('admin.dashboard.nodesUp')}`} value={`${m.nodes.ready + m.nodes.busy}`} unit={`/ ${m.nodes.total}`} legend={`${m.nodes.cordoned} cordon · ${m.nodes.offline} offline`} />
            <Stat label={`◇ ${t('admin.dashboard.creditsLast24h')}`} value={m.credit.consumed_last_24h} unit="C" legend={t('admin.dashboard.activeHolds', { amount: m.credit.active_holds })} />
            <Stat label={`◆ ${t('admin.dashboard.emptyGpus')}`} value={m.gpu.empty_gpu_count} unit={`/ ${m.gpu.device_total}`} legend={t('admin.dashboard.unpackedDevices')} />
            <Stat label={`⚠ ${t('admin.dashboard.healthAlerts')}`} value={healthAlerts} legend={healthAlerts > 0 ? t('admin.dashboard.cordonOffline') : t('admin.dashboard.healthy')} />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
            <div className="gs-card">
              <h2 className="font-bold mb-3">{t('admin.dashboard.nodeDistribution')} <span className="text-muted text-[12px] font-normal">{t('admin.dashboard.totalNodes', { count: m.nodes.total })}</span></h2>
              <dl className="grid grid-cols-2 gap-y-2 text-[13px]">
                <dt className="text-muted">ready</dt><dd className="font-semibold">{m.nodes.ready}</dd>
                <dt className="text-muted">busy</dt><dd className="font-semibold">{m.nodes.busy}</dd>
                <dt className="text-muted">cordoned</dt><dd className="font-semibold">{m.nodes.cordoned}</dd>
                <dt className="text-muted">offline</dt><dd className="font-semibold">{m.nodes.offline}</dd>
              </dl>
            </div>
            <div className="gs-card">
              <h2 className="font-bold mb-3">{t('admin.dashboard.vramResources')} <span className="text-muted text-[12px] font-normal">{t('admin.dashboard.dcgmAggregate')}</span></h2>
              <dl className="grid grid-cols-2 gap-y-2 text-[13px]">
                <dt className="text-muted">{t('admin.dashboard.deviceCount')}</dt><dd className="font-semibold">{m.gpu.device_total}</dd>
                <dt className="text-muted">{t('admin.dashboard.totalVram')}</dt><dd className="font-semibold">{formatVram(m.gpu.vram_total_mb)}</dd>
                <dt className="text-muted">{t('admin.dashboard.usedVram')}</dt><dd className="font-semibold">{formatVram(m.gpu.vram_used_mb)}</dd>
                <dt className="text-muted">{t('admin.dashboard.avgUtil')}</dt><dd className="font-semibold">{m.gpu.avg_utilization_pct.toFixed(1)}%</dd>
                {summary?.resource_usage && (
                  <>
                    <dt className="text-muted">{t('admin.dashboard.myRunningSessions')}</dt><dd className="font-semibold">{summary.resource_usage.running_sessions}</dd>
                  </>
                )}
              </dl>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
