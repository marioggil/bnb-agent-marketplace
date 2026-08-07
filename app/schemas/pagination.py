"""Pagination envelope shared by every listing endpoint."""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Generic page envelope.

    `items` is the slice for the current page; `total` is the count across
    all pages so the UI can render "page X of Y". `page` and `page_size`
    echo the requested values so the client can verify what it got.
    """

    items: list[T]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1)
