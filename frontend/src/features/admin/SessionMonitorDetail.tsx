import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { useAllSessions } from '@/api/hooks/useMonitor';
import { useSession, useStopSession, useStartSession } from '@/api/hooks/useSessions';
import { useSessionUsage, type SessionUsage } from '@/api/hooks/useMonitor';
import { useSessionUsageSeries } from '@/api/hooks/useMonitoring';
import { StatusPill } from '@/components/StatusPill';
import { SessionUsagePanel, type UsageRange } from '@/components/SessionUsagePanel';
import { Timestamp } from '@/components/Timestamp';
import { CopyableId } from '@/components/CopyButton';
import { X } from '@/components/icons';
import { formatVram, sessionStatusLabel } from '@/lib/format';
import { useUiStore } from '@/store/uiStore';
import { humanizeError, asApiError } from '@/lib/errors';
import { DrawerTimeline, type SessionRow } from '@/features/admin/Monitor';

/** The monitor's session detail, as an overlay above the list: live measured usage front and
 *  center (cadvisor CPU/MEM, HAMi VRAM/GPU-core), then the session's facts and lifecycle.
 *  A modal instead of a page — the list keeps its scroll and filters underneath. */
export function SessionMonitorOverlay({ sessionId, onClose }: {
  sessionId: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const panelRef = useRef<HTMLDivElement>(null);
  const [range, setRange] = useState<UsageRange>('15m');
  const pushToast = useUiStore((st) => st.pushToast);
  const stopSession = useStopSession();
  const startSession = useStartSession();

  // Read the row from the already-live list query, so status changes flow in over SSE.
  const { data: sessions } = useAllSessions({});
  const s = (sessions as SessionRow[] | undefined)?.find((x) => x.id === sessionId);
  const running = s?.status === 'running';
  const { data: u } = useSessionUsage(running ? sessionId : undefined) as { data?: SessionUsage };
  const { data: hist } = useSessionUsageSeries(sessionId, range);
  // Mounted volumes ride on the single-session read; the list rows do not carry them.
  const mounts = ((useSession(sessionId).data as { mounts?: { volume_id: string; name?: string | null; quota_gb?: number | null; mount_path: string; mode: string }[] } | undefined)?.mounts) ?? [];

  useEffect(() => {
    panelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  if (!s) return null;
  const isGpu = s.resource_class === 'gpu';
  const dash = '-';

  return (
    <div className="fixed inset-0 z-50 grid place-items-center p-4" role="presentation">
      <div className="absolute inset-0 bg-black/45" onClick={onClose} aria-hidden="true" />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
        className="relative w-full max-w-6xl max-h-[88vh] flex flex-col
                   bg-surface border border-border rounded-card shadow-raised outline-none"
      >
        <div className="flex items-center gap-2.5 px-5 py-3.5 border-b border-border">
          <h2 className="font-bold text-md min-w-0 truncate">{s.name || s.id}</h2>
          <StatusPill kind={s.status} label={sessionStatusLabel(s.status)} />
          {s.status_reason && s.status !== 'running' && (
            <span className={`text-xs truncate ${s.status === 'error' ? 'text-danger' : 'text-muted'}`}>
              {t(`enum.statusReason.${s.status_reason}`, { defaultValue: s.status_reason })}
            </span>
          )}
          <span className="text-muted text-xs truncate hidden sm:inline">{s.owner_name ?? s.owner_user_id ?? dash}</span>
          <div className="ml-auto flex items-center gap-2 shrink-0">
            <button
              type="button"
              className="w-8 h-8 grid place-items-center rounded-ctl text-muted hover:text-text hover:bg-surface-2"
              onClick={onClose}
              aria-label={t('common.close')}
            >
              <X size={16} aria-hidden="true" />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          <div>
            <h3 className="text-xs font-semibold text-muted mb-1">{t('admin.monitor.detailInfo')}</h3>
            <dl className="grid sm:grid-cols-2 gap-x-10">
              <Row label={t('admin.monitor.colOwner')}>{s.owner_name ?? s.owner_user_id ?? dash}</Row>
              <Row label={t('common.organization')}>{s.org_name ?? dash}</Row>
              <Row label={t('common.group')}>{s.group_name ?? dash}</Row>
              <Row label={t('session.nodeLabel')}>
                {(() => {
                  const n = s as unknown as { node_hostname?: string | null; node_id?: string | null };
                  if (!n.node_hostname) return dash;
                  return n.node_id
                    ? <Link to={`/admin/nodes/${n.node_id}/devices`} className="text-primary hover:underline" onClick={onClose}>{n.node_hostname}</Link>
                    : <span>{n.node_hostname}</span>;
                })()}
              </Row>
              <Row label={t('session.compute')} mono>
                {[s.cpu != null ? `${s.cpu}c` : null, s.mem_gb != null ? `${s.mem_gb}GiB` : null,
                  s.disk_gb != null ? `${s.disk_gb}GB` : null].filter(Boolean).join(' · ') || dash}
              </Row>
              {s.gpu_model && <Row label="GPU">{s.gpu_model}</Row>}
              {isGpu && (
                <Row label={t('admin.monitor.colResource')} mono>
                  {[s.mode, s.gpu_mem_mb ? formatVram(s.gpu_mem_mb) : null,
                    s.gpu_cores != null ? `${s.gpu_cores}%` : null].filter(Boolean).join(' · ') || dash}
                </Row>
              )}
              <Row label={t('common.created')}><Timestamp value={s.created_at} /></Row>
              {s.started_at && <Row label={t('session.started')}><Timestamp value={s.started_at} /></Row>}
              {s.status === 'terminated' && s.terminated_at && <Row label={t('session.terminatedAt')}><Timestamp value={s.terminated_at} /></Row>}
              {mounts.map((mnt) => (
                <Row key={mnt.volume_id} label={t('session.volumesLabel')}>
                  <span>{mnt.name || mnt.volume_id}</span>
                  <span className="text-muted text-xs ml-1.5 gs-num">
                    {mnt.quota_gb != null ? `${mnt.quota_gb} GiB · ` : ''}{mnt.mode === 'ro' ? 'RO' : 'RW'} · {mnt.mount_path}
                  </span>
                </Row>
              ))}
              <Row label="ID" mono><CopyableId value={s.id} /></Row>
            </dl>
          </div>

          {s.status !== 'terminated' && (
          <div className="mt-5 border-t border-border pt-4">
            <SessionUsagePanel
              limits={{ isGpu, cpu: s.cpu, mem_gb: s.mem_gb, gpu_mem_mb: s.gpu_mem_mb, gpu_cores: s.gpu_cores }}
              usage={u}
              series={hist}
              range={range}
              onRange={setRange}
            />
          </div>
          )}

          <DrawerTimeline sessionId={s.id} />
        </div>

        {s.status !== 'terminated' && (
          <div className="px-5 py-3.5 border-t border-border flex justify-end gap-2">
            {/* The same stop/start the owner has — the backend already admits administrators
                (_require_access), records the admin_stopped reason, and audits the action. */}
            {s.status === 'running' && (
              <button
                type="button"
                className="gs-btn gs-btn-sm"
                disabled={stopSession.isPending}
                onClick={() => stopSession.mutate(s.id, {
                  onSuccess: () => pushToast('success', t('admin.monitor.paused')),
                  onError: (e) => pushToast('error', humanizeError(asApiError(e))),
                })}
              >
                {t('admin.monitor.pauseSession')}
              </button>
            )}
            {s.status === 'paused' && (
              <button
                type="button"
                className="gs-btn gs-btn-sm gs-btn-primary"
                disabled={startSession.isPending}
                onClick={() => startSession.mutate(s.id, {
                  onSuccess: () => pushToast('success', t('admin.monitor.resumed')),
                  onError: (e) => pushToast('error', humanizeError(asApiError(e))),
                })}
              >
                {t('admin.monitor.resumeSession')}
              </button>
            )}
            <Link
              to={`/admin/monitor/sessions/${s.id}/terminate`}
              className={`gs-btn gs-btn-sm ${s.status === 'error' ? '' : 'gs-btn-danger'}`}
              title={s.status === 'error' ? t('admin.monitor.cleanupHint') : undefined}
              onClick={onClose}
            >
              {s.status === 'error' ? t('admin.monitor.cleanup') : t('admin.monitor.forceTerminate')}
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}

function Row({ label, mono, children }: { label: string; mono?: boolean; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5 border-b border-border/50 text-sm">
      <dt className="text-muted shrink-0">{label}</dt>
      <dd className={`text-right min-w-0 ${mono ? 'gs-num' : ''}`}>{children}</dd>
    </div>
  );
}
