"""Money: integer minor units + ISO 4217 currency code. No floats anywhere.

Currency exponents live in the ``currencies`` table (UZS=0, USD=2); the
in-memory CurrencyRegistry arrives with billing in Phase 3.
"""

from dataclasses import dataclass


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
