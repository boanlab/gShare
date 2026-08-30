"""Console image builds: create hands off a CR (mocked), the internal callback drives status and
mints a PRIVATE Image row once, the wizard listing shows own private images, logs return the tail."""
from __future__ import annotations

import pytest

from app.api.images_router import BuildCreate, build_logs, create_build, list_images
from app.auth.rbac import Principal
from app.cluster.crd import GShareImageBuildCRD
from app.core import ids
from app.db.models import Cluster, Image, ImageBuild, Membership, Organization, Project, User
from app.internal.imagebuild_status_router import BuildStatusEvent, report_build_status


def _p(uid, *, super_admin=False, memberships=None):
    return Principal(
        user_id=uid,
        global_role="super_admin" if super_admin else None,
        global_roles=["super_admin"] if super_admin else [],
        memberships=memberships or {},
    )


async def _seed(db):
    org = Organization(id=ids.new("org"), name="O")
    grp = Project(id=ids.new("project"), org_id=org.id, name="G")
    user = User(id=ids.new("user"), email="b@x.kr", name="B")
    clu = Cluster(id=ids.new("cluster"), name="c1", role="primary", api_server="https://x",
                  runtime="containerd", status="ready", kubeconfig_secret_ref="sec/kc")
    db.add_all([org, grp, user, clu,
                Membership(id=ids.new("membership"), user_id=user.id, group_id=grp.id, role="member")])
    await db.commit()
    return grp, user, clu


@pytest.fixture()
def no_handoff(monkeypatch):
    applied = []

    async def fake_apply(self, cluster_id, build):
        applied.append((cluster_id, build.id))

    monkeypatch.setattr(GShareImageBuildCRD, "apply_build", fake_apply)
    return applied


@pytest.mark.asyncio
async def test_member_build_lifecycle(db, no_handoff):
    grp, user, clu = await _seed(db)
    p = _p(user.id, memberships={grp.id: "member"})
    out = await create_build(
        BuildCreate(group_id=grp.id, name="My_Torch", source="dockerfile",
                    dockerfile="FROM python:3.12\nRUN pip install torch"),
        principal=p, db=db,
    )
    assert out["status"] == "queued" and no_handoff == [(clu.id, out["id"])]
    assert out["image_ref"].endswith("/my_torch:latest") and user.id.lower() in out["image_ref"]

    # a second concurrent build is refused
    from app.core.errors import DomainError
    with pytest.raises(DomainError):
        await create_build(BuildCreate(group_id=grp.id, name="two", source="dockerfile",
                                       dockerfile="FROM alpine"), principal=p, db=db)

    # operator: running -> succeeded (Image row minted once, private, owned)
    await report_build_status(out["id"], BuildStatusEvent(phase="running", log_tail="step 1/2"),
                              _claims={}, db=db)
    await report_build_status(
        out["id"], BuildStatusEvent(phase="succeeded", image_ref=out["image_ref"], log_tail="done"),
        _claims={}, db=db)
    await report_build_status(  # retried callback must not regress or duplicate
        out["id"], BuildStatusEvent(phase="succeeded", image_ref=out["image_ref"]),
        _claims={}, db=db)
    build = await db.get(ImageBuild, out["id"])
    assert build.status == "succeeded" and build.started_at and build.finished_at
    img = await db.get(Image, build.image_id)
    assert img.owner_user_id == user.id and img.public is False and img.registry == out["image_ref"]

    # wizard listing (public=true) includes the caller's own private image, hides it from others
    from app.api.deps import Pagination
    pg = Pagination(page=1, size=50)
    mine = await list_images(pagination=pg, kind=None, q=None, tag=None, public=True,
                             mine=False, principal=p, db=db)
    assert any(i["id"] == img.id for i in mine["data"])
    other = _p(ids.new("user"), memberships={grp.id: "member"})
    theirs = await list_images(pagination=pg, kind=None, q=None, tag=None, public=True,
                               mine=False, principal=other, db=db)
    assert not any(i["id"] == img.id for i in theirs["data"])

    logs = await build_logs(out["id"], tail=10, principal=p, db=db)
    assert logs["lines"] == ["done"]


@pytest.mark.asyncio
async def test_failed_handoff_is_recorded(db, monkeypatch):
    grp, user, clu = await _seed(db)

    async def boom(self, cluster_id, build):
        raise RuntimeError("kubeconfig missing")

    monkeypatch.setattr(GShareImageBuildCRD, "apply_build", boom)
    from app.core.errors import DomainError
    with pytest.raises(DomainError):
        await create_build(BuildCreate(group_id=grp.id, name="x", source="dockerfile",
                                       dockerfile="FROM alpine"),
                           principal=_p(user.id, memberships={grp.id: "member"}), db=db)
    row = (await db.execute(
        __import__("sqlalchemy").select(ImageBuild).where(ImageBuild.owner_user_id == user.id)
    )).scalar_one()
    assert row.status == "failed" and "handoff failed" in (row.error or "")
