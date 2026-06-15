import { useEffect, useState } from 'react';
import { useBlocker } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

/**
 * Prompt before leaving a form with unsaved input: the router blocker covers in-app navigation,
 * `beforeunload` covers a closed tab. Pass `dirty: false` while submitting.
 */
export function useUnsavedGuard(dirty: boolean) {
  const { t } = useTranslation();

  const blocker = useBlocker(({ currentLocation, nextLocation }) =>
    dirty && currentLocation.pathname !== nextLocation.pathname);

  useEffect(() => {
    if (blocker.state !== 'blocked') return;
    // Native dialog: the only prompt the browser honours for a navigation decision.
    if (window.confirm(t('common.unsavedWarning'))) blocker.proceed();
    else blocker.reset();
  }, [blocker, t]);

  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [dirty]);
}

/** Marker attribute for the guard. Spread onto the `<form>`. */
export const unsavedGuardProps = { 'data-unsaved-guard': true } as const;

/**
 * Guard for a form with no single value to diff against: any input inside it marks it dirty.
 *
 *   const guard = useFormGuard(saving);
 *   <form {...guard.props} onSubmit={…}>
 */
export function useFormGuard(busy = false) {
  const [dirty, setDirty] = useState(false);
  useUnsavedGuard(dirty && !busy);
  return {
    dirty,
    clear: () => setDirty(false),
    props: { ...unsavedGuardProps, onInput: () => setDirty(true) },
  };
}
