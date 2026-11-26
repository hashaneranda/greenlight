import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher

from greenlight.domain.money import Money
from greenlight.domain.spend_request import RequestStatus, SpendRequest

_COMPANY_SUFFIXES = re.compile(
    r"\b(ltd|limited|llc|l\.l\.c|inc|plc|gmbh|fze|fzco|fz-llc|co|company|sarl|pty)\b\.?"
)


@dataclass(frozen=True)
class InvoiceLine:
    description: str
    amount: Decimal


@dataclass(frozen=True)
class Invoice:
    supplier: str
    invoice_number: str
    currency: str
    total: Decimal
    invoice_date: date | None = None
    purchase_reference: str = ""
    lines: tuple[InvoiceLine, ...] = field(default_factory=tuple)

    @property
    def total_money(self) -> Money:
        return Money(self.total, self.currency)


@dataclass(frozen=True)
class InvoiceMatch:
    request: SpendRequest
    score: float
    invoice_usd: Decimal
    reasons: list[str]
    warnings: list[str]


def normalise_supplier(name: str) -> str:
    cleaned = _COMPANY_SUFFIXES.sub("", name.lower())
    return re.sub(r"[^a-z0-9]+", " ", cleaned).strip()


def _supplier_similarity(left: str, right: str) -> float:
    a, b = normalise_supplier(left), normalise_supplier(right)
    if not a or not b:
        return 0.0
    shorter, longer = sorted((a, b), key=len)
    if len(shorter) >= 4 and f" {shorter} " in f" {longer} ":
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _expected_charge_usd(request: SpendRequest) -> Decimal:
    if request.is_recurring:
        return request.unit_price.in_usd(request.usd_rate)
    return request.outstanding_usd


def rank_matches(
    invoice: Invoice, candidates: list[SpendRequest], usd_rate: Decimal, limit: int = 3
) -> list[InvoiceMatch]:
    invoice_usd = invoice.total_money.in_usd(usd_rate)
    matches = []

    for request in candidates:
        if request.status != RequestStatus.APPROVED or request.outstanding_usd <= 0:
            continue

        reasons: list[str] = []
        warnings: list[str] = []
        score = 0.0

        supplier_score = _supplier_similarity(invoice.supplier, request.supplier)
        score += 0.5 * supplier_score
        if supplier_score < 0.6:
            continue
        if supplier_score >= 0.8:
            reasons.append("Supplier matches")

        quotes_reference = invoice.purchase_reference.upper() == request.reference
        if quotes_reference:
            score += 1.0
            reasons.append(f"Invoice quotes {request.reference}")

        expected = _expected_charge_usd(request)
        if expected > 0:
            gap = abs(invoice_usd - expected) / expected
            amount_score = max(0.0, 1.0 - float(gap) * 5)
            score += 0.4 * amount_score
            if gap <= Decimal("0.02"):
                reasons.append("Amount matches")

        if invoice.currency == request.currency:
            score += 0.1

        if invoice_usd > request.outstanding_usd:
            warnings.append(
                f"Invoice is USD {invoice_usd - request.outstanding_usd:,.2f} more than "
                "what is left on the approval"
            )

        if score >= 0.45:
            matches.append(InvoiceMatch(request, round(score, 3), invoice_usd, reasons, warnings))

    matches.sort(key=lambda match: match.score, reverse=True)
    return matches[:limit]
