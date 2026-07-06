"""Money: integer minor units + ISO 4217 currency code. No floats anywhere.

Currency exponents live in the ``currencies`` table (UZS=0, USD=2). The
in-memory CurrencyRegistry loads them once at startup so validation is
synchronous (no await, no session) — interfaces doc §2.5.
"""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.errors import NotFoundError


@dataclass(frozen=True, slots=True)
class Money:
    amount: int
    currency: str = "UZS"

    def __post_init__(self) -> None:
        if self.amount < 0:
            # Refunds are out of v1 scope (OV-22) — negative amounts are forbidden.
            raise ValueError("Money.amount must be >= 0")
        if len(self.currency) != 3 or not self.currency.isalpha() or not self.currency.isupper():
            raise ValueError("Money.currency must be a 3-letter uppercase ISO 4217 code")


class CurrencyRegistry:
    """Read-only in-memory cache of currency exponents.

    Loaded once at startup via a raw SELECT (no core import — keeps shared
    decoupled). ``exponent`` is synchronous so Money conversions at provider
    boundaries never touch the DB.
    """

    def __init__(self) -> None:
        self._exponents: dict[str, int] = {}

    async def load(self, session: AsyncSession) -> None:
        rows = (await session.execute(text("SELECT code, exponent FROM currencies"))).all()
        self._exponents = {str(code): int(exponent) for code, exponent in rows}

    def exponent(self, code: str) -> int:
        try:
            return self._exponents[code]
        except KeyError:
            raise NotFoundError(f"unknown currency: {code}") from None

    def is_loaded(self) -> bool:
        return bool(self._exponents)


# Process-global registry; loaded at startup, read-only afterwards.
currency_registry = CurrencyRegistry()
