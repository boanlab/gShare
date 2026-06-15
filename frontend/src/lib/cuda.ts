// CUDA version parsing and comparison, mirroring the backend's app/core/cuda.py.
// An offering's min_cuda is compared against an image's cuda_version to hide incompatible images.

function parseCuda(v?: string | null): [number, number] | null {
  if (!v) return null;
  const m = /^(\d+)(?:\.(\d+))?/.exec(String(v).trim());
  if (!m) return null;
  return [Number(m[1]), Number(m[2] ?? 0)];
}

// True when the image's CUDA meets the offering's minimum. Permissive: if either side is unset,
// the pair passes.
export function cudaCompatible(imageCuda?: string | null, minCuda?: string | null): boolean {
  const iv = parseCuda(imageCuda);
  const mv = parseCuda(minCuda);
  if (!iv || !mv) return true;
  return iv[0] > mv[0] || (iv[0] === mv[0] && iv[1] >= mv[1]);
}
