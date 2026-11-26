from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
BASE_CURRENCY = "USD"


def round_money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if len(self.currency) != 3 or not self.currency.isupper():
            raise ValueError(f"Not an ISO currency code: {self.currency!r}")

    def times(self, factor: int) -> "Money":
        return Money(round_money(self.amount * factor), self.currency)

    def in_usd(self, usd_per_unit: Decimal) -> Decimal:
        return round_money(self.amount * usd_per_unit)

    def __str__(self) -> str:
        return f"{self.currency} {self.amount:,.2f}"
