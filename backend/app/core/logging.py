"""Structured logging + X-Request-Id correlation."""
from __future__ import annotations

import logging

_DEF_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logging once at process start."""
    logging.basicConfig(level=level, format=_DEF_FORMAT)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
