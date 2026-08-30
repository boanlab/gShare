import { useEffect, useState, type ReactNode } from 'react';
import { Select } from '@/components/Select';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Trans, useTranslation } from 'react-i18next';
import { useCreateSession, usePreviewCost } from '@/api/hooks/useCreateSession';
import { useVolumes } from '@/api/hooks/useVolumes';
import { useOfferings, useGpuAvailability, useEffectivePolicy, usePresets } from '@/api/hooks/useResources';
import { useImages } from '@/api/hooks/useImages';
import { useWallet } from '@/api/hooks/useWallet';
import { useAuthStore } from '@/auth/authStore';
import { StatusPill } from '@/components/StatusPill';
import { PageHeader } from '@/components/PageHeader';
import { DisabledReason } from '@/components/Field';
import { useFormGuard } from '@/hooks/useUnsavedGuard';
import { useUiStore } from '@/store/uiStore';
import { humanizeError, asApiError } from '@/lib/errors';
import { formatCredit, formatVram, scopeLabel } from '@/lib/format';
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
import { CaretDown, CaretRight, Check, Cube } from '@/components/icons';

type VolumeMount = NonNullable<CreateSessionBody['volume_mounts']>[number];

// The wizard's form state: the SessionCreate fields plus UI-only intermediate state, which toBody()
// maps back onto the backend's fields.
type WizardForm = Partial<CreateSessionBody> & {
  sharing_mode?: 'fractional' | 'exclusive';
  vram_mb?: number;
  core_percent?: number;
  compute_preset_id?: string;     // the chosen compute preset (cpu, mem, disk), or CUSTOM_COMPUTE
};

// Sentinel select value for the custom compute panel (direct cpu/mem/disk inputs).
const CUSTOM_COMPUTE = '__custom__';
// Bounds for the custom compute sliders. Oversized requests would only queue (the scheduler
// admits against real node headroom), so the UI keeps the range within a realistic single node.
const CUSTOM_LIMITS = { cpu: 32, mem_gb: 32, disk_gb: 500 };

// Whether the volume is configured read-only (ROX).
function volumeReadOnly(v: Volume): boolean {
  return /rox|read.?only/i.test(v.access_mode ?? '');
}
// Writable means an RWX volume the caller owns or holds rw on; anything else is pinned to ro.
function canWriteVolume(v: Volume): boolean {
  return !volumeReadOnly(v) && (v.role === 'owner' || v.role === 'rw');
}

// Mirror of the backend's mount-path rule (VolumeMountSpec): absolute, ASCII segments of
// [A-Za-z0-9._-], no '.'/'..', not over a system path. Validated inline so the 422 never fires.
const MOUNT_PATH_RE = /^(\/[A-Za-z0-9._-]{1,64})+$/;
const MOUNT_RESERVED = ['/proc', '/sys', '/dev', '/etc', '/bin', '/sbin', '/usr', '/lib', '/lib64'];
export function mountPathInvalid(path: string): boolean {
  if (!path || path.length > 255 || !MOUNT_PATH_RE.test(path)) return true;
  if (path.split('/').some((seg) => seg === '.' || seg === '..')) return true;
  const norm = path.replace(/\/+$/, '') || '/';
  return MOUNT_RESERVED.some((r) => norm === r || norm.startsWith(r + '/'));
}

// The session creation wizard: two steps (workload, then volumes and review) with an advanced
// toggle. GPU sessions branch into exclusive and shared.

type Step = 1 | 2 | 3 | 4;
type StepKey = 'compute' | 'gpu' | 'image' | 'review';

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

/**
 * The wizard's one selection tile: 1px border, selected = accent border + soft background + check
 * top-right, disabled = dimmed but visible. Padding comes from the caller so dense and roomy
 * tiles share the skin.
 */
function SelTile({ selected, disabled, onClick, className = '', children }: {
  selected: boolean;
  disabled?: boolean;
  onClick: () => void;
  className?: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      aria-pressed={selected}
      className={`relative border rounded-card text-left transition-colors duration-150 ${
        selected ? 'border-primary bg-primary-soft' : 'border-border'
      } ${disabled ? 'opacity-45 cursor-not-allowed' : selected ? '' : 'hover:border-border-strong'} ${className}`}
      onClick={onClick}
    >
      {selected && <Check size={14} weight="bold" aria-hidden="true" className="absolute top-3 right-3 text-primary" />}
      {children}
    </button>
  );
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
  // One decision per screen: compute → GPU → image → review (CPU sessions skip the GPU step).
  const wizardSteps: StepKey[] = isGpu ? ['compute', 'gpu', 'image', 'review'] : ['compute', 'image', 'review'];
  const stepKey: StepKey = wizardSteps[Math.min(step, wizardSteps.length) - 1];

  // Catalogue lookups (offerings, images, cluster, wallet). Without the advanced panel these supply
  // the defaults.
  const offerings = useOfferings().data ?? [];
  // The wizard lists public images only; private ones belong to the admin catalogue.
  const myId = useAuthStore((st) => (st.claims as { sub?: string }).sub);
  const imagesRes = useImages({ public: true }).data as { data?: { id: string; name: string; registry?: string | null; owner_user_id?: string | null; supported_gpus?: string[]; cuda_version?: string | null }[] } | undefined;
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

  // GPU tiers come from the administrator's GPU presets (per-model fractions), falling back to the
  // built-in defaults when none are configured.
  const gpuPresetTiers: GpuTier[] = (usePresets('gpu').data ?? [])
    .filter((p) => p.gpu_frac != null)
    .map((p) => ({ id: p.id, name: p.name, frac: p.gpu_frac as number, mode: (p.mode === 'exclusive' ? 'exclusive' : 'fractional') as 'exclusive' | 'fractional' }));
  const gpuTiers: GpuTier[] = gpuPresetTiers.length ? gpuPresetTiers : GPU_TIERS;
  // Compute presets (cpu, mem, disk). Choosing one sets the session's resources; the custom
  // sentinel opens direct inputs instead.
  const computePresets = usePresets('compute').data ?? [];
  // No "offering default" tile: the first admin preset is the default selection.
  useEffect(() => {
    if (!form.compute_preset_id && computePresets.length > 0) {
      setForm((f) => (f.compute_preset_id ? f : { ...f, compute_preset_id: computePresets[0].id }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [computePresets.length]);
  const customCompute = form.compute_preset_id === CUSTOM_COMPUTE;
  const compute = customCompute ? undefined : computePresets.find((c) => c.id === form.compute_preset_id);

  // Per-model availability. Tiers that cannot fit on a single card are disabled, because the
  // scheduler requires a fit on one device.
  const avail = (availQuery.data ?? []).find((a) => a.gpu_model === selectedOffering?.gpu_model);
  // Tier arithmetic runs against the REAL reported card capacity when inventory has one, falling
  // back to the offering's nominal figure. A real card reports slightly under nominal (48935 MB on
  // a "48 GB" card), and fractions of the nominal size would not tile the physical card.
  const modelMem =
    Math.max(0, ...(avail?.devices.map((d) => d.total_mem_mb) ?? [])) ||
    selectedOffering?.gpu_mem_mb ||
    GPU_FALLBACK_MB;
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
  const tierVramFor = (t: GpuTier) => (t.mode === 'exclusive' ? modelMem : tierVram(t.frac, modelMem, 'fractional'));
  const tierCoresFor = (t: GpuTier) => (t.mode === 'exclusive' ? 100 : tierCores(t.frac));
  // The user's own policy headroom would never allow this tier — a quota cap, not a capacity
  // shortage, so it can never run and is not queue-able.
  function tierPolicyBlocked(t: GpuTier): boolean {
    if (!pol?.has_policy) return false;
    const vram = tierVramFor(t);
    const cores = tierCoresFor(t);
    if (pol.remaining?.gpu_mem_mb != null && vram > pol.remaining.gpu_mem_mb) return true;
    if (pol.remaining?.gpu_cores != null && cores > pol.remaining.gpu_cores) return true;
    return false;
  }
  // Is there a card whose MODE can ever host this tier? Mirrors the backend's serviceability gate:
  // exclusive needs an exclusive-mode card; a fractional tier needs a fractional or MIG card. With
  // per-card mode, a fractional-only fleet has no exclusive card, so exclusive is not offered — the
  // backend would reject it as unserviceable, never queue it.
  function tierServiceable(t: GpuTier): boolean {
    if (!availQuery.isSuccess) return true;
    if (!avail) return false;
    if (t.mode === 'exclusive') return avail.devices.some((d) => d.mode === 'exclusive');
    return avail.devices.some((d) => d.mode === 'fractional' || d.mode === 'mig');
  }
  // Can this tier EVER run for this user? false only for the permanent blockers (VRAM too small,
  // no card of this model, no card in this mode, or policy headroom). A tier that merely lacks
  // capacity right now IS possible — it queues.
  function tierPossible(t: GpuTier): boolean {
    if (tierTooSmall(t)) return false;
    if (tierPolicyBlocked(t)) return false;
    if (!availQuery.isSuccess) return true;         // do not block while availability is loading
    if (!avail) return false;                       // no ready device of this model at all
    return tierServiceable(t);                       // and a card of the right mode must exist
  }
  // Does a card have room for it right now? When not, a possible tier queues instead of failing.
  function tierFitsNow(t: GpuTier): boolean {
    if (!availQuery.isSuccess) return true;
    if (!avail) return false;
    const vram = tierVramFor(t);
    const cores = tierCoresFor(t);
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
  const catalogImages = availableImages;
  // If the current selection is no longer compatible - after changing model, say - fall back to the
  // first compatible image.
  const imageId =
    form.image_id && catalogImages.some((i) => i.id === form.image_id)
      ? form.image_id
      : catalogImages[0]?.id;
  // The backend picks an available cluster automatically; form.cluster_id is used only when the user
  // pinned one explicitly.
  const clusterId = form.cluster_id || undefined;
  const walletId = form.billing_wallet_id || wallet?.id;

  // The default tier when the user has not clicked one: the preset default (index 1) if it fits now,
  // otherwise the largest tier that fits, otherwise — nothing fits — the smallest POSSIBLE tier, so
  // the queued request is the one that dequeues soonest. Always a real, highlighted choice, never a
  // hidden 1/2 that silently gets submitted.
  const defaultTierId = (() => {
    const possible = gpuTiers.filter(tierPossible);
    if (!possible.length) return gpuTiers[0]?.id ?? '';
    const pref = gpuTiers[Math.min(1, gpuTiers.length - 1)];
    if (pref && tierPossible(pref) && tierFitsNow(pref)) return pref.id;
    const fitting = possible.filter(tierFitsNow);
    if (fitting.length) return fitting[0].id;
    return possible[possible.length - 1].id;
  })();
  const effectiveTierId = (tierId && gpuTiers.some((x) => x.id === tierId && tierPossible(x))) ? tierId : defaultTierId;
  // The effective allocation: derived from the tier and model capacity, or taken straight from the
  // form in custom mode.
  const tier = gpuTiers.find((x) => x.id === effectiveTierId) ?? gpuTiers[0];
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
    // With a compute preset chosen, send cpu, mem, and disk explicitly; the custom panel sends the
    // user's own values; otherwise the offering's defaults apply.
    if (customCompute) {
      b.cpu = form.cpu ?? 2;
      b.mem_gb = form.mem_gb ?? 4;
      b.disk_gb = form.disk_gb ?? 20;
    } else if (compute) {
      if (compute.cpu != null) b.cpu = compute.cpu;
      if (compute.mem_gb != null) b.mem_gb = compute.mem_gb;
      if (compute.disk_gb != null) b.disk_gb = compute.disk_gb;
    }
    return b;
  }

  // Re-estimate the cost whenever a step-1 choice changes (offering, tier, compute preset, image).
  // The result is shown in the order summary.
  useEffect(() => {
    if (stepKey === 'review' || !offeringId || !imageId) return;
    const t = setTimeout(() => { void previewCost.mutateAsync(toBody() as never).catch(() => undefined); }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  // selectedOffering rate in the deps: an admin price edit invalidates offerings, and the
  // refreshed rate must re-run the estimate — the review step used to keep quoting the old price.
  }, [step, offeringId, imageId, form.resource_class, effMode, effVram, effCores, form.compute_preset_id, form.cpu, form.mem_gb, form.disk_gb, activeProjectId, selectedOffering?.credit_per_hour]);

  const guard = useFormGuard(createSession.isPending);

  async function handleNext() {
    setStep((s) => Math.min(wizardSteps.length, s + 1) as Step);
    void previewCost.mutateAsync(toBody() as never).catch(() => undefined);
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
      // Explain BEFORE redirecting - a silent page change reads as a glitch, not an answer.
      if (err.code === 'insufficient_credit') {
        pushToast('error', humanizeError(err));
        navigate('/wallet/request'); // out of credits: deep-link to the request form
        return;
      }
      if (err.code === 'no_capacity') {
        pushToast('info', humanizeError(err));
        navigate('/queue');
        return;
      }
      pushToast('error', humanizeError(err));
    }
  }

  // ── Order summary derivations (display only; every value already exists above) ──
  const summaryImage = catalogImages.find((i) => i.id === imageId);
  // Names for the summary's volume lines; the picker's query is cached, so this is free.
  const summaryVolumes = ((useVolumes().data ?? []) as unknown as { id: string; name?: string | null }[]);
  const rateStr = previewCost.data && !previewCost.isError
    ? formatCredit(previewCost.data.estimated_credit_per_hour)
    : null;
  const stepReasons: string[] = (
    stepKey === 'compute' ? [!form.name && t('wizard.nameLabel')]
    : stepKey === 'gpu' ? [!offeringId && t('wizard.offeringLabel')]
    : stepKey === 'image' ? [!imageId && t('wizard.imageLabel')]
    : []
  ).filter(Boolean) as string[];

  /** The step's primary action: Next until review, then submit (with the live rate). */
  function actionButton(full: boolean) {
    const width = full ? 'w-full' : '';
    if (stepKey !== 'review') {
      return (
        <button
          type="button"
          className={`gs-btn gs-btn-primary ${width}`}
          disabled={stepReasons.length > 0}
          onClick={handleNext}
        >
          {t('wizard.next')}
        </button>
      );
    }
    const badMount = (form.volume_mounts ?? []).some((m) => mountPathInvalid(m.mount_path));
    return (
      <button
        type="button"
        className={`gs-btn gs-btn-primary ${width}`}
        disabled={createSession.isPending || concurrencyFull || badMount}
        onClick={handleSubmit}
        title={concurrencyFull ? t('wizard.concurrencyFullShort') : badMount ? t('wizard.mountPathInvalid') : undefined}
      >
        {createSession.isPending
          ? t('wizard.starting')
          : rateStr
            ? t('wizard.createWithRate', { amount: rateStr })
            : t('wizard.start')}
      </button>
    );
  }

  // The summary grows as steps are visited: nothing is pre-announced from later steps.
  const visited = (k: StepKey) => wizardSteps.indexOf(k) <= wizardSteps.indexOf(stepKey);
  const computeLines = (cpu: number, mem: number, disk: number) => (
    <div className="text-muted gs-num leading-relaxed">
      <div>{t('wizard.sumCpu', { value: cpu })}</div>
      <div>{t('wizard.sumMem', { value: mem })}</div>
      <div>{t('wizard.sumDisk', { value: disk })}</div>
    </div>
  );
  const summaryBody = (
    <>
      <h2 className="font-bold text-sm">{t('wizard.orderSummary')}</h2>
      <dl className="text-xs space-y-3 mt-3">
        <div>
          <dt className="text-muted">{isGpu ? t('wizard.gpuModel') : t('wizard.resourceClass')}</dt>
          <dd className="font-semibold mt-0.5">
            {isGpu ? (selectedOffering?.gpu_model || selectedOffering?.name || '-') : t('wizard.cpuTitle')}
          </dd>
        </div>
        {isGpu && visited('gpu') && (
          <div>
            <dt className="text-muted">{t('wizard.gpuTier')}</dt>
            <dd className="mt-0.5">
              <div className="font-semibold">
                {custom
                  ? (isExclusive ? t('wizard.modeExclusiveTitle') : t('wizard.modeFractionalTitle'))
                  : tierName(tier)}
              </div>
              <div className="text-muted gs-num leading-relaxed">
                <div>{t('wizard.vramValue', { value: gbLabel(effVram) })}</div>
                <div>{t('wizard.coresLabel', { percent: effCores })}</div>
              </div>
            </dd>
          </div>
        )}
        <div>
          <dt className="text-muted">{t('wizard.computePreset')}</dt>
          <dd className="mt-0.5">
            {customCompute
              ? computeLines(form.cpu ?? 2, form.mem_gb ?? 4, form.disk_gb ?? 20)
              : compute
                ? (
                  <>
                    <div className="font-semibold">{compute.name}</div>
                    {computeLines(compute.cpu ?? 0, compute.mem_gb ?? 0, compute.disk_gb ?? 0)}
                  </>
                )
                : '-'}
          </dd>
        </div>
        {visited('image') && (
          <div>
            <dt className="text-muted">{t('wizard.image')}</dt>
            <dd className="font-semibold mt-0.5 break-all">{summaryImage?.name ?? '-'}</dd>
          </div>
        )}
        {(form.volume_mounts?.length ?? 0) > 0 && (
          <div>
            <dt className="text-muted">{t('wizard.sumVolumes')}</dt>
            <dd className="mt-0.5 space-y-0.5">
              {(form.volume_mounts ?? []).map((mnt) => {
                const v = summaryVolumes.find((x) => x.id === mnt.volume_id);
                return (
                  <div key={mnt.volume_id} className="min-w-0">
                    <span className="font-semibold">{v?.name || mnt.volume_id}</span>
                    <span className="text-muted gs-num ml-1.5">{mnt.mode === 'ro' ? 'RO' : 'RW'} · {mnt.mount_path}</span>
                  </div>
                );
              })}
            </dd>
          </div>
        )}
      </dl>
      <div className="border-t border-border mt-3 pt-3">
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-xs font-semibold text-muted">{t('wizard.estimatedCost')}</span>
          <span className="gs-num text-md font-bold" aria-live="polite">
            {previewCost.isPending
              ? <span className="text-muted text-xs font-normal">{t('wizard.calculating')}</span>
              : previewCost.isError
                ? <span className="text-danger text-xs font-normal">{t('wizard.estimateUnavailable')}</span>
                : previewCost.data
                  ? t('wizard.ratePerHour', { amount: formatCredit(previewCost.data.estimated_credit_per_hour) })
                  : '-'}
          </span>
        </div>
        {previewCost.data != null && !previewCost.isError && (
          <div className="text-muted text-2xs mt-1 text-right">{t('wizard.holdOnStart', { amount: formatCredit(previewCost.data.hold_amount) })}</div>
        )}
        {previewCost.isError && (
          <p role="alert" className="text-danger text-2xs mt-1 text-right">{t('wizard.estimateUnavailableHint')}</p>
        )}
        {form.resource_class === 'cpu' && (
          <p className="text-muted text-2xs mt-2">{t('wizard.cpuBillingNote')}</p>
        )}
      </div>
      <div className="mt-3 space-y-2">
        {stepKey !== 'review' && <DisabledReason reasons={stepReasons} />}
        {actionButton(true)}
      </div>
    </>
  );

  return (
    <div className="w-full max-w-5xl" {...guard.props}>
      <PageHeader
        title={t('wizard.title')}
        crumbs={[{ label: t('session.title'), to: '/sessions' }, { label: t('wizard.title') }]}
      />

      {/* Resource policy limits: concurrency and remaining headroom. */}
      {pol?.has_policy && (
        <div className={`gs-card mb-4 text-xs ${concurrencyFull ? 'bg-danger-soft text-danger' : 'bg-surface-2 text-muted'}`}>
          {/* Title on the left, then one hairline-divided cell per ceiling: label above, number
              below. A comma-run sentence made four figures read as one; cells let the eye land
              on each number. */}
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
            <div className="shrink-0">
              <b>{t('wizard.policyLimits')}</b>
              {pol.scope && <span className="ml-1 opacity-75">· {scopeLabel(pol.scope)}</span>}
            </div>
            <dl className="flex flex-wrap gap-y-2 m-0">
              {pol.max_concurrent != null && (
                <PolicyCell label={t('wizard.limitConcurrent')} value={`${pol.used?.active ?? 0} / ${pol.max_concurrent}`} />
              )}
              {pol.remaining?.gpu_mem_mb != null && (
                <PolicyCell label={t('wizard.limitVram')} value={formatVram(pol.remaining.gpu_mem_mb)} />
              )}
              {pol.remaining?.cpu != null && (
                <PolicyCell label={t('wizard.limitCpu')} value={String(pol.remaining.cpu)} unit="vCPU" />
              )}
              {pol.remaining?.mem_gb != null && (
                <PolicyCell label={t('wizard.limitMem')} value={String(pol.remaining.mem_gb)} unit="GiB" />
              )}
              {pol.remaining?.gpu_cores != null && (
                <PolicyCell label={t('wizard.limitCore')} value={String(pol.remaining.gpu_cores)} unit="%" />
              )}
              {pol.remaining?.storage_gb != null && (
                <PolicyCell label={t('wizard.limitDisk')} value={String(pol.remaining.storage_gb)} unit="GB" />
              )}
            </dl>
          </div>
          {concurrencyFull && <div className="font-bold mt-2">{t('wizard.concurrencyFull')}</div>}
        </div>
      )}

      {/* Step indicator: one numbered chip per decision, GPU skipped for CPU sessions. */}
      <div className="flex items-center gap-3 mb-5 flex-wrap" data-wizard-steps>
        {wizardSteps.flatMap((key, i) => {
          const n = i + 1;
          const active = step === n;
          const done = step > n;
          const chip = (
            <div key={key} className="flex items-center gap-2 shrink-0">
              <span
                className={`w-6 h-6 rounded-full inline-flex items-center justify-center text-xs font-bold border ${
                  done ? 'bg-primary border-primary text-white' : active ? 'border-primary text-primary' : 'border-border text-muted'
                }`}
                aria-hidden="true"
              >
                {done ? '✓' : n}
              </span>
              <span className={`text-sm ${active ? 'font-bold' : done ? 'font-semibold' : 'text-muted'}`}>
                {t(`wizard.step_${key}`)}
              </span>
            </div>
          );
          return i === 0
            ? [chip]
            : [<div key={`ln-${key}`} className={`h-px flex-1 ${step > i ? 'bg-primary' : 'bg-border'}`} aria-hidden="true" />, chip];
        })}
      </div>

      <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_300px] lg:gap-4 lg:items-start">
        <div className="min-w-0">
          {stepKey === 'compute' && (
            <div className="gs-card space-y-5">
              <label className="block">
                <span className="text-xs font-semibold text-muted">{t('wizard.sessionName')}</span>
                <input className="gs-input w-full mt-1" value={form.name ?? ''} onChange={(e) => patch({ name: e.target.value })} placeholder="my-training" autoComplete="off" />
              </label>

              {/* Resource class */}
              <div>
                <span className="text-xs font-semibold text-muted">{t('wizard.resourceClass')}</span>
                <div className="grid grid-cols-2 gap-3 mt-1">
                  {(['gpu', 'cpu'] as ResourceClass[]).map((rc) => (
                    <SelTile
                      key={rc}
                      selected={form.resource_class === rc}
                      className="p-4"
                      onClick={() => patch({ resource_class: rc })}
                    >
                      <div className="font-bold pr-5">{rc === 'gpu' ? t('wizard.gpuTitle') : t('wizard.cpuTitle')}</div>
                      <div className="text-muted text-xs mt-0.5">{rc === 'gpu' ? t('wizard.gpuDesc') : t('wizard.cpuDesc')}</div>
                    </SelTile>
                  ))}
                </div>
              </div>

              {/* Compute preset (cpu, mem, disk): shared by GPU and CPU, and chosen first —
                  the same card grid the class picker uses, not a dropdown. */}
              {computePresets.length > 0 && (
                <div>
                  <span className="text-xs font-semibold text-muted">{t('wizard.computePreset')}</span>
                  <div className="grid grid-cols-2 lg:grid-cols-3 gap-3 mt-1">
                    {computePresets.map((c) => (
                      <SelTile
                        key={c.id}
                        selected={form.compute_preset_id === c.id}
                        className="p-3"
                        onClick={() => patch({ compute_preset_id: c.id })}
                      >
                        <div className="font-bold text-sm pr-5">{c.name}</div>
                        <div className="text-muted text-xs mt-1 gs-num">
                          {t('wizard.computeLine', { cpu: c.cpu, mem: c.mem_gb, disk: c.disk_gb ?? 0 })}
                        </div>
                      </SelTile>
                    ))}
                    <SelTile
                      selected={customCompute}
                      className="p-3"
                      onClick={() => patch({ compute_preset_id: CUSTOM_COMPUTE })}
                    >
                      <div className="font-bold text-sm pr-5">{t('wizard.computeCustom')}</div>
                      <div className="text-muted text-2xs mt-1">{t('wizard.computeCustomDesc')}</div>
                    </SelTile>
                  </div>
                  {customCompute && (
                    <div className="mt-2 grid grid-cols-3 gap-4 border border-border rounded-card p-3">
                      <Slider label={t('wizard.customCpu')} min={1} max={CUSTOM_LIMITS.cpu} step={1} value={form.cpu ?? 2} onChange={(v) => patch({ cpu: v })} />
                      <Slider label={t('wizard.customMem')} min={1} max={CUSTOM_LIMITS.mem_gb} step={1} value={form.mem_gb ?? 4} onChange={(v) => patch({ mem_gb: v })} />
                      <Slider label={t('wizard.customDisk')} min={10} max={CUSTOM_LIMITS.disk_gb} step={10} value={form.disk_gb ?? 20} onChange={(v) => patch({ disk_gb: v })} />
                    </div>
                  )}
                </div>
              )}

            </div>
          )}

          {stepKey === 'gpu' && (
            <div className="gs-card space-y-5">
              {isGpu && (
                <div>
                  <span className="text-xs font-semibold text-muted">{t('wizard.gpuModel')}</span>
                  {availQuery.isSuccess && gpuModels.length === 0 && (
                    <p className="text-muted text-xs mt-1">{t('wizard.noGpu')}</p>
                  )}
                  <div className="grid sm:grid-cols-2 gap-3 mt-1" data-model-grid>
                    {gpuModels.map((o) => {
                      const mAvail = (availQuery.data ?? []).find((a) => a.gpu_model === o.gpu_model);
                      // Same traffic light as the dashboard: share of VRAM still free across the
                      // model's cards. Card counts are operator detail and stay hidden here too.
                      const mFree = mAvail?.devices.reduce((sum, d) => sum + d.free_mem_mb, 0) ?? 0;
                      const mTotal = mAvail?.devices.reduce((sum, d) => sum + d.total_mem_mb, 0) ?? 0;
                      const mLevel = mTotal <= 0 || mFree <= 0 ? 'unavailable'
                        : mFree / mTotal >= 0.6 ? 'free' : mFree / mTotal >= 0.3 ? 'moderate' : 'congested';
                      return (
                        <SelTile
                          key={o.id}
                          selected={offeringId === o.id}
                          className="p-3"
                          onClick={() => patch({ offering_id: o.id })}
                        >
                          <div className="flex items-center gap-2 pr-5 flex-wrap">
                            <span className="font-bold text-sm">{o.gpu_model || o.name}</span>
                            {mAvail && <StatusPill kind={mLevel} label={t(`dashboard.congestion.${mLevel}`)} />}
                          </div>
                          <div className="mt-1.5 flex items-center justify-between gap-2 text-xs text-muted">
                            <span className="gs-num">{t('wizard.vramValue', { value: gbLabel(o.gpu_mem_mb) })}</span>
                            <span className="gs-num">{t('wizard.ratePerHour', { amount: formatCredit(Number(o.credit_per_hour)) })}</span>
                          </div>
                        </SelTile>
                      );
                    })}
                  </div>
                  {selectedOffering && (
                    <span className="text-muted text-2xs mt-1.5 block">
                      {t('wizard.tierDerivedFrom', { capacity: gbLabel(modelMem) })}
                    </span>
                  )}
                </div>
              )}

              {/* GPU preset tiers, as fractions of the selected model's capacity. */}
              {isGpu && (
                <div>
                  <span className="text-xs font-semibold text-muted">{t('wizard.gpuTier')}</span>
                  <div className="grid grid-cols-2 gap-3 mt-1">
                    {gpuTiers.map((tier) => {
                      const possible = tierPossible(tier);       // can ever run (else hard-disabled)
                      const fitsNow = tierFitsNow(tier);          // has capacity right now
                      const queueable = possible && !fitsNow;     // selectable, but will queue
                      const active = possible && !custom && effectiveTierId === tier.id;
                      const vram = tierVram(tier.frac, modelMem, tier.mode);
                      const cores = tier.mode === 'exclusive' ? 100 : tierCores(tier.frac);
                      const blockLabel = tierTooSmall(tier)
                        ? t('wizard.vramTooSmall')
                        : tierPolicyBlocked(tier)
                          ? t('wizard.tierPolicyBlocked')
                          : !tierServiceable(tier)
                            ? t('wizard.tierUnavailable')         // no card in this mode (e.g. no exclusive card)
                            : t('wizard.capacityShort');          // !avail: no card of this model
                      return (
                        <SelTile
                          key={tier.id}
                          selected={active}
                          disabled={!possible}
                          className="p-3"
                          onClick={() => {
                            if (!possible) return;
                            setTierId(tier.id);
                            setCustom(false);
                          }}
                        >
                          <div className="flex items-center justify-between gap-1 pr-5 flex-wrap">
                            <span className="font-bold text-sm">{tierName(tier)}</span>
                            {!possible ? (
                              <span className="gs-pill bg-danger-soft text-danger">{blockLabel}</span>
                            ) : queueable ? (
                              <span className="gs-pill bg-warn-soft text-warn">{t('wizard.tierQueues')}</span>
                            ) : (
                              <span className="gs-tag">{t('wizard.occupancyBilled', { percent: tierOccupancy(vram, cores, modelMem) })}</span>
                            )}
                          </div>
                          <div className="text-xs mt-1">{t('wizard.vramValue', { value: gbLabel(vram) })} · {t('wizard.coresLabel', { percent: cores })}{tier.mode === 'exclusive' ? t('wizard.exclusiveSuffix') : ''}</div>
                          <div className="text-muted text-2xs mt-0.5">{tierHint(tier)}</div>
                        </SelTile>
                      );
                    })}
                  </div>
                  {availQuery.isSuccess && gpuTiers.some(tierPossible) && !gpuTiers.some(tierFitsNow) && (
                    <p className="text-warn text-xs mt-2">{t('wizard.noCapacityWarn')}</p>
                  )}

                  <button
                    type="button"
                    className="text-primary text-xs font-semibold mt-2 inline-flex items-center gap-1.5 hover:underline"
                    onClick={() =>
                      setCustom((c) => {
                        const next = !c;
                        if (next) patch({ sharing_mode: effMode, vram_mb: effVram, core_percent: effCores });
                        return next;
                      })
                    }
                  >
                    {custom ? <CaretDown size={13} aria-hidden="true" /> : <CaretRight size={13} aria-hidden="true" />}
                    {t('wizard.customToggle')}
                  </button>
                  {custom && (
                    <div className="mt-2 space-y-3 border border-border rounded-card p-3">
                      <div className="grid grid-cols-2 gap-2">
                        <ModeOpt active={!isExclusive} title={t('wizard.modeFractionalTitle')} desc={t('wizard.modeFractionalDesc')} onClick={() => patch({ sharing_mode: 'fractional' })} />
                        <ModeOpt active={isExclusive} title={t('wizard.modeExclusiveTitle')} desc={t('wizard.modeExclusiveDesc')} onClick={() => patch({ sharing_mode: 'exclusive' })} />
                      </div>
                      <div className="grid grid-cols-2 gap-4">
                        <Slider label={t('wizard.vramMb')} min={512} max={modelMem} step={512} value={isExclusive ? modelMem : (form.vram_mb ?? effVram)} disabled={isExclusive} onChange={(v) => patch({ vram_mb: v })} />
                        <Slider label={t('wizard.corePercent')} min={5} max={100} step={5} value={isExclusive ? 100 : (form.core_percent ?? effCores)} disabled={isExclusive} onChange={(v) => patch({ core_percent: v })} />
                      </div>
                      {!isExclusive && isImbalanced(form.vram_mb ?? 0, form.core_percent ?? 0, modelMem) && (
                        <p className="text-warn text-xs">
                          <Trans i18nKey="wizard.imbalancedWarn" values={{ capacity: gbLabel(modelMem) }} components={{ 1: <b /> }} />
                        </p>
                      )}
                    </div>
                  )}
                </div>
              )}

            </div>
          )}

          {stepKey === 'image' && (
            <div className="gs-card space-y-5">
              <div>
                <span className="text-xs font-semibold text-muted">{t('wizard.image')} <span className="text-danger">*</span></span>
                {catalogImages.length === 0 ? (
                  <p className="text-muted text-xs mt-1">{isGpu ? t('wizard.noCompatibleImage') : t('wizard.noImage')}</p>
                ) : (
                  <div className="grid sm:grid-cols-2 gap-3 mt-1" data-image-grid>
                    {catalogImages.map((im) => (
                      <SelTile
                        key={im.id}
                        selected={imageId === im.id}
                        className="p-3"
                        onClick={() => patch({ image_id: im.id })}
                      >
                        <div className="flex items-start gap-2.5 pr-5">
                          <Cube size={18} aria-hidden="true" className={`shrink-0 mt-0.5 ${imageId === im.id ? 'text-primary' : 'text-muted'}`} />
                          <div className="min-w-0">
                            <div className="font-bold text-sm break-all">{im.name}{im.owner_user_id === myId && <span className="gs-tag ml-1.5 align-middle">{t('images.myTag')}</span>}</div>
                            {(im.cuda_version || im.supported_gpus?.length) ? (
                              <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
                                {im.cuda_version && <span className="gs-tag">{t('wizard.cudaTag', { version: im.cuda_version })}</span>}
                                {im.supported_gpus?.length ? <span className="gs-tag">{t('wizard.imageGpuCount', { n: im.supported_gpus.length })}</span> : null}
                              </div>
                            ) : null}
                          </div>
                        </div>
                      </SelTile>
                    ))}
                  </div>
                )}
                {isGpu && selectedOffering?.gpu_model && (
                  <span className="text-muted text-2xs mt-1.5 block">
                    {t('wizard.compatibleOnly', { model: selectedOffering.gpu_model })}
                    {selectedOffering.min_cuda ? t('wizard.cudaRequired', { version: selectedOffering.min_cuda }) : ''}.
                  </span>
                )}
              </div>

              {/* How billing works. The live estimate itself lives in the order summary. */}
              {offeringId && imageId && (
                <div className="rounded-card border border-border bg-surface-2 p-3 text-xs text-muted">
                  <b className="text-text">{t('wizard.billingNote')}</b> -{' '}
                  {isGpu ? t('wizard.billingGpu') : t('wizard.billingCpu')}
                </div>
              )}
            </div>
          )}

          {stepKey === 'review' && (
            <div className="gs-card space-y-4">
              <h2 className="font-bold">{t('wizard.volumeMounts')}</h2>
              <VolumePicker mounts={form.volume_mounts ?? []} onChange={(m) => patch({ volume_mounts: m })} />
            </div>
          )}

          {step > 1 && (
            <div className="mt-4">
              <button type="button" className="gs-btn" onClick={() => setStep((s) => Math.max(1, s - 1) as Step)}>{t('wizard.back')}</button>
            </div>
          )}
        </div>

        {/* Order summary: sticky right column on large screens. */}
        <aside className="hidden lg:block lg:sticky lg:top-4" data-order-summary>
          <div className="gs-card p-4">{summaryBody}</div>
        </aside>
      </div>

      {/* Order bar: the compact sticky equivalent below lg. */}
      <div className="lg:hidden sticky bottom-2 z-10 mt-4 gs-card p-3 shadow-raised" data-order-bar>
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="gs-num text-sm font-bold">
              {previewCost.isPending
                ? <span className="text-muted font-normal">{t('wizard.calculating')}</span>
                : rateStr
                  ? t('wizard.ratePerHour', { amount: rateStr })
                  : <span className="text-muted font-normal">-</span>}
            </div>
            {previewCost.data != null && !previewCost.isError && (
              <div className="text-muted text-2xs">{t('wizard.holdOnStart', { amount: formatCredit(previewCost.data.hold_amount) })}</div>
            )}
          </div>
          <div className="shrink-0">{actionButton(false)}</div>
        </div>
        {stepKey !== 'review' && <DisabledReason reasons={stepReasons} />}
      </div>
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

  if (isLoading) return <p className="text-muted text-xs">{t('wizard.loadingVolumes')}</p>;
  if (volumes.length === 0)
    return <p className="text-muted text-xs">{t('wizard.noVolumes')}</p>;

  return (
    <div className="space-y-2">
      {volumes.map((v) => {
        const mount = byId(v.id);
        const locked = !canWriteVolume(v);          // not writable, so the mount is pinned to ro
        return (
          <div key={v.id} className="border border-border rounded-card p-3">
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={!!mount} onChange={() => toggle(v)} />
              <span className="font-semibold text-sm">{(v as { name?: string }).name || v.type || v.id}</span>
              <span className="text-muted text-2xs">
                {v.type} · {v.access_mode} · {v.used_gb}/{v.quota_gb} GiB
                {locked && <span className="ml-1 text-warn">{t('wizard.readOnlyLocked')}</span>}
              </span>
            </label>
            {mount && (
              <div className="mt-2 grid grid-cols-[1fr_auto] gap-2 items-center">
                <input
                  className={`gs-input px-2 py-1.5 text-xs font-mono ${mountPathInvalid(mount.mount_path) ? 'border-danger' : ''}`}
                  value={mount.mount_path}
                  aria-invalid={mountPathInvalid(mount.mount_path)}
                  onChange={(e) => update(v.id, { mount_path: e.target.value })}
                  placeholder="/data/…" autoComplete="off" />
                <Select
                  className="gs-input px-2 py-1.5 text-xs"
                  value={mount.mode}
                  disabled={locked}
                  onChange={(e) => update(v.id, { mode: e.target.value as VolumeMount['mode'] })}
                >
                  <option value="rw">{t('wizard.mountRw')}</option>
                  <option value="ro">{t('wizard.mountRo')}</option>
                </Select>
                {mountPathInvalid(mount.mount_path)
                  ? <p role="alert" className="col-span-2 text-danger text-2xs font-medium">{t('wizard.mountPathInvalid')}</p>
                  : <p className="col-span-2 text-muted text-2xs">{t('wizard.mountPathHint')}</p>}
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
    <SelTile selected={active} className="p-3" onClick={onClick}>
      <div className="font-bold text-sm pr-5">{title}</div>
      <div className="text-muted text-2xs mt-1">{desc}</div>
    </SelTile>
  );
}

/** One ceiling in the policy band: label above, figure below, a hairline between cells. Colour is
 *  inherited from the band (muted, or danger when the concurrency limit is hit); hierarchy comes
 *  from size and weight only, so the band informs without shouting. */
function PolicyCell({ label, value, unit }: { label: string; value: string; unit?: string }) {
  return (
    <div className="pr-5 mr-5 border-r border-border last:border-r-0 last:mr-0 last:pr-0">
      <dt className="font-semibold opacity-75">{label}</dt>
      <dd className="m-0 mt-0.5 text-sm font-semibold gs-num">
        {value}
        {unit && <small className="ml-1 text-xs font-medium opacity-75">{unit}</small>}
      </dd>
    </div>
  );
}

function Slider({ label, min, max, step, value, disabled, onChange }: { label: string; min: number; max: number; step: number; value: number; disabled?: boolean; onChange: (v: number) => void }) {
  const { t } = useTranslation();
  return (
    <label className="block">
      <span className="text-xs font-semibold text-muted flex justify-between">
        <span>{label}</span>
        <span><span className="gs-num">{value}</span>{disabled ? t('wizard.fixed') : ''}</span>
      </span>
      <input type="range" className="w-full mt-1" min={min} max={max} step={step} value={value} disabled={disabled} onChange={(e) => onChange(Number(e.target.value))} autoComplete="off" />
    </label>
  );
}
