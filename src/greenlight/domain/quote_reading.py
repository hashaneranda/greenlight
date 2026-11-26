from dataclasses import dataclass
from decimal import Decimal

from greenlight.domain.spend_request import BillingPeriod


@dataclass(frozen=True)
class QuoteReading:
    """What an AI model read from a supplier's quote, to fill in a request."""

    supplier: str
    title: str
    currency: str
    amount: Decimal
    billing_period: BillingPeriod | None = None
    term_months: int | None = None
    category_name: str = ""
    unclear_fields: tuple[str, ...] = ()

    @property
    def is_recurring(self) -> bool:
        return self.billing_period is not None
