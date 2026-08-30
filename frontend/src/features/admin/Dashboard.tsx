import { Link } from 'react-router-dom';
import { useClusterMetrics, useDashboardSummary } from '@/api/hooks/useAdminDashboard';
import { useGpuDevices } from '@/api/hooks/useNodes';
import { useAuditLogs } from '@/api/hooks/useAudit';
import { actionLabel, resultMeta, targetDisplay, changesSummary } from '@/features/admin/Audit';
import { PageHeader } from '@/components/PageHeader';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/auth/authStore';
import { formatVram } from '@/lib/format';
import { ErrorState } from '@/components/EmptyState';
import { Figure } from '@/components/Figure';
import { Meter } from '@/components/Meter';
import { Timestamp } from '@/components/Timestamp';
import type { ReactNode } from 'react';

// The admin dashboard, backed by GET /metrics/cluster, /sessions/gpu-availability, and the audit
// trail. One hero KPI band, a per-card GPU grid (the fleet's actual shape), node/compute pressure,
// and a recent-activity feed — patterned after infra monitoring consoles rather than a card pile.

/** A labelled figure inside a side panel. */
function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="gs-hair flex items-center justify-between gap-4 py-2">
      <span className="text-muted text-sm">{label}</span>
      <span className="gs-num font-semibold text-sm">{value}</span>
    </div>
  );
}

/** One capacity row: label, mono reading, and the fill underneath. */
function CapacityRow({ label, reading, pct, variant = 'primary' }: {
  label: string;
  reading: string;
  pct: number;
  variant?: 'primary' | 'free' | 'warn' | 'danger';
}) {
  return (
    <div className="py-2">
      <div className="flex items-baseline justify-between gap-4 mb-1.5">
        <span className="text-muted text-xs font-semibold">{label}</span>
        <span className="gs-num text-xs">{reading}</span>
      </div>
      <Meter value={pct} variant={variant} />
    </div>
  );
}

/** Strip vendor noise so tiles read as "RTX 4090 #1", not a spec sheet. */
function shortModel(model: string): string {
  return model.replace(/^NVIDIA\s+(GeForce\s+)?/, '');
}

/** One GPU card: VRAM fill, free cores, mode — the rack view. */
function DeviceTile({ model, index, freeMemMb, totalMemMb, freeCores, mode }: {
  model: string;
  index: number;
  freeMemMb: number;
  totalMemMb: number;
  freeCores: number;
  mode: string;
}) {
  const { t } = useTranslation();
  const usedPct = totalMemMb > 0 ? ((totalMemMb - freeMemMb) / totalMemMb) * 100 : 0;
  const variant = usedPct >= 90 ? 'danger' : usedPct >= 70 ? 'warn' : 'primary';
  return (
    <div className="rounded-ctl border border-border bg-surface-2/40 px-3 py-2.5 min-w-0">
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <span className="text-xs font-bold truncate">{shortModel(model)} <span className="text-muted gs-num">#{index + 1}</span></span>
        <span className="gs-tag shrink-0">{t(`enum.deviceMode.${mode}`, { defaultValue: mode })}</span>
      </div>
      <Meter value={usedPct} variant={variant} />
      <div className="flex items-center justify-between gap-2 mt-1.5 text-2xs gs-num text-muted">
        <span>{formatVram(totalMemMb - freeMemMb)} / {formatVram(totalMemMb)}</span>
        <span>{t('admin.dashboard.deviceFreeShort')} {freeCores}%</span>
      </div>
    </div>
  );
}

export function AdminDashboard() {
  const { t } = useTranslation();
  // /metrics/cluster is super_admin only, so other roles never make the call and see a scoped
  // summary instead.
  const isSuper = useAuthStore((s) => s.claims.global_role === 'super_admin');
  const { data: m, isLoading, isError, error, refetch: refetchMetrics } = useClusterMetrics({}, { enabled: isSuper });
  const { data: summary } = useDashboardSummary();
  // The fleet inventory, NOT /sessions/gpu-availability: that endpoint applies the caller's
  // node-pool access, which hid pool-granted cards from the admin's own grid.
  const { data: fleetDevices = [] } = useGpuDevices();
  const auditQ = useAuditLogs(isSuper ? { size: 8, sort: '-at' } : {});
  const auditRows = (auditQ.data?.data ?? []) as Array<{
    id: string; action: string; actor_id?: string; actor_name?: string; result?: string; at?: string;
    target: string; target_name?: string; detail?: Record<string, unknown>;
  }>;
  const auditNames = ((auditQ.data as { names?: Record<string, string> } | undefined)?.names) ?? {};

  const vramPct = m && m.gpu.vram_total_mb > 0 ? (m.gpu.vram_used_mb / m.gpu.vram_total_mb) * 100 : 0;
  const healthAlerts = m ? m.nodes.cordoned + m.nodes.offline : 0;

  // For an org_admin or group_admin: their own summary (same endpoint as the user dashboard) in
  // place of the super_admin-only cluster KPIs.
  if (!isSuper) {
    return (
      <div>
        <PageHeader
          title={t('admin.dashboard.title')}
          description={t('admin.dashboard.scopedSubtitle')}
        />
        <section className="gs-panel grid md:grid-cols-4">
          <Figure label={t('admin.dashboard.runningSessions')} value={summary?.sessions.running ?? 0} />
          <Figure label={t('admin.dashboard.activeSessions')} value={summary?.sessions.active ?? 0} />
          <Figure label={t('admin.dashboard.vramInUse')} value={summary ? formatVram(summary.vram.used_mb) : '-'} />
          <Figure
            label={t('admin.dashboard.computeCpu')}
            value={summary?.compute.cpu.used ?? 0}
            unit={summary?.compute.cpu.limit ? `/ ${summary.compute.cpu.limit} vCPU` : 'vCPU'}
          />
        </section>
        <p className="text-muted text-xs mt-4">{t('admin.dashboard.superOnly')}</p>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title={t('admin.dashboard.title')}
        description={t('admin.dashboard.globalSubtitle')}
      />

      {isLoading ? (
        <div className="gs-panel p-5"><p className="text-muted">{t('common.loading')}</p></div>
      ) : isError || !m ? (
        <div className="gs-panel"><ErrorState error={error} onRetry={() => refetchMetrics()} /></div>
      ) : (
        <>
          {/* The lead band: the six numbers an operator on call reads first. Active sessions alone
              gets the accent ink. */}
          <section className="gs-panel grid md:grid-cols-3 xl:grid-cols-6 mb-5">
            <Figure hero label={t('admin.dashboard.activeSessions')} value={m.sessions.running} foot={t('admin.dashboard.runningOfTotal')} />
            <Figure label={t('admin.dashboard.queue')} value={m.sessions.queued} unit={t('admin.dashboard.waiting')} />
            <Figure label={t('admin.dashboard.gpuUtil')} value={m.gpu.avg_utilization_pct.toFixed(0)} unit="%"
              foot={t('admin.dashboard.gpuUtilBasis')} bar={{ value: m.gpu.avg_utilization_pct }} />
            <Figure label={t('admin.dashboard.vramPacking')} value={vramPct.toFixed(0)} unit="%" bar={{ value: vramPct }} foot={`${formatVram(m.gpu.vram_used_mb)} / ${formatVram(m.gpu.vram_total_mb)}`} />
            <Figure label={t('admin.dashboard.emptyGpus')} value={m.gpu.empty_gpu_count} unit={`/ ${m.gpu.device_total}`} foot={t('admin.dashboard.unpackedDevices')} />
            <Figure label={t('admin.dashboard.creditsLast24h')} value={m.credit.consumed_last_24h} unit="C" foot={t('admin.dashboard.activeHolds', { amount: m.credit.active_holds })} />
          </section>

          {/* The fleet's actual shape: one tile per physical card — full row. */}
          <section className="gs-panel p-5 mb-5">
              <div className="flex items-baseline justify-between gap-2">
                <h2 className="gs-h2">{t('admin.dashboard.deviceGridTitle')}</h2>
                <Link to="/admin/gpus" className="text-primary text-xs font-semibold hover:underline">
                  {t('admin.dashboard.deviceGridLink')}
                </Link>
              </div>
              <p className="gs-sub mt-1">{t('admin.dashboard.deviceGridSub')}</p>
              {fleetDevices.length === 0 ? (
                <p className="text-muted text-sm mt-4">{t('admin.dashboard.unpackedDevices')}</p>
              ) : (
                <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-2.5">
                  {(() => {
                    const perModel: Record<string, number> = {};
                    return fleetDevices.map((d) => {
                      const model = d.model ?? '-';
                      const idx = (perModel[model] = (perModel[model] ?? 0) + 1) - 1;
                      const total = d.total_mem_mb ?? 0;
                      const used = d.used_mem_mb ?? 0;
                      return (
                        <DeviceTile
                          key={d.gpu_uuid ?? `${model}-${idx}`}
                          model={model}
                          index={idx}
                          freeMemMb={Math.max(0, total - used)}
                          totalMemMb={total}
                          freeCores={Math.max(0, 100 - (d.used_cores ?? 0))}
                          mode={d.mode ?? '-'}
                        />
                      );
                    });
                  })()}
                </div>
              )}
          </section>

          {/* Fleet pressure: node states | host compute | storage pool. */}
          <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-5 mb-5">
              <section className="gs-panel p-5">
                {/* The counts below already carry the split; a stacked bar on top only made this
                    card taller than the one beside it. The total moves up next to the title. */}
                <div className="flex items-baseline justify-between gap-2">
                  <h2 className="gs-h2">{t('admin.dashboard.nodeStateBar')}</h2>
                  <span className="text-muted text-xs">{t('admin.dashboard.totalNodes', { count: m.nodes.total })}</span>
                </div>
                <div className="mt-2">
                  <Row label={t('admin.dashboard.stateBusy')} value={m.nodes.busy} />
                  <Row label={t('admin.dashboard.stateReady')} value={m.nodes.ready} />
                  <Row label={t('admin.dashboard.stateCordoned')} value={m.nodes.cordoned} />
                  <Row label={t('admin.dashboard.stateOffline')} value={m.nodes.offline} />
                  <Row
                    label={t('admin.dashboard.healthAlerts')}
                    value={<span className={healthAlerts > 0 ? 'text-danger' : 'text-free'}>{healthAlerts}</span>}
                  />
                </div>
              </section>

              {m.compute && (
                <section className="gs-panel p-5">
                  <h2 className="gs-h2">{t('admin.dashboard.capacityTitle')}</h2>
                  <div className="mt-1">
                    <CapacityRow
                      label={t('admin.dashboard.computeCpu')}
                      reading={`${m.compute.cpu.used} / ${m.compute.cpu.total} vCPU`}
                      pct={m.compute.cpu.total > 0 ? (m.compute.cpu.used / m.compute.cpu.total) * 100 : 0}
                      variant="warn"
                    />
                    <CapacityRow
                      label={t('admin.dashboard.computeMem')}
                      reading={`${m.compute.mem_gb.used} / ${m.compute.mem_gb.total} GiB`}
                      pct={m.compute.mem_gb.total > 0 ? (m.compute.mem_gb.used / m.compute.mem_gb.total) * 100 : 0}
                      variant="warn"
                    />
                    <CapacityRow
                      label={t('admin.dashboard.computeDisk')}
                      reading={`${m.compute.disk_gb.used} / ${m.compute.disk_gb.total} GB`}
                      pct={m.compute.disk_gb.total > 0 ? (m.compute.disk_gb.used / m.compute.disk_gb.total) * 100 : 0}
                      variant="warn"
                    />
                  </div>
                </section>
              )}

              {(m as { storage?: { disk_gb: { used: number; total: number }; node_count: number } }).storage && (() => {
                const st = (m as unknown as { storage: { disk_gb: { used: number; total: number }; node_count: number } }).storage;
                return (
                  <section className="gs-panel p-5">
                    <h2 className="gs-h2">{t('admin.dashboard.storageTitle')}</h2>
                    <p className="gs-sub mt-1">{t('admin.dashboard.storageSub', { count: st.node_count })}</p>
                    <div className="mt-1">
                      <CapacityRow
                        label={t('admin.dashboard.storageAllocated')}
                        reading={`${st.disk_gb.used} / ${st.disk_gb.total} GB`}
                        pct={st.disk_gb.total > 0 ? (st.disk_gb.used / st.disk_gb.total) * 100 : 0}
                        variant={st.disk_gb.used > st.disk_gb.total ? 'danger' : 'primary'}
                      />
                    </div>
                  </section>
                );
              })()}
          </div>

          {/* What just happened: the audit trail's newest entries, deep-linking to the full log. */}
          <section className="gs-panel p-5">
            <div className="flex items-center justify-between gap-3 mb-1">
              <h2 className="gs-h2">{t('admin.dashboard.recentActivity')}</h2>
              <Link to="/admin/audit" className="text-primary text-xs font-semibold hover:underline">
                {t('admin.dashboard.viewAudit')} →
              </Link>
            </div>
            {auditRows.length === 0 ? (
              <p className="text-muted text-sm mt-2">-</p>
            ) : (
              <ol>
                {auditRows.map((r) => {
                  // Who did what to what, with the outcome — the same reading the audit list gives
                  // before its detail unfolds, so the two screens never disagree.
                  const meta = resultMeta(r.result);
                  const changes = changesSummary(r.detail, auditNames);
                  return (
                    <li key={r.id} className="gs-hair flex items-center gap-x-3 gap-y-0.5 py-2 text-sm min-w-0 flex-wrap">
                      <span className="text-muted text-xs shrink-0 w-[7.5rem] truncate">{r.actor_name ?? '-'}</span>
                      <span className="font-semibold shrink-0">{actionLabel(r.action)}</span>
                      <span className="text-muted text-xs truncate max-w-[26rem]">{targetDisplay(r)}</span>
                      <span className={`gs-pill text-2xs shrink-0 ${meta.tone}`}>{meta.label}</span>
                      {changes && (
                        <span className="text-muted text-2xs basis-full sm:basis-auto truncate max-w-[34rem]" title={changes}>{changes}</span>
                      )}
                      <Timestamp value={r.at} className="gs-num text-xs text-muted ml-auto shrink-0" />
                    </li>
                  );
                })}
              </ol>
            )}
          </section>
        </>
      )}
    </div>
  );
}
