"""What Greenlight learns from past decisions.

Everything here is a suggestion for a person to act on. Nothing changes a rule by itself,
so every rule in force can still be traced to someone who chose it.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from statistics import median
from uuid import UUID

from greenlight.domain.approval_policy import ApprovalPolicy, ApprovalStage
from greenlight.domain.catalog import Category
from greenlight.domain.invoice_matching import supplier_similarity
from greenlight.domain.spend_request import (
    ApprovalStep,
    RequestStatus,
    SpendRequest,
    StepStatus,
)

SAME_SUPPLIER = 0.8
UNUSUALLY_HIGH = Decimal("2")
ENOUGH_TO_COMPARE = 3
ENOUGH_TO_CHANGE_A_RULE = 5


def _same_supplier(left: str, right: str) -> bool:
    return supplier_similarity(left, right) >= SAME_SUPPLIER


def _newest_first(requests: Iterable[SpendRequest]) -> list[SpendRequest]:
    return sorted(requests, key=lambda r: r.submitted_at, reverse=True)


@dataclass(frozen=True)
class Precedent:
    approved_before: list[SpendRequest]
    sent_back_before: list[SpendRequest]
    typical_usd: Decimal | None
    times_typical: Decimal | None

    @property
    def is_unusually_high(self) -> bool:
        return self.times_typical is not None and self.times_typical >= UNUSUALLY_HIGH

    @property
    def says_anything(self) -> bool:
        return bool(self.approved_before or self.sent_back_before or self.is_unusually_high)


def precedent_for(request: SpendRequest, history: Iterable[SpendRequest]) -> Precedent:
    others = [r for r in _newest_first(history) if r.id != request.id]
    same_supplier = [r for r in others if _same_supplier(r.supplier, request.supplier)]
    category_costs = [
        r.approved_total_usd
        for r in others
        if r.category_id == request.category_id and r.approved_total_usd
    ]

    typical = median(category_costs) if len(category_costs) >= ENOUGH_TO_COMPARE else None
    return Precedent(
        approved_before=[r for r in same_supplier if r.status == RequestStatus.APPROVED],
        sent_back_before=[r for r in same_supplier if r.status == RequestStatus.REJECTED],
        typical_usd=typical,
        times_typical=(request.total_usd / typical).quantize(Decimal("0.1")) if typical else None,
    )


@dataclass(frozen=True)
class Filing:
    category_id: UUID
    tag_id: UUID | None
    learned_from: str


def learned_filing(supplier: str, history: Iterable[SpendRequest]) -> Filing | None:
    for request in _newest_first(history):
        filed_well = request.status in (RequestStatus.APPROVED, RequestStatus.PENDING)
        if filed_well and _same_supplier(request.supplier, supplier):
            return Filing(request.category_id, request.tag_id, request.reference)
    return None


def send_back_lessons(
    category_id: UUID | None,
    supplier: str,
    has_quote: bool,
    history: Iterable[SpendRequest],
) -> list[str]:
    sent_back = [r for r in _newest_first(history) if r.status == RequestStatus.REJECTED]
    lessons = []

    if not has_quote and category_id:
        without_quote = [r for r in sent_back if r.category_id == category_id and not r.quotes]
        if len(without_quote) >= 2:
            lessons.append(
                f"{len(without_quote)} requests in this category without a quote were sent back."
            )

    if supplier.strip():
        last = next((r for r in sent_back if _same_supplier(r.supplier, supplier)), None)
        if last:
            lessons.append(f"Last time {last.supplier} was sent back: {last.sent_back_reason}")

    return lessons


@dataclass(frozen=True)
class LimitSuggestion:
    category: Category
    current_usd: Decimal
    suggested_usd: Decimal
    evidence: int
    median_hours: float


def _executive_step(request: SpendRequest) -> ApprovalStep | None:
    return next(
        (s for s in request.current_steps if ApprovalStage.EXECUTIVE in s.stages and s.decided_at),
        None,
    )


def _limit_above(amount: Decimal) -> Decimal:
    step = Decimal("1000") if amount < 20000 else Decimal("5000")
    return (amount // step + 1) * step


def suggest_limits(
    policy: ApprovalPolicy,
    categories: Iterable[Category],
    history: Iterable[SpendRequest],
) -> list[LimitSuggestion]:
    """A higher COO limit for a category where the COO approves everything just over it.

    Only requests up to twice the current limit count, so one large contract can't pull the
    suggestion up with it, and anything bigger still goes to the COO.
    """
    decided_by_coo = [(r, step) for r in history if (step := _executive_step(r))]
    suggestions = []

    for category in categories:
        current = policy.executive_threshold_for(category)
        if current is None:
            continue
        decisions = [
            (r, s)
            for r, s in decided_by_coo
            if r.category_id == category.id and r.total_usd < current * 2
        ]
        if len(decisions) < ENOUGH_TO_CHANGE_A_RULE:
            continue
        if any(step.status == StepStatus.REJECTED for _, step in decisions):
            continue

        hours = [(s.decided_at - s.activated_at).total_seconds() / 3600 for _, s in decisions]
        suggestions.append(
            LimitSuggestion(
                category=category,
                current_usd=current,
                suggested_usd=_limit_above(max(r.total_usd for r, _ in decisions)),
                evidence=len(decisions),
                median_hours=median(hours),
            )
        )
    return suggestions
