// GPU preset tiers, with occupancy and balance arithmetic. Pure functions, independent of the UI.
//
// The balance rule mirrors the backend: the core share must stay within twice the VRAM share,
// measured against the model's full card, which is what blocks asymmetric allocations. Two modes,
// shared and exclusive.

export const GPU_FALLBACK_MB = 24576; // fallback when the offering reports no capacity (about 24 GB)

/**
 * A GPU tier, from either source. Built-in tiers carry translation keys; tiers an administrator
 * defined as presets carry a literal `name` the administrator typed, which is not translated.
 */
export interface GpuTier {
  id: string;
  frac: number;
  mode: 'fractional' | 'exclusive';
  name?: string;
  nameKey?: string;
  hintKey?: string;
}

// The ids and fractions are part of the contract with the backend and do not change.
export const GPU_TIERS: GpuTier[] = [
  { id: 's', nameKey: 'tier.s', frac: 1 / 8, mode: 'fractional', hintKey: 'tier.sHint' },
  { id: 'm', nameKey: 'tier.m', frac: 1 / 4, mode: 'fractional', hintKey: 'tier.mHint' },
  { id: 'l', nameKey: 'tier.l', frac: 1 / 2, mode: 'fractional', hintKey: 'tier.lHint' },
  { id: 'x', nameKey: 'tier.x', frac: 1, mode: 'exclusive', hintKey: 'tier.xHint' },
];

// Tier to actual VRAM in MB: exclusive takes the full card; fractional snaps model capacity times
// the fraction to a 512 MB boundary.
export function tierVram(frac: number, modelMem: number, mode: string): number {
  if (mode === 'exclusive') return modelMem;
  return Math.max(512, Math.round((modelMem * frac) / 512) * 512);
}

export function tierCores(frac: number): number {
  return Math.min(100, Math.max(5, Math.round(frac * 100)));
}

export function tierOccupancy(vram: number, cores: number, modelMem: number): number {
  return Math.round(Math.max(vram / modelMem, cores / 100) * 100);
}

export function gbLabel(mb: number): string {
  const gb = mb / 1024;
  return Number.isInteger(gb) ? `${gb}GB` : `${gb.toFixed(1)}GB`;
}

// Warn about an unbalanced custom allocation before the backend rejects it: the rule is the same,
// a core-to-VRAM share ratio above two, measured against the model's full card.
export function isImbalanced(vram: number, cores: number, modelMem: number): boolean {
  const memFrac = vram / modelMem;
  const coreFrac = cores / 100;
  const hi = Math.max(memFrac, coreFrac);
  const lo = Math.max(Math.min(memFrac, coreFrac), 0.05);
  return hi > 2 * lo;
}
