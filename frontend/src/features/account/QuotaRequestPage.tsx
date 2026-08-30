import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/auth/authStore';
import { useEffectivePolicy } from '@/api/hooks/useResources';
import { useCreateResourceRequest } from '@/api/hooks/useResourceRequests';
import { Field, DisabledReason } from '@/components/Field';
import { useUiStore } from '@/store/uiStore';
import { humanizeError, asApiError } from '@/lib/errors';
import { useFormGuard } from '@/hooks/useUnsavedGuard';

// Ask for a bigger compute quota (CPU / memory / disk). The resource policy stays the DEFAULT;
// an approval upserts a user-scope policy with only the granted keys.
export function QuotaRequestForm({ onDone }: { onDone?: () => void }) {
  const { t } = useTranslation();
  const pushToast = useUiStore((s) => s.pushToast);
  const activeProjectId = useAuthStore((s) => s.activeProjectId);
  const eff = useEffectivePolicy(activeProjectId ?? undefined).data as
    | { has_policy?: boolean; limits?: Record<string, number> } | undefined;
  const cur = (k: string): number | null => {
    const v = eff?.limits?.[k];
    return v && v > 0 ? v : null;
  };
  const create = useCreateResourceRequest();
  const [cpu, setCpu] = useState('');
  const [memGb, setMemGb] = useState('');
  const [storageGb, setStorageGb] = useState('');
  const [gpuMemGb, setGpuMemGb] = useState('');   // entered in GB; sent as MB
  const [gpuCores, setGpuCores] = useState('');
  const [note, setNote] = useState('');
  const guard = useFormGuard(create.isPending);

  const n = (s: string) => (s.trim() ? Number(s) : undefined);
  const anyTarget = [cpu, memGb, storageGb, gpuMemGb, gpuCores].some((s) => s.trim());
  const allValid = [cpu, memGb, storageGb, gpuMemGb, gpuCores].every((s) => !s.trim() || (Number.isFinite(Number(s)) && Number(s) > 0));
  const valid = anyTarget && allValid && !!note.trim();
  const blockers = [
    !anyTarget && t('quota.anyTargetBlocker'),
    !note.trim() && t('common.reason'),
  ].filter(Boolean) as string[];

  const submit = () => {
    if (!valid) return;
    create.mutate(
      { group_id: activeProjectId ?? null, cpu: n(cpu), mem_gb: n(memGb), storage_gb: n(storageGb),
        gpu_mem_mb: gpuMemGb.trim() ? Number(gpuMemGb) * 1024 : undefined,
        gpu_cores: n(gpuCores), note: note.trim() },
      {
        onSuccess: () => { guard.clear(); pushToast('success', t('quota.sent')); setCpu(''); setMemGb(''); setStorageGb(''); setGpuMemGb(''); setGpuCores(''); setNote(''); onDone?.(); },
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      },
    );
  };

  // `scale` renders a limit stored in a finer unit (gpu_mem_mb) in the unit the field uses (GB).
  const numField = (label: string, curKey: string, val: string, set: (v: string) => void, scale = 1) => {
    const c = cur(curKey);
    const disp = c != null ? Math.round(c / scale) : null;
    return (
      <Field label={label} hint={disp != null ? t('quota.currentLimit', { value: disp }) : t('quota.noLimit')}>
        {(ids) => (
          <input {...ids} type="number" inputMode="numeric" min={1} className="gs-input w-full"
            value={val} onChange={(e) => set(e.target.value)} placeholder={disp != null ? String(disp) : '-'} autoComplete="off" />
        )}
      </Field>
    );
  };

  return (
      <form className="gs-card space-y-4" {...guard.props} onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <p className="text-muted text-xs">{t('quota.note')}</p>
        <div className="grid sm:grid-cols-3 gap-3">
          {numField(t('quota.cpuLabel'), 'cpu', cpu, setCpu)}
          {numField(t('quota.memLabel'), 'mem_gb', memGb, setMemGb)}
          {numField(t('quota.diskLabel'), 'storage_gb', storageGb, setStorageGb)}
          {numField(t('quota.gpuMemLabel'), 'gpu_mem_mb', gpuMemGb, setGpuMemGb, 1024)}
          {numField(t('quota.gpuCoresLabel'), 'gpu_cores', gpuCores, setGpuCores)}
        </div>
        <Field label={t('common.reason')} required>
          {(ids) => (
            <input {...ids} className="gs-input w-full" value={note} maxLength={280}
              onChange={(e) => setNote(e.target.value)} placeholder={t('quota.reasonPlaceholder')} autoComplete="off" />
          )}
        </Field>
        <div className="flex items-center justify-end gap-3 flex-wrap">
          <DisabledReason reasons={valid ? [] : blockers} />
          <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={create.isPending || !valid}>
            {create.isPending ? t('wallet.sending') : t('quota.send')}
          </button>
        </div>
      </form>
  );
}
