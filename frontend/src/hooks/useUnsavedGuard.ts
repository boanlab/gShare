import { useEffect, useRef } from 'react';
import { useBlocker } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

/**
 * Prompt before leaving a form with unsaved input: the router blocker covers in-app navigation,
 * `beforeunload` covers a closed tab. Pass `dirty: false` while submitting.
 */
export function useUnsavedGuard(dirty: boolean, dirtyNow?: () => boolean) {
  const { t } = useTranslation();

  // The blocker predicate runs at navigation time but closes over the last committed render, so a
  // submit handler that clears the guard and navigates in the same tick would still be blocked.
  // Reading through a ref (or the caller's dirtyNow) keeps the decision current.
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const blocker = useBlocker(({ currentLocation, nextLocation }) =>
    (dirtyNow ? dirtyNow() : dirtyRef.current) && currentLocation.pathname !== nextLocation.pathname);

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
  // Dirty lives in a REF only, never in state: the first onInput used to setState, and that
  // re-render landed BETWEEN a <select>'s native `input` and `change` events — React re-asserted
  // the old controlled value in the gap, so `change` read the stale value and the user's first
  // pick silently did not take (every form's first interaction, selects especially). Nothing
  // renders from the flag: the router blocker reads it through dirtyNow at decision time and the
  // beforeunload handler reads it at event time.
  const dirtyRef = useRef(false);
  const busyRef = useRef(busy);
  busyRef.current = busy;
  useUnsavedGuard(false, () => dirtyRef.current && !busyRef.current);
  useEffect(() => {
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (dirtyRef.current && !busyRef.current) { e.preventDefault(); e.returnValue = ''; }
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, []);
  return {
    clear: () => { dirtyRef.current = false; },
    props: { ...unsavedGuardProps, onInput: () => { dirtyRef.current = true; } },
  };
}
