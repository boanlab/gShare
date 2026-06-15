"""Regression tests for cross-tenant volume read/enumeration access control.

Covers the tenant gate added to volumes_router: list_volumes must only return volumes the caller
owns / is permitted on / administers the group of (super_admin sees all), and get_volume /
list_folders / create_folder must raise Forbidden for non-owning, non-permitted members.
"""
from __future__ import annotations

import pytest

from app.api.volumes_router import (
    create_folder,
    get_volume,
    list_folders,
    list_volumes,
)
from app.auth.rbac import Principal
from app.core.errors import Forbidden
from app.db.models import StorageFolder, StorageVolume, User, VolumePermission


class _Page:
    """Minimal stand-in for the Pagination dependency."""

    offset = 0
    size = 100


async def _seed(db):
    """Owner 'alice' personal volume + group 'eng' volume + a shared volume; outsider 'mallory'."""
    db.add_all(
        [
            User(id="alice", email="alice@example.com", name="Alice"),
            User(id="mallory", email="mallory@example.com", name="Mallory"),
            User(id="bob", email="bob@example.com", name="Bob"),
            StorageVolume(
                id="vol_alice", scope="user", scope_id="alice", type="home",
                access_mode="RWO", owner_id="alice", quota_gb=10,
            ),
            StorageVolume(
                id="vol_eng", scope="group", scope_id="eng", type="group",
                access_mode="RWX", owner_id="eng", quota_gb=50,
            ),
            StorageVolume(
                id="vol_shared", scope="user", scope_id="bob", type="home",
                access_mode="RWO", owner_id="bob", quota_gb=20,
            ),
            # bob shares vol_shared with mallory (explicit VolumePermission).
            VolumePermission(id="vpm1", volume_id="vol_shared", user_id="mallory", role="ro"),
            StorageFolder(id="fld1", volume_id="vol_alice", path="/data", size_bytes=0),
        ]
    )
    await db.commit()


def _principal(user_id: str, *, super_admin: bool = False, memberships: dict | None = None) -> Principal:
    return Principal(
        user_id=user_id,
        global_roles={"super_admin"} if super_admin else set(),
        memberships=memberships or {},
    )


@pytest.mark.asyncio
async def test_member_cannot_read_others_volume(db):
    await _seed(db)
    mallory = _principal("mallory")
    # mallory has no ownership / permission / admin scope on alice's volume.
    with pytest.raises(Forbidden):
        await get_volume("vol_alice", principal=mallory, db=db)
    with pytest.raises(Forbidden):
        await list_folders("vol_alice", page=_Page(), principal=mallory, db=db)
    with pytest.raises(Forbidden):
        await create_folder("vol_alice", {"path": "/evil"}, principal=mallory, db=db)


@pytest.mark.asyncio
async def test_member_cannot_enumerate_others_volumes(db):
    await _seed(db)
    mallory = _principal("mallory")
    rows = await list_volumes(scope=None, scope_id=None, type=None, access_mode=None, page=_Page(), principal=mallory, db=db)
    ids = {v.id for v in rows}
    # Only the volume explicitly shared with mallory is visible; alice's & eng's are hidden.
    assert ids == {"vol_shared"}


@pytest.mark.asyncio
async def test_owner_can_read_own_volume(db):
    await _seed(db)
    alice = _principal("alice")
    vol = await get_volume("vol_alice", principal=alice, db=db)
    assert vol.id == "vol_alice"
    folders = await list_folders("vol_alice", page=_Page(), principal=alice, db=db)
    assert folders["data"][0]["path"] == "/data"
    created = await create_folder("vol_alice", {"path": "/new"}, principal=alice, db=db)
    assert created["path"] == "/new"
    ids = {v.id for v in await list_volumes(scope=None, scope_id=None, type=None, access_mode=None, page=_Page(), principal=alice, db=db)}
    assert ids == {"vol_alice"}


@pytest.mark.asyncio
async def test_group_admin_can_access_group_volume(db):
    await _seed(db)
    admin = _principal("carol", memberships={"eng": "group_admin"})
    vol = await get_volume("vol_eng", principal=admin, db=db)
    assert vol.id == "vol_eng"
    ids = {v.id for v in await list_volumes(scope=None, scope_id=None, type=None, access_mode=None, page=_Page(), principal=admin, db=db)}
    assert ids == {"vol_eng"}


@pytest.mark.asyncio
async def test_super_admin_sees_all(db):
    await _seed(db)
    root = _principal("root", super_admin=True)
    await get_volume("vol_alice", principal=root, db=db)
    ids = {v.id for v in await list_volumes(scope=None, scope_id=None, type=None, access_mode=None, page=_Page(), principal=root, db=db)}
    assert ids == {"vol_alice", "vol_eng", "vol_shared"}
