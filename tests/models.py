"""Test-only tenant-scoped model to exercise the shared base classes."""

from sqlalchemy.orm import Mapped

from shared.db import TenantScopedBase, TimestampMixin


class Gadget(TimestampMixin, TenantScopedBase):
    __tablename__ = "test_gadgets"

    name: Mapped[str]
