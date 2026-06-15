#!/usr/bin/env python3
"""O1 — ledger transaction latency microbenchmark (control-plane overhead).

Times a representative credit-ledger transaction round-trip (BEGIN; SELECT wallet FOR
UPDATE; UPDATE; ROLLBACK) from the API process to Postgres. Benign — rolls back, no state
change. Run inside the api container so DATABASE_URL + SQLAlchemy are available:

  docker exec gshare-gshare-api-1 python - < docs/paper/eval/o1/ledger_bench.py

Reports P50/P99 → O1 overhead numbers (manuscript §Evaluation).
"""
import asyncio
import os
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def main():
    url = os.environ.get("DATABASE_URL") or os.environ.get("GSHARE_DATABASE_URL")
    eng = create_async_engine(url)
    async with eng.connect() as c:  # warm the pool
        await c.execute(text("select 1"))
    lat = []
    for _ in range(2000):
        t = time.perf_counter()
        try:
            async with eng.begin() as c:
                wid = (await c.execute(
                    text("select id from credit_wallet order by id limit 1 for update"))).scalar()
                await c.execute(text("update credit_wallet set balance=balance where id=:i"), {"i": wid})
                raise RuntimeError("rollback")  # begin() rolls back on exception
        except RuntimeError:
            pass
        lat.append((time.perf_counter() - t) * 1e3)
    lat.sort()
    print(f"ledger_txn(FOR UPDATE+update, rollback)  "
          f"P50={lat[len(lat)//2]:.3f}ms  P99={lat[int(len(lat)*0.99)]:.3f}ms  n={len(lat)}")


asyncio.run(main())
