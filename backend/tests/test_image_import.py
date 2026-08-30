"""Image imports are an administrator's tool: the catalogue is curated, not user-supplied.

Members used to be able to register a public registry reference as a private image of their own;
that path is closed for security — everything a session can run comes from the admin-curated
catalogue. These tests pin the new contract: members are refused outright, admin imports stay
shared, deduplicated, and validated.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.api.images_router import ImageImport, import_image
from app.auth.rbac import Principal
from app.core import ids
from app.core.errors import DomainError, Forbidden, NotImplementedFeature
from app.db.models import Image, User

REF = "pytorch/pytorch:2.4.0-cuda12.4-cudnn9-runtime"


def _p(uid: str, *, super_admin: bool = False) -> Principal:
    return Principal(
        user_id=uid,
        global_role="super_admin" if super_admin else None,
        global_roles={"super_admin"} if super_admin else set(),
        memberships={},
    )


def _body(source: str = REF, **kw) -> ImageImport:
    kw.setdefault("source_type", "registry")
    kw.setdefault("name", "PyTorch 2.4")
    kw.setdefault("kind", "container")
    return ImageImport(source=source, **kw)


async def _user(db, email: str) -> str:
    u = User(id=ids.new("user"), email=email, name=email)
    db.add(u)
    await db.commit()
    return u.id


@pytest.mark.asyncio
async def test_member_import_is_forbidden(db):
    """The old member path minted a private owned row; it now refuses outright."""
    uid = await _user(db, "m1@x.kr")
    with pytest.raises(Forbidden):
        await import_image(_body(), principal=_p(uid), db=db)
    total = await db.scalar(select(func.count()).select_from(Image).where(Image.registry == REF))
    assert total == 0


@pytest.mark.asyncio
async def test_member_is_refused_even_for_an_existing_shared_ref(db):
    """Idempotent hand-back of the shared row was a member convenience; it went with the path."""
    uid = await _user(db, "m2@x.kr")
    db.add(Image(id=ids.new("image"), name="Shared", registry=REF, kind="container", tags={},
                 import_status="ready", public=True, owner_user_id=None))
    await db.commit()
    with pytest.raises(Forbidden):
        await import_image(_body(), principal=_p(uid), db=db)


@pytest.mark.asyncio
async def test_super_admin_import_stays_shared_and_dedups(db):
    uid = await _user(db, "root2@x.kr")
    p = _p(uid, super_admin=True)
    out = await import_image(_body(), principal=p, db=db)
    assert out["owner_user_id"] is None and out["public"] is True and out["existing"] is False
    with pytest.raises(DomainError) as exc:
        await import_image(_body(), principal=p, db=db)
    assert exc.value.code == "conflict"


@pytest.mark.asyncio
async def test_registry_auth_is_still_refused(db):
    uid = await _user(db, "root3@x.kr")
    with pytest.raises(NotImplementedFeature):
        await import_image(_body(registry_auth={"username": "u", "password": "p"}),
                           principal=_p(uid, super_admin=True), db=db)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", "   ", "busy box:1.36", "busybox:1.36; rm -rf /",
                                 "-badstart/x", "acme//x:1", "acme/x:"])
async def test_malformed_references_are_rejected(db, bad):
    uid = await _user(db, f"bad{abs(hash(bad)) % 997}@x.kr")
    with pytest.raises(DomainError) as exc:
        await import_image(_body(bad), principal=_p(uid, super_admin=True), db=db)
    assert exc.value.code == "validation_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("good", ["busybox:1.36", REF, "10.10.0.191:5001/gshare/x:ux5-1",
                                  "nvcr.io/nvidia/pytorch:24.05-py3", "alpine",
                                  "alpine@sha256:" + "a" * 64])
async def test_well_formed_references_are_accepted(db, good):
    uid = await _user(db, f"ok{abs(hash(good)) % 997}@x.kr")
    out = await import_image(_body(good), principal=_p(uid, super_admin=True), db=db)
    assert out["registry"] == good
