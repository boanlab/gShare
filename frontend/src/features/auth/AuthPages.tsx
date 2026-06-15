import { useState } from 'react';
import { useNavigate, useLocation, Link, Navigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/auth/authStore';
import { useUiStore } from '@/store/uiStore';
import { LanguageToggle } from '@/components/LanguageToggle';
import { Field, DisabledReason } from '@/components/Field';
import { useDocumentTitle } from '@/hooks/useDocumentTitle';

/** Shared shell for the signed-out screens: a landmark, a heading, and the language control. */
function AuthShell({ title, children }: { title: string; children: React.ReactNode }) {
  useDocumentTitle(title);
  return (
    <main className="min-h-full grid place-items-center bg-bg p-4">
      <div className="w-full max-w-[380px]">{children}</div>
    </main>
  );
}

// Sign-in, with an email and a password.
export function Login() {
  const navigate = useNavigate();
  const loc = useLocation();
  const { t } = useTranslation();
  const pushToast = useUiStore((s) => s.pushToast);
  const loginPassword = useAuthStore((s) => s.loginPassword);
  const [email, setEmail] = useState('');
  const [pw, setPw] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [emailTouched, setEmailTouched] = useState(false);
  const emailMalformed = emailTouched && email.trim().length > 0 && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim());
  const returnUrl = (loc.state as { returnUrl?: string } | null)?.returnUrl ?? '/';

  async function handlePassword(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await loginPassword(email, pw);
      // A first sign-in — an account an administrator registered, or the bootstrap account —
      // has to change its password before going anywhere else.
      const mustChange = useAuthStore.getState().claims.must_change_password;
      navigate(mustChange ? '/change-password' : returnUrl, { replace: true });
    } catch {
      // Inline as well as a toast: the message has to outlast the toast.
      setError(t('auth.invalidCredentials'));
      pushToast('error', t('auth.invalidCredentials'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell title={t('auth.signIn')}>
      <form className="gs-card space-y-3" onSubmit={handlePassword} noValidate>
        <div className="flex items-center gap-2 font-extrabold text-lg mb-2">
          <span className="w-[22px] h-[22px] rounded-md bg-primary inline-block" aria-hidden="true" />
          <h1 className="text-lg font-extrabold">GShare</h1>
          <span className="ml-auto"><LanguageToggle /></span>
        </div>
        {error && <p role="alert" className="text-danger text-[12.5px]">{error}</p>}
        <Field label={t('auth.email')} required error={emailMalformed ? t('auth.emailMalformed') : null}>
          {(ids) => (
            <input
              {...ids}
              className="gs-input w-full"
              type="email"
              inputMode="email"
              autoComplete="username"
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              onBlur={() => setEmailTouched(true)}
            />
          )}
        </Field>
        <Field label={t('auth.password')} required>
          {(ids) => (
            <input
              {...ids}
              className="gs-input w-full"
              type="password"
              autoComplete="current-password"
              value={pw}
              onChange={(e) => setPw(e.target.value)}
            />
          )}
        </Field>
        <DisabledReason reasons={[!email && t('auth.email'), !pw && t('auth.password')].filter(Boolean) as string[]} />
        <button type="submit" className="gs-btn gs-btn-primary w-full justify-center disabled:opacity-50" disabled={busy || !email || !pw || emailMalformed}>
          {busy ? t('auth.signingIn') : t('auth.signIn')}
        </button>
      </form>
    </AuthShell>
  );
}

// Password change: forced at first sign-in (must_change), or requested by the user themselves.
export function ChangePassword() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const isAuthed = useAuthStore((s) => s.isAuthed);
  const mustChange = useAuthStore((s) => s.claims.must_change_password);
  const changePassword = useAuthStore((s) => s.changePassword);
  const pushToast = useUiStore((s) => s.pushToast);
  const [cur, setCur] = useState('');
  const [pw, setPw] = useState('');
  const [pw2, setPw2] = useState('');
  const [busy, setBusy] = useState(false);
  const [touched, setTouched] = useState(false);

  // Validated as typed.
  const tooShort = pw.length > 0 && pw.length < 8;
  const mismatch = pw2.length > 0 && pw !== pw2;

  if (!isAuthed) return <Navigate to="/login" replace />;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (pw.length < 8 || pw !== pw2) return;
    setBusy(true);
    try {
      await changePassword(pw, mustChange ? undefined : cur);
      pushToast('success', t('auth.changed'));
      navigate('/', { replace: true });
    } catch {
      pushToast('error', mustChange ? t('auth.changeFailed') : t('auth.wrongCurrent'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell title={t('auth.changePassword')}>
      <form className="gs-card space-y-3" onSubmit={submit} noValidate>
        <div className="flex items-center gap-2 mb-1">
          <span className="w-[22px] h-[22px] rounded-md bg-primary inline-block" aria-hidden="true" />
          <h1 className="text-lg font-extrabold">{t('auth.changePassword')}</h1>
          <span className="ml-auto"><LanguageToggle /></span>
        </div>
        {mustChange && <p className="text-warn text-[12px]">{t('auth.firstLogin')}</p>}
        {!mustChange && (
          <Field label={t('auth.currentPassword')} required>
            {(ids) => (
              <input {...ids} className="gs-input w-full" type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} />
            )}
          </Field>
        )}
        <Field
          label={t('auth.newPassword')}
          required
          hint={t('auth.passwordRule')}
          error={(touched || pw.length > 0) && tooShort ? t('auth.tooShort') : null}
        >
          {(ids) => (
            <input {...ids} className="gs-input w-full" type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} />
          )}
        </Field>
        <Field label={t('auth.confirmPassword')} required error={mismatch ? t('auth.mismatch') : null}>
          {(ids) => (
            <input {...ids} className="gs-input w-full" type="password" autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} />
          )}
        </Field>
        <DisabledReason reasons={[!pw && t('auth.newPassword'), !pw2 && t('auth.confirmPassword')].filter(Boolean) as string[]} />
        <button type="submit" className="gs-btn gs-btn-primary w-full justify-center disabled:opacity-50" disabled={busy || !pw || !pw2 || tooShort || mismatch}>
          {busy ? t('auth.changing') : t('auth.changePassword')}
        </button>
      </form>
    </AuthShell>
  );
}

function ErrorScreen({ code, title, hint }: { code: string; title: string; hint?: string }) {
  const { t } = useTranslation();
  useDocumentTitle(title);
  return (
    <main className="min-h-full grid place-items-center text-center p-4">
      <div>
        <div aria-hidden="true" className="text-5xl font-extrabold text-muted">{code}</div>
        <h1 className="text-xl font-bold mt-2">{title}</h1>
        {hint && <p className="text-muted mt-1">{hint}</p>}
        <div className="mt-4 flex items-center justify-center gap-2 flex-wrap">
          <Link to="/" className="gs-btn gs-btn-primary">{t('auth.backToDashboard')}</Link>
          <button type="button" className="gs-btn" onClick={() => window.history.back()}>← {t('common.back')}</button>
          <LanguageToggle />
        </div>
      </div>
    </main>
  );
}

export function Forbidden() {
  const { t } = useTranslation();
  return <ErrorScreen code="403" title={t('auth.forbiddenTitle')} hint={t('auth.forbiddenHint')} />;
}

export function NotFound() {
  const { t } = useTranslation();
  return <ErrorScreen code="404" title={t('auth.notFoundTitle')} hint={t('auth.notFoundHint')} />;
}

export function SystemError() {
  const { t } = useTranslation();
  return <ErrorScreen code="500" title={t('auth.errorTitle')} hint={t('auth.errorHint')} />;
}
