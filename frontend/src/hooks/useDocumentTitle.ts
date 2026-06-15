import { useEffect } from 'react';

const SUFFIX = 'GShare';

/** Per-screen document title, `<name> · GShare`. `null` keeps the current title. */
export function useDocumentTitle(title: string | null | undefined) {
  useEffect(() => {
    if (!title) return;
    const previous = document.title;
    document.title = `${title} · ${SUFFIX}`;
    return () => { document.title = previous; };
  }, [title]);
}
