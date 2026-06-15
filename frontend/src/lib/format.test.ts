import { describe, it, expect } from 'vitest';
import '@/i18n';
import {
  roleLabel,
  userStatusLabel,
  scopeLabel,
  accessModeLabel,
  sessionStatusLabel,
  reqStatusLabel,
  formatVram,
  formatDuration,
} from './format';

describe('roleLabel', () => {
  it('renders a known role and returns an empty string for none', () => {
    expect(roleLabel('super_admin')).toBe('Super Admin');
    expect(roleLabel('group_admin')).toBe('Group Admin');
    expect(roleLabel(null)).toBe('');
  });
  it('title-cases an unknown code as a fallback', () => {
    expect(roleLabel('billing_viewer')).toBe('Billing Viewer');
  });
});

describe('enum code to label', () => {
  it('translates one representative value of each kind', () => {
    expect(userStatusLabel('active')).toBe('Active');
    expect(scopeLabel('group')).toBe('Group');
    expect(accessModeLabel('RWX')).toBe('Shared read/write');
    expect(sessionStatusLabel('running')).toBe('Running');
    expect(reqStatusLabel('approved')).toBe('Approved');
  });
  it('passes an unknown code through, and renders null as a dash', () => {
    expect(sessionStatusLabel('weird_state')).toBe('weird_state');
    expect(scopeLabel(null)).toBe('-');
  });
});

describe('formatVram', () => {
  it('uses GiB from 1024 MiB up, MiB below, and a dash for null', () => {
    expect(formatVram(49152)).toBe('48 GiB');
    expect(formatVram(1536)).toBe('1.5 GiB');
    expect(formatVram(512)).toBe('512 MiB');
    expect(formatVram(null)).toBe('-');
  });
});

describe('formatDuration', () => {
  it('formats hours, minutes, and seconds against a fixed reference time', () => {
    const start = '2026-06-13T00:00:00Z';
    const startMs = new Date(start).getTime();
    expect(formatDuration(start, startMs + 2 * 3600_000 + 13 * 60_000)).toBe('2h 13m');
    expect(formatDuration(start, startMs + 5 * 60_000 + 2_000)).toBe('5m 02s');
    expect(formatDuration(start, startMs + 7_000)).toBe('7s');
  });
  it('renders an em dash for a missing or invalid start', () => {
    expect(formatDuration(null)).toBe('—');
    expect(formatDuration('not-a-date')).toBe('—');
  });
});
