"""Volume mounting spec: PVC access mode from the volume's class, quota carried for
provisioning, readOnly as the per-session mount intent."""
from __future__ import annotations

import pytest

from app.cluster.crd import GShareSessionCRD, _to_crd_spec
from app.core import ids
from app.db.models import StorageVolume


def _spec_with(vols):
    return {"cluster_id": "c", "session_id": "s", "resource_class": "gpu", "volumes": vols}


def test_serializer_emits_access_mode_readonly_and_size():
    out = _to_crd_spec(_spec_with([
        {"volume_id": "vol_A", "mount_path": "/data", "mode": "rw",
         "access_mode": "RWO", "size_gb": 5},
        {"volume_id": "vol_B", "mount_path": "/shared", "mode": "ro",
         "access_mode": "RWX", "size_gb": 100},
    ]))
    a, b = out["volumes"]
    assert a == {"name": "vol_A", "mountPath": "/data", "mode": "ReadWriteOnce", "sizeGb": 5}
    assert b["mode"] == "ReadWriteMany" and b["readOnly"] is True and b["sizeGb"] == 100


def test_serializer_fallback_without_enrichment():
    out = _to_crd_spec(_spec_with([
        {"volume_id": "vol_A", "mount_path": "/data", "mode": "rw"},
        {"volume_id": "vol_B", "mount_path": "/ro", "mode": "ro"},
    ]))
    a, b = out["volumes"]
    assert a["mode"] == "ReadWriteOnce" and "readOnly" not in a
    assert b["mode"] == "ReadOnlyMany" and b["readOnly"] is True


@pytest.mark.asyncio
async def test_enrich_volumes_reads_storage_volume(db):
    vol = StorageVolume(
        id=ids.new("volume"), scope="user", scope_id=ids.new("user"), type="scratch",
        name="d", access_mode="RWO", quota_gb=7,
    )
    async with db.begin():
        db.add(vol)
    crd = GShareSessionCRD(db)
    out = await crd._enrich_volumes([
        {"volume_id": vol.id, "mount_path": "/data", "mode": "rw"},
        {"volume_id": "vol_missing", "mount_path": "/x", "mode": "rw"},
    ])
    assert out[0]["access_mode"] == "RWO" and out[0]["size_gb"] == 7
    assert "access_mode" not in out[1]   # unknown volume keeps the fallback path
