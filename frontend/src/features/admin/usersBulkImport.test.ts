import { describe, expect, it } from 'vitest';
import { credentialsCsv, parseCsv } from './UsersBulkImport';

describe('parseCsv', () => {
  it('parses email,name rows and lowercases emails', () => {
    const rows = parseCsv('A@U.AC.KR,Kim\nb@u.ac.kr,Lee');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({ email: 'a@u.ac.kr', name: 'Kim', problem: null });
  });

  it('skips a header row', () => {
    const rows = parseCsv('email,name\na@u.ac.kr,Kim');
    expect(rows).toHaveLength(1);
    expect(rows[0].email).toBe('a@u.ac.kr');
  });

  it('flags invalid emails, in-file duplicates, and missing names', () => {
    const rows = parseCsv('a@u.ac.kr,Kim\nnot-an-email,X\na@u.ac.kr,Kim2\nc@u.ac.kr,');
    expect(rows.map((r) => r.problem)).toEqual([null, 'invalid_email', 'duplicate', 'missing_name']);
  });

  it('handles quoted names containing commas', () => {
    const rows = parseCsv('a@u.ac.kr,"Kim, Cheolsu"');
    expect(rows[0]).toMatchObject({ name: 'Kim, Cheolsu', problem: null });
  });
});

describe('credentialsCsv', () => {
  it('emits only created rows with their one-time passwords', () => {
    const csv = credentialsCsv([
      { row: 0, email: 'a@u.ac.kr', status: 'created', initial_password: 'pw-1' },
      { row: 1, email: 'b@u.ac.kr', status: 'exists' },
    ]);
    expect(csv).toBe('email,initial_password\na@u.ac.kr,pw-1');
  });
});
