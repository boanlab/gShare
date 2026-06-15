"""Parsing and comparison of CUDA versions.

GPU compatibility is determined by the architecture — the compute capability — which translates into
the minimum CUDA toolkit version that supports the card. Offerings carry min_cuda per GPU model and
images carry cuda_version; the pair is compatible only when
image.cuda_version >= offering.min_cuda.
"""
from __future__ import annotations


def parse_cuda(v: str | None) -> tuple[int, int] | None:
    """Parse '12.8' to (12, 8) and '11' to (11, 0). Empty or malformed input yields None."""
    if not v:
        return None
    try:
        parts = str(v).strip().split(".")
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 and parts[1] != "" else 0
        return (major, minor)
    except (ValueError, IndexError):
        return None


def cuda_compatible(image_cuda: str | None, min_cuda: str | None) -> bool:
    """True when the image's CUDA version meets or exceeds the offering's minimum.

    Deliberately permissive: if either side is unset or malformed the pair passes, so an unknown
    constraint never hides an image.
    """
    iv = parse_cuda(image_cuda)
    mv = parse_cuda(min_cuda)
    if iv is None or mv is None:
        return True
    return iv >= mv
