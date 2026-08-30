// Container image references, as typed by a user: a light client-side shape check and a readable
// default name. The backend re-validates every reference — this only keeps obvious typos out of a
// request.

// [host[:port]/]path[:tag][@digest]; mirrors the backend's _IMAGE_REF_RE.
const SEG = '[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*';
const IMAGE_REF = new RegExp(
  `^(?:${SEG}(?::[0-9]+)?/)?${SEG}(?:/${SEG})*(?::[A-Za-z0-9_][A-Za-z0-9._-]{0,127})?(?:@[A-Za-z0-9]+:[A-Fa-f0-9]{32,})?$`,
);

export function looksLikeImageRef(ref: string): boolean {
  const v = ref.trim();
  return v.length > 0 && v.length <= 512 && IMAGE_REF.test(v);
}

/** A display name for a reference the user did not name: the last path segment plus its tag. */
export function deriveImageName(ref: string): string {
  const v = ref.trim().split('@')[0];
  if (!v) return '';
  return (v.split('/').pop() ?? v).slice(0, 120);
}
