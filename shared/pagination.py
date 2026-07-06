"""Pagination primitives (interfaces doc §2.2).

Limits are enforced by the constructor, not by trusting the caller —
mandatory pagination with a hard cap is a DoS control (threat model V9).
"""

from collections.abc import Sequence
from dataclasses import dataclass

MAX_PAGE_LIMIT = 200


@dataclass(frozen=True, slots=True)
class Page:
    limit: int = 50
    offset: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= MAX_PAGE_LIMIT:
            raise ValueError(f"Page.limit must be within 1..{MAX_PAGE_LIMIT}")
        if self.offset < 0:
            raise ValueError("Page.offset must be >= 0")


@dataclass(frozen=True, slots=True)
class PageResult[T]:
    items: Sequence[T]
    total: int
    limit: int
    offset: int
