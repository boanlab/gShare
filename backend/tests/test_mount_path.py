"""Mount-path validation on VolumeMountSpec (batch 4-4).

The path lands verbatim in the pod spec, so it must be an absolute, plain-ASCII path with
[A-Za-z0-9._-] segments — no traversal, spaces, or non-ASCII — and may not shadow system paths.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.schemas.session import VolumeMountSpec


def _spec(path: str) -> VolumeMountSpec:
    return VolumeMountSpec(volume_id="vol_x", mount_path=path)


@pytest.mark.parametrize("path", [
    "/data",
    "/data/my-set_01",
    "/workspace/proj.v2",
    "/mnt/a/b/c",
])
def test_valid_paths(path):
    assert _spec(path).mount_path == path


@pytest.mark.parametrize("path", [
    "data",                # relative
    "",                    # empty
    "/",                   # root alone
    "/data/../etc",        # traversal
    "/data/.",             # dot segment
    "/한글",                # non-ASCII
    "/has space",          # space
    "/data//x",            # empty segment
    "/data/" + "x" * 65,   # segment too long
    "/" + "a/" * 130 + "b",  # total too long
])
def test_invalid_paths(path):
    with pytest.raises(ValidationError):
        _spec(path)


@pytest.mark.parametrize("path", ["/etc", "/etc/passwd", "/usr/lib", "/proc/self", "/dev/shm", "/bin"])
def test_reserved_system_paths(path):
    with pytest.raises(ValidationError):
        _spec(path)
