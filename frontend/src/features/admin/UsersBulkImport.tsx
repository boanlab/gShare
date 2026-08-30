import { useMemo, useRef, useState } from 'react';
import { Select } from '@/components/Select';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import Papa from 'papaparse';
import {
  useBulkCreateUsers,
  type BulkRowResult,
  type BulkUserRow,
} from '@/api/hooks/useUsers';
import { useProjects, useOrganizations } from '@/api/hooks/useGroups';
import { useAuthStore } from '@/auth/authStore';
import { PageHeader } from '@/components/PageHeader';
import { Field } from '@/components/Field';
import { useUiStore } from '@/store/uiStore';
import { humanizeError, asApiError } from '@/lib/errors';

// Roster import: parse a CSV of email,name locally, preview with per-row validation, upload in
// chunks of 200 (the API's batch cap), then hand the admin a credentials CSV - the initial
// passwords exist only in these responses, never again (must_change_password rotates them at
// first login).

const emailOk = (e: string) => /\S+@\S+\.\S+/.test(e.trim()) && !e.trim().endsWith('.');
const CHUNK = 200;

export interface ParsedRow extends BulkUserRow {
  line: number;
  problem: 'invalid_email' | 'duplicate' | 'missing_name' | null;
}

export function parseCsv(text: string): ParsedRow[] {
  const parsed = Papa.parse<string[]>(text.trim(), { skipEmptyLines: true });
  const seen = new Set<string>();
  const rows: ParsedRow[] = [];
  parsed.data.forEach((cols, i) => {
    const [rawEmail = '', rawName = ''] = cols;
    const email = rawEmail.trim().toLowerCase();
    // Tolerate a header row: skip a first line whose email column does not look like an email.
    if (i === 0 && !emailOk(email)) return;
    const name = rawName.trim();
    let problem: ParsedRow['problem'] = null;
    if (!emailOk(email)) problem = 'invalid_email';
    else if (seen.has(email)) problem = 'duplicate';
    else if (!name) problem = 'missing_name';
    if (!problem) seen.add(email);
    rows.push({ line: i + 1, email, name, problem });
  });
  return rows;
}

export function credentialsCsv(results: BulkRowResult[]): string {
  const created = results.filter((r) => r.status === 'created' && r.initial_password);
  const lines = ['email,initial_password', ...created.map((r) => `${r.email},${r.initial_password}`)];
  return lines.join('\n');
}

export function UsersBulkImportPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const claims = useAuthStore((s) => s.claims);
  const orgAdminOrgs = useAuthStore((s) => s.orgAdminOrgs);
  const memberships = useAuthStore((s) => s.memberships);
  const canListOrgs = claims.global_role === 'super_admin' || orgAdminOrgs.length > 0
    || memberships.some((m) => m.role === 'org_admin');

  const [orgId, setOrgId] = useState('');
  const [groupId, setGroupId] = useState('');
  const [rows, setRows] = useState<ParsedRow[]>([]);
  const [fileName, setFileName] = useState('');
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [results, setResults] = useState<BulkRowResult[] | null>(null);
  // One idempotency scope per parsed file: retrying the same upload replays, a new file restarts.
  const batchRef = useRef('');

  const orgs = useOrganizations({ enabled: canListOrgs }).data ?? [];
  const groups = (useProjects(orgId || undefined).data ?? []).filter((g) => !orgId || g.org_id === orgId);
  const bulk = useBulkCreateUsers();
  const pushToast = useUiStore((s) => s.pushToast);

  const valid = useMemo(() => rows.filter((r) => !r.problem), [rows]);
  const invalid = useMemo(() => rows.filter((r) => r.problem), [rows]);

  const onFile = (file: File) => {
    setResults(null);
    setFileName(file.name);
    batchRef.current = `${file.name}:${file.size}:${Date.now()}`;
    file.text().then((text) => setRows(parseCsv(text)));
  };

  const submit = async () => {
    if (!groupId || valid.length === 0) return;
    setProgress({ done: 0, total: valid.length });
    const collected: BulkRowResult[] = [];
    try {
      for (let i = 0; i < valid.length; i += CHUNK) {
        const chunk = valid.slice(i, i + CHUNK).map(({ email, name }) => ({ email, name }));
        const res = await bulk.mutateAsync({
          body: { group_id: groupId, initial_role: 'member', rows: chunk },
          idem: `${batchRef.current}:${i}`,
        });
        collected.push(...res.results);
        setProgress({ done: Math.min(i + CHUNK, valid.length), total: valid.length });
      }
      setResults(collected);
      const created = collected.filter((r) => r.status === 'created').length;
      pushToast('success', t('admin.users.bulk.done', { created, total: valid.length }));
    } catch (e) {
      setProgress(null);
      pushToast('error', humanizeError(asApiError(e)));
    }
  };

  const downloadCredentials = () => {
    if (!results) return;
    const blob = new Blob([credentialsCsv(results)], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `gshare-credentials-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const createdCount = results?.filter((r) => r.status === 'created').length ?? 0;

  return (
    <div className="w-full">
      <PageHeader title={t('admin.users.bulk.title')} description={t('admin.users.bulk.subtitle')} />

      <div className="gs-card max-w-3xl space-y-4">
        {canListOrgs && (
          <Field label={t('common.organization')} htmlFor="bulk-org">
            {(ids) => (
              <Select {...ids} className="gs-input" value={orgId}
                onChange={(e) => { setOrgId(e.target.value); setGroupId(''); }}>
                <option value="">{t('admin.users.bulk.allOrgs')}</option>
                {orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
              </Select>
            )}
          </Field>
        )}
        <Field label={t('common.group')} htmlFor="bulk-group" required>
          {(ids) => (
            <Select {...ids} className="gs-input" value={groupId} onChange={(e) => setGroupId(e.target.value)}>
              <option value="">{t('admin.users.bulk.pickGroup')}</option>
              {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
            </Select>
          )}
        </Field>

        <Field label={t('admin.users.bulk.file')} htmlFor="bulk-file" required
          hint={t('admin.users.bulk.fileHint')}>
          {(ids) => (
            <input
              {...ids} type="file" accept=".csv,text/csv" className="gs-input"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); }}
            />
          )}
        </Field>

        {rows.length > 0 && !results && (
          <>
            <div className="text-sm">
              {t('admin.users.bulk.parsed', { file: fileName, valid: valid.length, invalid: invalid.length })}
            </div>
            {invalid.length > 0 && (
              <div className="bg-danger-soft text-danger rounded-ctl p-2 text-xs max-h-40 overflow-y-auto">
                {invalid.slice(0, 50).map((r) => (
                  <div key={r.line}>
                    {t('admin.users.bulk.badRow', { line: r.line, email: r.email || '-' })}
                    {' '}
                    ({t(`admin.users.bulk.problem.${r.problem}`)})
                  </div>
                ))}
                {invalid.length > 50 && <div>…{invalid.length - 50}</div>}
              </div>
            )}
            <div className="max-h-56 overflow-y-auto border border-border rounded">
              <table className="w-full text-xs">
                <thead><tr className="text-left text-muted">
                  <th className="p-1.5">{t('common.email')}</th><th className="p-1.5">{t('common.name')}</th>
                </tr></thead>
                <tbody>
                  {valid.slice(0, 100).map((r) => (
                    <tr key={r.email}><td className="p-1.5 font-mono">{r.email}</td><td className="p-1.5">{r.name}</td></tr>
                  ))}
                  {valid.length > 100 && (
                    <tr><td className="p-1.5 text-muted" colSpan={2}>+{valid.length - 100}</td></tr>
                  )}
                </tbody>
              </table>
            </div>
            <button
              type="button" className="gs-btn gs-btn-primary"
              disabled={!groupId || valid.length === 0 || progress !== null}
              onClick={submit}
            >
              {progress
                ? t('admin.users.bulk.uploading', { done: progress.done, total: progress.total })
                : t('admin.users.bulk.submit', { count: valid.length })}
            </button>
          </>
        )}

        {results && (
          <div className="space-y-3">
            <div className="text-sm">
              {t('admin.users.bulk.resultSummary', {
                created: createdCount,
                exists: results.filter((r) => r.status === 'exists').length,
                invalid: results.filter((r) => r.status === 'invalid').length,
              })}
            </div>
            {createdCount > 0 && (
              <>
                <div className="bg-warn-soft text-warn rounded-ctl p-2 text-xs">
                  {t('admin.users.bulk.passwordWarning')}
                </div>
                <button type="button" className="gs-btn gs-btn-primary" onClick={downloadCredentials}>
                  {t('admin.users.bulk.downloadCredentials', { count: createdCount })}
                </button>
              </>
            )}
            <div>
              <button type="button" className="gs-btn" onClick={() => navigate('/admin/users')}>
                {t('admin.users.bulk.backToList')}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
