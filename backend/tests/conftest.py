"""pytest fixtures.

Testcontainers Postgres/Redis are heavy and unavailable in CI/sandbox. For the domain-logic suite
we bind an in-memory async SQLite engine instead: the CreditEngine / SchedulerService logic under
test is pure SQL + Python (FOR UPDATE serialization is a Postgres runtime concern, not a schema
one), so SQLite is sufficient to exercise idempotency, differencing, and gate ordering.
Postgres-only column types (JSONB) and partial-UNIQUE indexes are adapted/ignored for SQLite below
so ``Base.metadata.create_all`` succeeds.

The cluster handoff is injected as a Fake port so tests can assert the desired payload without a
real K8s cluster. Pod builder/reconcile unit tests live in the operator.
"""
from __future__ import annotations

import os

# Inject a test HS256 signing key before the settings singleton is built, which the app.* imports
# below trigger. Production code has no default (config.USER_JWT_SECRET is ""), so the tests pin one
# here.
os.environ.setdefault("GSHARE_USER_JWT_SECRET", "test-secret-not-for-prod")

import pytest
import pytest_asyncio
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base


# SQLite has no JSONB; render it as plain JSON (text-backed) so create_all works off the same
# models the production Postgres schema uses. This is a test-only dialect shim.
@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    """AsyncSession bound to an in-memory SQLite.

    A single connection-pooled in-memory engine; schema created via run_sync(create_all). Yields one
    session per test (rolled back / disposed at teardown). Postgres partial-UNIQUE indexes carry a
    ``postgresql_where`` that SQLite simply ignores, which is fine for these logic tests.
    """
    # StaticPool shares one connection across every session, so the in-memory database survives
    # between them and the connection stays consistent after an exception inside begin(). With the
    # default pool the async adapter can be left in a broken state.
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    session = sessionmaker()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


class FakeHandoff:
    """Captures the last desired payload instead of applying a real CR.

    Records the (sess, req) it was handed and the serialized GShareSession spec so scheduler tests
    can assert the desired-state handoff happened with the right shape — without a live cluster.
    """

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.last_sess = None
        self.last_req = None
        self.last_spec: dict | None = None

    async def apply_desired(self, sess, req) -> None:
        from app.cluster.crd import GShareSessionCRD

        # Pure serialization (no db / no cluster) — mirrors Handoff.apply_desired's spec build.
        self.last_sess = sess
        self.last_req = req
        self.last_spec = GShareSessionCRD().to_session_spec(sess, req)
        self.calls.append((sess, req, self.last_spec))


@pytest.fixture
def fake_handoff() -> FakeHandoff:
    return FakeHandoff()


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    """Replace get_redis's singleton with an in-memory FakeRedis so the tests need no real Redis.

    SchedulerService.create_session uses Redis for the idempotency key (SET NX) and the queue (ZADD
    and ZPOPMAX). Injecting fakeredis instead of a testcontainers Redis keeps the unit tests
    self-contained on a laptop or in a single container.
    """
    from fakeredis.aioredis import FakeRedis

    import app.core.redis as redis_mod

    client = FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis_mod, "_pool", client, raising=False)
    yield client
    monkeypatch.setattr(redis_mod, "_pool", None, raising=False)
