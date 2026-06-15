import { describe, it, expect } from 'vitest';
import { atLeast } from './rbac';
import { parseJwt } from './jwt';

describe('rbac.atLeast', () => {
  it('a higher role satisfies a lower requirement', () => {
    expect(atLeast('org_admin', 'group_admin')).toBe(true);
    expect(atLeast('member', 'org_admin')).toBe(false);
    expect(atLeast(undefined, 'member')).toBe(false);
  });
});

describe('parseJwt', () => {
  it('an invalid token yields an empty claims object', () => {
    expect(parseJwt('not-a-jwt')).toEqual({});
  });
});
