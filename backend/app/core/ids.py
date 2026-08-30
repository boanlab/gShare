"""Prefixed-ULID id generator.

IDs are ``<prefix>_<ULID>`` e.g. ``ses_01J...``, ``usr_...``, ``clu_...``, ``cnx_...``.
"""
from __future__ import annotations

from ulid import ULID

# Canonical entity prefixes. Extend as needed.
PREFIXES = {
    "ireply": "irp",
    "inquiry": "inq",
    "notice": "ntc",
    "user": "usr", "org": "org", "group": "grp", "membership": "mbr",
    "wallet": "wal", "transaction": "txn", "topup": "top",
    "offering": "off", "preset": "pst", "policy": "pol", "image": "img", "build": "bld",
    "session": "ses", "allocation": "alc", "queue": "que",
    "cluster": "clu", "node": "nod", "device": "dev", "pool": "npl", "pool_grant": "pgr",
    "volume": "vol", "folder": "fld", "snapshot": "snp", "permission": "vpm",
    "budget": "bdg", "alert": "alr",
    "notification": "ntf", "connection": "cnx",
    "webhook": "wbh", "delivery": "wbd", "audit": "aud",
    "healthevent": "nhe", "maintenance": "mnt", "checkpoint": "ckp",
    "quotarequest": "qrq",
    "allocrequest": "car",
    "sessionevent": "sev",
    "resourcerequest": "rrq",
}


def new(kind: str) -> str:
    """Return a fresh prefixed ULID for ``kind`` (see:data:`PREFIXES`)."""
    prefix = PREFIXES.get(kind, kind)
    return f"{prefix}_{ULID()}"
