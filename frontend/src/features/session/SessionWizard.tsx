import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Trans, useTranslation } from 'react-i18next';
import { useCreateSession, usePreviewCost } from '@/api/hooks/useCreateSession';
import { useVolumes } from '@/api/hooks/useVolumes';
import { useOfferings, useGpuAvailability, useEffectivePolicy, usePresets } from '@/api/hooks/useResources';
import { useImages } from '@/api/hooks/useImages';
import { useWallet } from '@/api/hooks/useWallet';
import { useAuthStore } from '@/auth/authStore';
import { PageHeader, BackLink } from '@/components/PageHeader';
import { DisabledReason } from '@/components/Field';
import { useFormGuard } from '@/hooks/useUnsavedGuard';
import { useUiStore } from '@/store/uiStore';
import { humanizeError, asApiError } from '@/lib/errors';
import { formatCredit, scopeLabel } from '@/lib/format';
import { cudaCompatible } from '@/lib/cuda';
import {
  GPU_FALLBACK_MB,
  GPU_TIERS,
  tierVram,
  tierCores,
  tierOccupancy,
  gbLabel,
  isImbalanced,
  type GpuTier,
} from './tier';
import type { CreateSessionBody, ResourceClass, Volume } from '@/api/types';
import i18n from '@/i18n';

type VolumeMount = NonNullable<CreateSessionBody['volume_mounts']>[number];

// The wizard's form state: the SessionCreate fields plus UI-only intermediate state, which toBody()
// maps back onto the backend's fields.
type WizardForm = Partial<CreateSessionBody> & {
  sharing_mode?: 'fractional' | 'exclusive';
  vram_mb?: number;
  core_percent?: number;
  compute_preset_id?: string;     // the chosen compute preset (cpu, mem, disk)
};

// Whether the volume is configured read-only (ROX).
function volumeReadOnly(v: Volume): boolean {
  return /rox|read.?only/i.test(v.access_mode ?? '');
}
// Writable means an RWX volume the caller owns or holds rw on; anything else is pinned to ro.
function canWriteVolume(v: Volume): boolean {
  return !volumeReadOnly(v) && (v.role === 'owner' || v.role === 'rw');
}

// The session creation wizard: two steps (workload, then volumes and review) with an advanced
// toggle. GPU sessions branch into exclusive and shared.

type Step = 1 | 2;

// Tier and occupancy arithmetic lives in ./tier, where it is unit tested.

/** A tier's display name: built-in tiers translate their key, presets show the administrator's name. */
function tierName(tier: GpuTier): string {
  return tier.nameKey ? i18n.t(tier.nameKey) : (tier.name ?? tier.id);
}

/** A built-in tier's hint. Administrator-defined presets have none. */
function tierHint(tier: GpuTier): string {
  return tier.hintKey ? i18n.t(tier.hintKey) : '';
}

// Suggested name: <class>-MMDD-HHMM.
function suggestName(rc: string): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${rc}-${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
}

export function SessionWizard() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const pushToast = useUiStore((s) => s.pushToast);
  const createSession = useCreateSession();
  const previewCost = usePreviewCost();

  // Arriving from a quick-start card with ?class=cpu|gpu preselects that resource class.
  const [searchParams] = useSearchParams();
  const initialClass: ResourceClass = searchParams.get('class') === 'cpu' ? 'cpu' : 'gpu';

  const [step, setStep] = useState<Step>(1);
  const [custom, setCustom] = useState(false);   // whether the custom (advanced) panel is open
  const [tierId, setTierId] = useState('');       // selected GPU tier preset id; empty picks the default
  const [form, setForm] = useState<WizardForm>({
    name: suggestName(initialClass),    // suggested, and editable
    resource_class: initialClass,
    sharing_mode: 'fractional',
  });

  const isGpu = form.resource_class === 'gpu';

  // Catalogue lookups (offerings, images, cluster, wallet). Without the advanced panel these supply
  // the defaults.
  const offerings = useOfferings().data ?? [];
  // The wizard lists public images only; private ones belong to the admin catalogue.
  const imagesRes = useImages({ public: true }).data as { data?: { id: string; name: string; supported_gpus?: string[]; cuda_version?: string | null }[] } | undefined;
  const images = imagesRes?.data ?? [];
  const wallet = useWallet().data as { id?: string } | undefined;
  // Real GPU inventory, i.e. the models of ready devices. The model list is restricted to these.
  const availQuery = useGpuAvailability();
  const availModels = new Set((availQuery.data ?? []).map((a) => a.gpu_model));
  // GPU models are the intersection of the GPU offerings with real inventory, deduplicated by model
  // name (keeping the largest VRAM) and excluding inactive offerings. Before availability has loaded
  // the whole catalogue is shown; afterwards it narrows to models with real devices.
  const gpuModels = (() => {
    const m = new Map<string, (typeof offerings)[number]>();
    for (const o of offerings) {
      if (o.resource_class !== 'gpu' || (o as { status?: string }).status === 'inactive') continue;
      if (availQuery.isSuccess && !availModels.has(o.gpu_model || '')) continue;  // no real device for this model
      const key = o.gpu_model || o.name;
      const cur = m.get(key);
      if (!cur || (o.gpu_mem_mb ?? 0) > (cur.gpu_mem_mb ?? 0)) m.set(key, o);
    }
    return [...m.values()];
  })();
  const cpuOffering = offerings.find((o) => o.resource_class === 'cpu');
  const selectedOffering = isGpu
    ? gpuModels.find((o) => o.id === form.offering_id) ?? gpuModels[0]
    : cpuOffering;
  const modelMem = selectedOffering?.gpu_mem_mb || GPU_FALLBACK_MB;

  // GPU tiers come from the administrator's GPU presets (per-model fractions), falling back to the
  // built-in defaults when none are configured.
  const gpuPresetTiers: GpuTier[] = (usePresets('gpu').data ?? [])
    .filter((p) => p.gpu_frac != null)
    .map((p) => ({ id: p.id, name: p.name, frac: p.gpu_frac as number, mode: (p.mode === 'exclusive' ? 'exclusive' : 'fractional') as 'exclusive' | 'fractional' }));
  const gpuTiers: GpuTier[] = gpuPresetTiers.length ? gpuPresetTiers : GPU_TIERS;
  // Compute presets (cpu, mem, disk). Choosing one sets the session's resources.
  const computePresets = usePresets('compute').data ?? [];
  const compute = computePresets.find((c) => c.id === form.compute_preset_id);

  // Per-model availability. Tiers that cannot fit on a single card are disabled, because the
  // scheduler requires a fit on one device.
  const avail = (availQuery.data ?? []).find((a) => a.gpu_model === selectedOffering?.gpu_model);
  // Sessions are created under the active group, so policy sums are read per group, falling back to
  // the user and then the global policy.
  const activeProjectId = useAuthStore((s) => s.activeProjectId);
  const pol = useEffectivePolicy(activeProjectId).data;
  const concurrencyFull = !!pol?.has_policy && pol.max_concurrent != null && (pol.used?.active ?? 0) >= pol.max_concurrent;
  // A fractional tier whose derived VRAM (model capacity times the fraction) is under 1 GB is
  // disabled: it cannot even hold a CUDA context. Exclusive always takes the full card and is exempt.
  // The check uses the raw value, before the 512 MB snap.
  function tierTooSmall(t: GpuTier): boolean {
    return t.mode !== 'exclusive' && modelMem * t.frac < 1024;
  }
  function tierAvailable(t: GpuTier): boolean {
    if (tierTooSmall(t)) return false;              // derived VRAM under 1 GB; a static rule, not availability
    if (!availQuery.isSuccess) return true;        // do not block while availability is loading
    if (!avail) return false;                       // no ready device of this model
    const vram = t.mode === 'exclusive' ? modelMem : tierVram(t.frac, modelMem, 'fractional');
    const cores = t.mode === 'exclusive' ? 100 : tierCores(t.frac);
    // Policy headroom; a tier that exceeds it is disabled.
    if (pol?.has_policy) {
      if (pol.remaining?.gpu_mem_mb != null && vram > pol.remaining.gpu_mem_mb) return false;
      if (pol.remaining?.gpu_cores != null && cores > pol.remaining.gpu_cores) return false;
    }
    if (t.mode === 'exclusive') {
      return avail.devices.some(
        (d) => d.mode === 'exclusive' && d.free_mem_mb >= d.total_mem_mb && d.free_cores >= d.total_cores,
      );
    }
    return avail.devices.some((d) => d.mode === 'fractional' && d.free_mem_mb >= vram && d.free_cores >= cores);
  }
  const offeringId = selectedOffering?.id;
  // An image counts as GPU-only when it declares a CUDA version or a list of supported GPUs.
  const isGpuImage = (im: { cuda_version?: string | null; supported_gpus?: string[] }) =>
    !!im.cuda_version || !!im.supported_gpus?.length;
  // Image visibility: the CPU class lists CPU images only; the GPU class lists GPU images compatible
  // with the selected model and its CUDA minimum.
  const availableImages = images.filter((im) => {
    if (!isGpu) return !isGpuImage(im);
    if (!isGpuImage(im)) return false;
    const modelOk = !im.supported_gpus?.length || im.supported_gpus.includes(selectedOffering?.gpu_model ?? '');
    const cudaOk = cudaCompatible(im.cuda_version, selectedOffering?.min_cuda);
    return modelOk && cudaOk;
  });
  // If the current selection is no longer compatible — after changing model, say — fall back to the
  // first compatible image.
  const imageId =
    form.image_id && availableImages.some((i) => i.id === form.image_id)
      ? form.image_id
      : availableImages[0]?.id;
  // The backend picks an available cluster automatically; form.cluster_id is used only when the user
  // pinned one explicitly.
  const clusterId = form.cluster_id || undefined;
  const walletId = form.billing_wallet_id || wallet?.id;

  // The effective allocation: derived from the tier and model capacity, or taken straight from the
  // form in custom mode.
  const tier = gpuTiers.find((x) => x.id === tierId) ?? gpuTiers[Math.min(1, gpuTiers.length - 1)] ?? gpuTiers[0];
  const effMode = custom ? (form.sharing_mode ?? 'fractional') : tier.mode;
  const isExclusive = effMode === 'exclusive';
  const effVram = custom
    ? (form.vram_mb ?? tierVram(tier.frac, modelMem, effMode))
    : tierVram(tier.frac, modelMem, tier.mode);
  const effCores = custom
    ? (isExclusive ? 100 : form.core_percent ?? tierCores(tier.frac))
    : (isExclusive ? 100 : tierCores(tier.frac));

  function patch(p: Partial<WizardForm>) {
    setForm((f) => ({ ...f, ...p }));
  }

  // Map the wizard's form onto the backend's SessionCreate: align the field names (mode, gpu_mem_mb,
  // gpu_cores) and fill in the defaults.
  function toBody(): Record<string, unknown> {
    const b: Record<string, unknown> = {
      name: form.name || undefined,
      resource_class: form.resource_class,
      ...(clusterId ? { cluster_id: clusterId } : {}),   // omitted means the backend chooses
      ...(activeProjectId ? { group_id: activeProjectId } : {}),  // created under the active group, so its policy applies
      cluster_mode: 'single',
      offering_id: offeringId,
      image_id: imageId,
      billing_wallet_id: walletId,   // both GPU and CPU sessions are billed, so a wallet is required
      volume_mounts: form.volume_mounts ?? [],
    };
    if (form.resource_class === 'gpu') {
      b.mode = effMode;
      if (effMode === 'fractional') {
        b.gpu_mem_mb = effVram;
        b.gpu_cores = effCores;
      }
    }
    // With a compute preset chosen, send cpu, mem, and disk explicitly; otherwise the offering's
    // defaults apply.
    if (compute) {
      if (compute.cpu != null) b.cpu = compute.cpu;
      if (compute.mem_gb != null) b.mem_gb = compute.mem_gb;
      if (compute.disk_gb != null) b.disk_gb = compute.disk_gb;
    }
    return b;
  }

  // Re-estimate the cost whenever a step-1 choice changes (offering, tier, compute preset, image).
  // The result is shown under the image field.
  useEffect(() => {
    if (step !== 1 || !offeringId || !imageId) return;
    const t = setTimeout(() => { void previewCost.mutateAsync(toBody() as never).catch(() => undefined); }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, offeringId, imageId, form.resource_class, effMode, effVram, effCores, form.compute_preset_id, activeProjectId]);

  const guard = useFormGuard(createSession.isPending);

  async function handleNext() {
    if (step === 1) {
      setStep(2);
      void previewCost.mutateAsync(toBody() as never).catch(() => undefined);
    }
  }

  async function handleSubmit() {
    try {
      const session = (await createSession.mutateAsync(toBody() as never)) as
        | { id?: string; status?: string; queue?: boolean }
        | undefined;
      // Queued for lack of capacity goes to the queue; a session id goes to its detail page;
      // anything else falls back to the list.
      if (session?.queue) {
        navigate('/queue'); // 409 no_capacity
      } else if (session?.id) {
        navigate(`/sessions/${session.id}`);
      } else {
        navigate('/sessions'); // an unparseable response still must not trap the user in the wizard
      }
    } catch (e) {
      const err = asApiError(e);
      // Route the two admission gates to where the user can act on them.
      if (err.code === 'insufficient_credit') {
        navigate('/wallet/request'); // out of credits: deep-link to the request form
        return;
      }
      if (err.code === 'no_capacity') {
        navigate('/queue');
        return;
      }
      pushToast('error', humanizeError(err));
    }
  }

  return (
    <div className="w-full" {...guard.props}>
      <PageHeader
        title={t('wizard.title')}
        crumbs={[{ label: t('session.title'), to: '/sessions' }, { label: t('wizard.title') }]}
        actions={<BackLink to="/sessions" label={t('session.backToList')} />}
      />

      {/* Resource policy limits: concurrency and remaining headroom. */}
      {pol?.has_policy && (
        <div className={`gs-card mb-4 text-[12.5px] ${concurrencyFull ? 'bg-danger-soft text-danger' : 'bg-surface-2 text-muted'}`}>
          <b>{t('wizard.policyLimits')}</b>{pol.scope ? ` (${scopeLabel(pol.scope)})` : ''} —{' '}
          {pol.max_concurrent != null && <>{t('wizard.concurrent', { used: pol.used?.active ?? 0, limit: pol.max_concurrent })}, </>}
          {pol.remaining?.gpu_mem_mb != null && <>{t('wizard.remainingVram', { value: pol.remaining.gpu_mem_mb })}, </>}
          {pol.remaining?.cpu != null && <>{t('wizard.remainingCpu', { value: pol.remaining.cpu })}, </>}
          {pol.remaining?.mem_gb != null && <>{t('wizard.remainingMem', { value: pol.remaining.mem_gb })}</>}
          {concurrencyFull && <div className="font-bold mt-1">{t('wizard.concurrencyFull')}</div>}
        </div>
      )}

      {/* Step indicator */}
      <div className="flex gap-2 mb-5">
        {(['wizard.stepWorkload', 'wizard.stepReview'] as const).map((key, i) => (
          <div key={key} className={`flex-1 text-center text-[12px] font-bold py-2 rounded-lg ${step === i + 1 ? 'bg-primary-soft text-primary' : 'bg-surface-2 text-muted'}`}>
            {i + 1}. {t(key)}
          </div>
        ))}
      </div>

      {step === 1 && (
        <div className="gs-card space-y-4">
          <label className="block">
            <span className="text-[12px] font-semibold text-muted">{t('wizard.sessionName')}</span>
            <input className="w-full mt-1 px-3 py-2 border border-border rounded-lg bg-surface-2" value={form.name ?? ''} onChange={(e) => patch({ name: e.target.value })} placeholder="my-training" autoComplete="off" />
          </label>

          {/* Resource class */}
          <div>
            <span className="text-[12px] font-semibold text-muted">{t('wizard.resourceClass')}</span>
            <div className="grid grid-cols-2 gap-3 mt-1">
              {(['gpu', 'cpu'] as ResourceClass[]).map((rc) => (
                <button
                  key={rc}
                  type="button"
                  className={`border-2 rounded-xl p-4 text-left ${form.resource_class === rc ? 'border-primary' : 'border-border'}`}
                  onClick={() => patch({ resource_class: rc })}
                >
                  <div className="font-bold">{rc === 'gpu' ? t('wizard.gpuTitle') : t('wizard.cpuTitle')}</div>
                  <div className="text-muted text-[12px]">{rc === 'gpu' ? t('wizard.gpuDesc') : t('wizard.cpuDesc')}</div>
                </button>
              ))}
            </div>
          </div>

          {/* Compute preset (cpu, mem, disk): shared by GPU and CPU, and chosen first. */}
          {computePresets.length > 0 && (
            <label className="block">
              <span className="text-[12px] font-semibold text-muted">{t('wizard.computePreset')}</span>
              <select
                className="w-full mt-1 px-3 py-2 border border-border rounded-lg bg-surface-2"
                value={form.compute_preset_id ?? ''}
                onChange={(e) => patch({ compute_preset_id: e.target.value || undefined })}
              >
                <option value="">{t('wizard.computeDefault')}</option>
                {computePresets.map((c) => (
                  <option key={c.id} value={c.id}>{c.name} — CPU {c.cpu} · {c.mem_gb}GiB · {c.disk_gb ?? 0}GiB</option>
                ))}
              </select>
            </label>
          )}

          {/* GPU model, after the compute preset. Picks the card in a mixed fleet. */}
          {isGpu && (
            <label className="block">
              <span className="text-[12px] font-semibold text-muted">{t('wizard.gpuModel')}</span>
              <select
                className="w-full mt-1 px-3 py-2 border border-border rounded-lg bg-surface-2"
                value={offeringId ?? ''}
                onChange={(e) => patch({ offering_id: e.target.value })}
              >
                {gpuModels.length === 0 && <option value="">{t('wizard.noGpu')}</option>}
                {gpuModels.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.gpu_model || o.name} · {gbLabel(o.gpu_mem_mb)}
                  </option>
                ))}
              </select>
              {selectedOffering && (
                <span className="text-muted text-[11px] mt-1 block">
                  {t('wizard.tierDerivedFrom', { capacity: gbLabel(modelMem) })}
                </span>
              )}
            </label>
          )}

          {/* GPU preset tiers, as fractions of the selected model's capacity. */}
          {isGpu && (
            <div>
              <span className="text-[12px] font-semibold text-muted">{t('wizard.gpuTier')}</span>
              <div className="grid grid-cols-2 gap-3 mt-1">
                {gpuTiers.map((tier) => {
                  const ok = tierAvailable(tier);
                  const active = ok && !custom && tierId === tier.id;
                  const vram = tierVram(tier.frac, modelMem, tier.mode);
                  const cores = tier.mode === 'exclusive' ? 100 : tierCores(tier.frac);
                  return (
                    <button
                      key={tier.id}
                      type="button"
                      disabled={!ok}
                      className={`border-2 rounded-xl p-3 text-left transition ${
                        active ? 'border-primary' : 'border-border'
                      } ${!ok ? 'opacity-45 cursor-not-allowed' : ''}`}
                      onClick={() => {
                        if (!ok) return;
                        setTierId(tier.id);
                        setCustom(false);
                      }}
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-bold text-[13px]">{tierName(tier)}</span>
                        {ok ? (
                          <span className="gs-pill bg-surface-2 text-muted text-[11px]">{t('wizard.occupancyBilled', { percent: tierOccupancy(vram, cores, modelMem) })}</span>
                        ) : (
                          <span className="gs-pill bg-danger-soft text-danger text-[11px]">{tierTooSmall(tier) ? t('wizard.vramTooSmall') : t('wizard.capacityShort')}</span>
                        )}
                      </div>
                      <div className="text-[12px] mt-1">{gbLabel(vram)} VRAM · {t('wizard.coresLabel', { percent: cores })}{tier.mode === 'exclusive' ? t('wizard.exclusiveSuffix') : ''}</div>
                      <div className="text-muted text-[11px] mt-0.5">{tierHint(tier)}</div>
                    </button>
                  );
                })}
              </div>
              {availQuery.isSuccess && !gpuTiers.some((x) => tierAvailable(x)) && (
                <p className="text-warn text-[12px] mt-2">{t('wizard.noCapacityWarn')}</p>
              )}

              <button
                type="button"
                className="text-primary text-[12px] font-semibold mt-2"
                onClick={() =>
                  setCustom((c) => {
                    const next = !c;
                    if (next) patch({ sharing_mode: effMode, vram_mb: effVram, core_percent: effCores });
                    return next;
                  })
                }
              >
                {custom ? '▼' : '▶'} {t('wizard.customToggle')}
              </button>
              {custom && (
                <div className="mt-2 space-y-3 border border-border rounded-xl p-3">
                  <div className="grid grid-cols-2 gap-2">
                    <ModeOpt active={!isExclusive} title={t('wizard.modeFractionalTitle')} desc={t('wizard.modeFractionalDesc')} onClick={() => patch({ sharing_mode: 'fractional' })} />
                    <ModeOpt active={isExclusive} title={t('wizard.modeExclusiveTitle')} desc={t('wizard.modeExclusiveDesc')} onClick={() => patch({ sharing_mode: 'exclusive' })} />
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <Slider label={t('wizard.vramMb')} min={512} max={modelMem} step={512} value={isExclusive ? modelMem : (form.vram_mb ?? effVram)} disabled={isExclusive} onChange={(v) => patch({ vram_mb: v })} />
                    <Slider label={t('wizard.corePercent')} min={5} max={100} step={5} value={isExclusive ? 100 : (form.core_percent ?? effCores)} disabled={isExclusive} onChange={(v) => patch({ core_percent: v })} />
                  </div>
                  {!isExclusive && isImbalanced(form.vram_mb ?? 0, form.core_percent ?? 0, modelMem) && (
                    <p className="text-warn text-[12px]">
                      <Trans i18nKey="wizard.imbalancedWarn" values={{ capacity: gbLabel(modelMem) }} components={{ 1: <b /> }} />
                    </p>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Image, required. Only images compatible with the selected GPU model are listed. */}
          <label className="block">
            <span className="text-[12px] font-semibold text-muted">{t('wizard.image')} <span className="text-danger">*</span></span>
            <select className="w-full mt-1 px-3 py-2 border border-border rounded-lg bg-surface-2" value={imageId ?? ''} onChange={(e) => patch({ image_id: e.target.value })}>
              {availableImages.length === 0 && <option value="">{isGpu ? t('wizard.noCompatibleImage') : t('wizard.noImage')}</option>}
              {availableImages.map((im) => (<option key={im.id} value={im.id}>{im.name}</option>))}
            </select>
            {isGpu && selectedOffering?.gpu_model && (
              <span className="text-muted text-[11px] mt-1 block">
                {t('wizard.compatibleOnly', { model: selectedOffering.gpu_model })}
                {selectedOffering.min_cuda ? t('wizard.cudaRequired', { version: selectedOffering.min_cuda }) : ''}.
              </span>
            )}
          </label>

          {/* Under the image: how billing works, plus the estimated cost. */}
          {offeringId && imageId && (
            <div className="space-y-2">
              <div className="rounded-lg border border-border bg-surface-2 p-3 text-[12px] text-muted">
                <b className="text-fg">{t('wizard.billingNote')}</b> —{' '}
                {isGpu ? t('wizard.billingGpu') : t('wizard.billingCpu')}
              </div>
              <div className="flex items-center justify-between rounded-lg border border-border p-3">
                <span className="text-[12px] font-semibold text-muted">{t('wizard.estimatedCost')}</span>
                <span className="text-[15px] font-extrabold" aria-live="polite">
                  {previewCost.isPending
                    ? <span className="text-muted text-[13px] font-normal">{t('wizard.calculating')}</span>
                    : previewCost.isError
                      ? <span className="text-danger text-[13px] font-normal">{t('wizard.estimateUnavailable')}</span>
                      : previewCost.data
                        ? <>{t('wizard.perHour', { amount: formatCredit(previewCost.data.estimated_credit_per_hour) })}</>
                        : <span className="text-muted text-[13px] font-normal">—</span>}
                </span>
              </div>
              {previewCost.data != null && !previewCost.isError && (
                <div className="text-muted text-[11px] text-right">{t('wizard.holdOnStart', { amount: formatCredit(previewCost.data.hold_amount) })}</div>
              )}
              {previewCost.isError && (
                <p role="alert" className="text-danger text-[11.5px] text-right">{t('wizard.estimateUnavailableHint')}</p>
              )}
            </div>
          )}

          <div className="flex justify-end items-center gap-3 flex-wrap">
            <DisabledReason reasons={[
              !form.name && t('wizard.nameLabel'),
              !offeringId && t('wizard.offeringLabel'),
              !imageId && t('wizard.imageLabel'),
            ].filter(Boolean) as string[]} />
            <button
              type="button"
              className="gs-btn gs-btn-primary disabled:opacity-50"
              disabled={!form.name || !imageId || !offeringId}
              onClick={handleNext}
            >
              {t('wizard.next')}
            </button>
          </div>
        </div>
      )}

      {step === 2 && (
        <div className="gs-card space-y-4">
          <h2 className="font-bold">{t('wizard.volumeMounts')}</h2>
          <VolumePicker mounts={form.volume_mounts ?? []} onChange={(m) => patch({ volume_mounts: m })} />

          <h2 className="font-bold mt-4">{t('wizard.review')}</h2>
          {previewCost.isPending ? (
            <p className="text-muted">{t('wizard.estimating')}</p>
          ) : previewCost.data ? (
            <dl className="text-[13px] space-y-1.5">
              <div className="flex justify-between"><dt className="text-muted">{t('wizard.estimatedCost')}</dt><dd>{t('wizard.perHourShort', { amount: formatCredit(previewCost.data.estimated_credit_per_hour) })}</dd></div>
              <div className="flex justify-between"><dt className="text-muted">{t('wizard.hold')}</dt><dd>{t('wizard.perHour', { amount: formatCredit(previewCost.data.hold_amount) }).replace(/\/.*$/, '')}</dd></div>
              {form.resource_class === 'cpu' && <div className="text-muted text-[11.5px]">{t('wizard.cpuBillingNote')}</div>}
            </dl>
          ) : (
            <p className="text-muted">{t('wizard.estimateFailed')}</p>
          )}

          <div className="flex justify-between">
            <button type="button" className="gs-btn" onClick={() => setStep(1)}>{t('wizard.back')}</button>
            <button type="button" className="gs-btn gs-btn-primary" disabled={createSession.isPending || concurrencyFull} onClick={handleSubmit} title={concurrencyFull ? t('wizard.concurrencyFullShort') : undefined}>
              {createSession.isPending ? t('wizard.starting') : t('wizard.start')}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function VolumePicker({ mounts, onChange }: { mounts: VolumeMount[]; onChange: (m: VolumeMount[]) => void }) {
  const { t } = useTranslation();
  // useVolumes returns a loose Record<string, unknown>[], but the payload really is VolumeRead[].
  const { data: rawVolumes = [], isLoading } = useVolumes();
  const volumes = rawVolumes as unknown as Volume[];
  const byId = (id: string) => mounts.find((m) => m.volume_id === id);

  function toggle(v: Volume) {
    if (byId(v.id)) {
      onChange(mounts.filter((m) => m.volume_id !== v.id));
    } else {
      // Default to rw when writable (the user can switch to ro); otherwise pin to ro.
      onChange([
        ...mounts,
        { volume_id: v.id, mount_path: `/data/${v.type || 'vol'}`, mode: canWriteVolume(v) ? 'rw' : 'ro' },
      ]);
    }
  }

  function update(id: string, patch: Partial<VolumeMount>) {
    onChange(mounts.map((m) => (m.volume_id === id ? { ...m, ...patch } : m)));
  }

  if (isLoading) return <p className="text-muted text-[12px]">{t('wizard.loadingVolumes')}</p>;
  if (volumes.length === 0)
    return <p className="text-muted text-[12px]">{t('wizard.noVolumes')}</p>;

  return (
    <div className="space-y-2">
      {volumes.map((v) => {
        const mount = byId(v.id);
        const locked = !canWriteVolume(v);          // not writable, so the mount is pinned to ro
        return (
          <div key={v.id} className="border border-border rounded-lg p-3">
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={!!mount} onChange={() => toggle(v)} />
              <span className="font-semibold text-[13px]">{v.type || v.id}</span>
              <span className="text-muted text-[11px]">
                {v.type} · {v.access_mode} · {v.used_gb}/{v.quota_gb} GiB
                {locked && <span className="ml-1 text-warn">{t('wizard.readOnlyLocked')}</span>}
              </span>
            </label>
            {mount && (
              <div className="mt-2 grid grid-cols-[1fr_auto] gap-2 items-center">
                <input
                  className="px-2 py-1.5 border border-border rounded-lg bg-surface-2 text-[12px] font-mono"
                  value={mount.mount_path}
                  onChange={(e) => update(v.id, { mount_path: e.target.value })}
                  placeholder="/data/…" autoComplete="off" />
                <select
                  className="px-2 py-1.5 border border-border rounded-lg bg-surface-2 text-[12px] disabled:opacity-60"
                  value={mount.mode}
                  disabled={locked}
                  onChange={(e) => update(v.id, { mode: e.target.value as VolumeMount['mode'] })}
                >
                  <option value="rw">{t('wizard.mountRw')}</option>
                  <option value="ro">{t('wizard.mountRo')}</option>
                </select>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ModeOpt({ active, title, desc, onClick }: { active: boolean; title: string; desc: string; onClick: () => void }) {
  return (
    <button type="button" className={`border-2 rounded-xl p-3 text-left ${active ? 'border-primary' : 'border-border'}`} onClick={onClick}>
      <div className="font-bold text-[13px]">{title}</div>
      <div className="text-muted text-[11px] mt-1">{desc}</div>
    </button>
  );
}

function Slider({ label, min, max, step, value, disabled, onChange }: { label: string; min: number; max: number; step: number; value: number; disabled?: boolean; onChange: (v: number) => void }) {
  const { t } = useTranslation();
  return (
    <label className="block">
      <span className="text-[12px] font-semibold text-muted flex justify-between">
        <span>{label}</span>
        <span>{value}{disabled ? t('wizard.fixed') : ''}</span>
      </span>
      <input type="range" className="w-full mt-1" min={min} max={max} step={step} value={value} disabled={disabled} onChange={(e) => onChange(Number(e.target.value))} autoComplete="off" />
    </label>
  );
}
