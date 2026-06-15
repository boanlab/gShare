import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/auth/authStore';
import { useUiStore } from '@/store/uiStore';
import { PageHeader, BackLink } from '@/components/PageHeader';
import { Field, DisabledReason } from '@/components/Field';
import { useUnsavedGuard, unsavedGuardProps } from '@/hooks/useUnsavedGuard';

// Voluntary password change, on its own page inside the app shell (/account/password). The current
// password is verified, then the new token from the response replaces the stored one.
export function PasswordPage() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const changePassword = useAuthStore((s) => s.changePassword);
  const pushToast = useUiStore((s) => s.pushToast);
  const [cur, setCur] = useState('');
  const [pw, setPw] = useState('');
  const [pw2, setPw2] = useState('');
  const [busy, setBusy] = useState(false);
  const [touched, setTouched] = useState(false);
  // Both rules are checked as the user types, so neither is discovered only on submit.
  const tooShort = pw.length > 0 && pw.length < 8;
  const mismatch = pw2.length > 0 && pw !== pw2;
  useUnsavedGuard((!!cur || !!pw || !!pw2) && !busy);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (pw.length < 8) { pushToast('error', t('account.newPasswordTooShort')); return; }
    if (pw !== pw2) { pushToast('error', t('auth.mismatch')); return; }
    setBusy(true);
    try {
      await changePassword(pw, cur);   // verifies `cur` and stores the fresh token in authStore
      pushToast('success', t('auth.changed'));
      navigate('/account', { replace: true });
    } catch {
      pushToast('error', t('auth.wrongCurrent'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="w-full">
      <PageHeader
        title={t('auth.changePassword')}
        crumbs={[{ label: t('account.title'), to: '/account' }, { label: t('auth.changePassword') }]}
        actions={<BackLink to="/account" label={t('account.backToAccount')} />}
      />
      <div className="gs-card max-w-xl">
        <form className="space-y-3" {...unsavedGuardProps} onSubmit={submit} noValidate>
          <Field label={t('auth.currentPassword')} required>
            {(ids) => (
              <input {...ids} type="password" autoComplete="current-password" className="gs-input w-full" value={cur} onChange={(e) => setCur(e.target.value)} autoFocus />
            )}
          </Field>
          <Field
            label={t('auth.password')}
            required
            hint={t('account.newPasswordHint')}
            error={(touched || pw.length > 0) && tooShort ? t('account.newPasswordTooShort') : null}
          >
            {(ids) => (
              <input {...ids} type="password" autoComplete="new-password" className="gs-input w-full" value={pw} onChange={(e) => setPw(e.target.value)} />
            )}
          </Field>
          <Field label={t('auth.confirmPassword')} required error={mismatch ? t('auth.mismatch') : null}>
            {(ids) => (
              <input {...ids} type="password" autoComplete="new-password" className="gs-input w-full" value={pw2} onChange={(e) => setPw2(e.target.value)} />
            )}
          </Field>
          <DisabledReason reasons={[
            !cur && t('auth.currentPassword'),
            !pw && t('auth.password'),
            !pw2 && t('auth.confirmPassword'),
          ].filter(Boolean) as string[]} />
          <div className="flex gap-2 pt-1">
            <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={busy || !cur || !pw || !pw2 || tooShort || mismatch}>
              {busy ? t('auth.changing') : t('auth.changePassword')}
            </button>
            <Link to="/account" className="gs-btn">{t('common.cancel')}</Link>
          </div>
        </form>
      </div>
    </div>
  );
}
