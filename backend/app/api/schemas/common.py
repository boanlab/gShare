"""Shared Pydantic v2 building blocks."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PageMeta(BaseModel):
    """Standard pagination envelope (page/size/total/total_pages)."""

    page: int
    size: int
    total: int
    total_pages: int


class PageMetaNoPages(BaseModel):
    """Pagination envelope without ``total_pages`` (queue list)."""

    page: int
    size: int
    total: int
