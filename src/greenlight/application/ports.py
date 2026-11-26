from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, Self
from uuid import UUID

from greenlight.domain.approval_policy import ApprovalPolicy
from greenlight.domain.catalog import Category, Tag
from greenlight.domain.cover import Cover
from greenlight.domain.invoice_matching import Invoice
from greenlight.domain.people import Department, Employee, Role, Site
from greenlight.domain.quote_reading import QuoteReading
from greenlight.domain.spend_request import RequestStatus, SpendRequest


class SpendRequests(Protocol):
    def add(self, request: SpendRequest) -> None: ...

    def get(self, request_id: UUID) -> SpendRequest | None: ...

    def by_reference(self, reference: str, *, for_update: bool = False) -> SpendRequest | None: ...

    def next_reference(self) -> str: ...

    def all(self) -> list[SpendRequest]: ...

    def raised_by(self, requester_id: UUID) -> list[SpendRequest]: ...

    def with_status(self, status: RequestStatus) -> list[SpendRequest]: ...


class People(Protocol):
    def get(self, employee_id: UUID) -> Employee | None: ...

    def by_email(self, email: str) -> Employee | None: ...

    def active(self) -> list[Employee]: ...

    def with_role(self, role: Role) -> list[Employee]: ...

    def all(self) -> list[Employee]: ...

    def add(self, employee: Employee) -> None: ...

    def departments(self) -> list[Department]: ...

    def sites(self) -> list[Site]: ...


class Catalog(Protocol):
    def categories(self) -> list[Category]: ...

    def category(self, category_id: UUID) -> Category | None: ...

    def tags(self) -> list[Tag]: ...

    def tag(self, tag_id: UUID) -> Tag | None: ...

    def add_tag(self, tag: Tag) -> None: ...

    def approval_policy(self) -> ApprovalPolicy: ...

    def set_executive_threshold(self, category_id: UUID, amount_usd: Decimal) -> None: ...

    def usd_rate(self, currency: str) -> Decimal | None: ...

    def currencies(self) -> list[str]: ...


class Covers(Protocol):
    def add(self, cover: Cover) -> None: ...

    def get(self, cover_id: UUID) -> Cover | None: ...

    def for_absent(self, employee_id: UUID) -> list[Cover]: ...

    def active_on(self, day: date) -> list[Cover]: ...

    def held_by(self, covering_id: UUID, day: date) -> list[Cover]: ...


@dataclass
class AuditEntry:
    action: str
    occurred_at: datetime
    actor_id: UUID | None
    request_id: UUID | None
    on_behalf_of_id: UUID | None = None
    details: dict = field(default_factory=dict)
    previous_hash: str = ""
    hash: str = ""


class AuditTrail(Protocol):
    def append(self, entry: AuditEntry) -> None: ...

    def for_request(self, request_id: UUID) -> list[AuditEntry]: ...

    def all(self) -> list[AuditEntry]: ...


@dataclass
class OutgoingNotification:
    recipient_id: UUID
    subject: str
    body: str
    request_id: UUID | None
    created_at: datetime


class Outbox(Protocol):
    def add(self, notification: OutgoingNotification) -> None: ...


class UnitOfWork(Protocol):
    requests: SpendRequests
    people: People
    catalog: Catalog
    covers: Covers
    audit: AuditTrail
    outbox: Outbox

    def __enter__(self) -> Self: ...

    def __exit__(self, *exc_info) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class FileStorage(Protocol):
    def save(self, key: str, content: bytes, content_type: str) -> None: ...

    def load(self, key: str) -> bytes: ...


class InvoiceReader(Protocol):
    def read(self, content: bytes, content_type: str) -> Invoice: ...


class QuoteReader(Protocol):
    def read(self, content: bytes, content_type: str, categories: list[str]) -> QuoteReading: ...


class Clock(Protocol):
    def now(self) -> datetime: ...
