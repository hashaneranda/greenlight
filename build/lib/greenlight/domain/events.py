from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class DomainEvent:
    request_id: UUID
    actor_id: UUID | None


@dataclass(frozen=True)
class RequestSubmitted(DomainEvent):
    snapshot: dict


@dataclass(frozen=True)
class ApprovalRouteSet(DomainEvent):
    revision: int
    steps: list[dict]
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ApprovalRequested(DomainEvent):
    approver_id: UUID
    stage_label: str


@dataclass(frozen=True)
class ApprovalReminded(DomainEvent):
    approver_id: UUID
    stage_label: str


@dataclass(frozen=True)
class StepApproved(DomainEvent):
    on_behalf_of_id: UUID | None
    stages: list[str]
    comment: str


@dataclass(frozen=True)
class RequestApproved(DomainEvent):
    total_usd: Decimal


@dataclass(frozen=True)
class RequestRejected(DomainEvent):
    on_behalf_of_id: UUID | None
    stages: list[str]
    reason: str


@dataclass(frozen=True)
class RequestWithdrawn(DomainEvent):
    reason: str


@dataclass(frozen=True)
class RequestRevised(DomainEvent):
    reason: str
    before: dict
    after: dict
    needs_reapproval: bool


@dataclass(frozen=True)
class PaymentRecorded(DomainEvent):
    invoice_reference: str
    amount: str
    amount_usd: Decimal
    over_approval_usd: Decimal | None
