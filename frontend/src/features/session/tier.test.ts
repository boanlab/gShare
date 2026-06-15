import { describe, it, expect } from 'vitest';
import {
  GPU_TIERS,
  tierVram,
  tierCores,
  tierOccupancy,
  gbLabel,
  isImbalanced,
} from './tier';

const REF = 49152; // a full card of roughly PRO 6000 size, in MB

describe('tierVram', () => {
  it('exclusive takes the model full-card capacity as-is', () => {
    expect(tierVram(1, REF, 'exclusive')).toBe(REF);
  });
  it('fractional snaps capacity times the fraction to a 512 MB boundary', () => {
    expect(tierVram(1 / 4, REF, 'fractional')).toBe(12288); // 49152*0.25
    expect(tierVram(1 / 2, REF, 'fractional')).toBe(24576);
  });
  it('guarantees at least 512 MB, however small the fraction', () => {
    expect(tierVram(1 / 1000, REF, 'fractional')).toBe(512);
  });
});

describe('tierCores', () => {
  it('converts a fraction to a core percentage, clamped to 5-100', () => {
    expect(tierCores(1 / 4)).toBe(25);
    expect(tierCores(1)).toBe(100);
    expect(tierCores(1 / 1000)).toBe(5); // lower bound
    expect(tierCores(2)).toBe(100); // upper bound
  });
});

describe('tierOccupancy', () => {
  it('bills on whichever of the VRAM and core shares is larger', () => {
    expect(tierOccupancy(12288, 25, REF)).toBe(25); // both are 0.25
    expect(tierOccupancy(1024, 100, REF)).toBe(100); // cores dominate
  });
});

describe('gbLabel', () => {
  it('renders whole GB without a decimal, and one decimal otherwise', () => {
    expect(gbLabel(24576)).toBe('24GB');
    expect(gbLabel(12288)).toBe('12GB');
    expect(gbLabel(512)).toBe('0.5GB');
  });
});

describe('isImbalanced, matching the backend _validate_balance', () => {
  it('little VRAM with many cores is unbalanced', () => {
    expect(isImbalanced(1024, 100, REF)).toBe(true);
  });
  it('a proportional allocation (a quarter of VRAM, 25% of cores) is balanced', () => {
    expect(isImbalanced(12288, 25, REF)).toBe(false);
  });
  it('half the VRAM with 50% of the cores is balanced too', () => {
    expect(isImbalanced(24576, 50, REF)).toBe(false);
  });
});

describe('GPU_TIERS', () => {
  it('has S, M, and L shared tiers plus an exclusive full card, with ascending fractions', () => {
    expect(GPU_TIERS.map((t) => t.id)).toEqual(['s', 'm', 'l', 'x']);
    expect(GPU_TIERS.find((t) => t.id === 'x')?.mode).toBe('exclusive');
    expect(GPU_TIERS.filter((t) => t.mode === 'fractional')).toHaveLength(3);
  });
});
