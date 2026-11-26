from collections.abc import Collection
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.orm import Session

from greenlight.application import audit_chain
from greenlight.application.ports import AuditEntry, OutgoingNotification, RateRow
from greenlight.domain.approval_policy import ApprovalPolicy
from greenlight.domain.catalog import Category, Tag
from greenlight.domain.cover import Cover
from greenlight.domain.money import BASE_CURRENCY
from greenlight.domain.people import Department, Employee, Role, Site
from greenlight.domain.spend_request import RequestStatus, SpendRequest
from greenlight.infrastructure.persistence import tables

AUDIT_LOCK = 4_711_001


class SqlSpendRequests:
    def __init__(self, session: Session):
        self._session = session

    def add(self, request: SpendRequest) -> None:
        self._session.add(request)

    def get(self, request_id: UUID) -> SpendRequest | None:
        return self._session.get(SpendRequest, request_id)

    def by_reference(self, reference: str, *, for_update: bool = False) -> SpendRequest | None:
        query = select(SpendRequest).where(tables.spend_requests.c.reference == reference)
        if for_update:
            query = query.with_for_update(of=tables.spend_requests)
        return self._session.scalars(query).first()

    def next_reference(self) -> str:
        counter = tables.counters
        bumped = self._session.execute(
            update(counter)
            .where(counter.c.name == "spend_request")
            .values(value=counter.c.value + 1)
            .returning(counter.c.value)
        ).scalar()
        if bumped is None:
            self._session.execute(counter.insert().values(name="spend_request", value=1))
            bumped = 1
        return f"GL-{bumped:05d}"

    def all(self) -> list[SpendRequest]:
        return list(self._session.scalars(select(SpendRequest)))

    def raised_by(self, requester_id: UUID) -> list[SpendRequest]:
        return list(
            self._session.scalars(
                select(SpendRequest).where(tables.spend_requests.c.requester_id == requester_id)
            )
        )

    def with_status(self, status: RequestStatus) -> list[SpendRequest]:
        return list(
            self._session.scalars(
                select(SpendRequest).where(tables.spend_requests.c.status == status)
            )
        )

    def references(self, request_ids: Collection[UUID]) -> dict[UUID, str]:
        columns = tables.spend_requests.c
        rows = self._session.execute(
            select(columns.id, columns.reference).where(columns.id.in_(request_ids))
        )
        return {row.id: row.reference for row in rows}


class SqlPeople:
    def __init__(self, session: Session):
        self._session = session

    def get(self, employee_id: UUID) -> Employee | None:
        return self._session.get(Employee, employee_id) if employee_id else None

    def by_email(self, email: str) -> Employee | None:
        return self._session.scalars(
            select(Employee).where(func.lower(tables.employees.c.email) == email.lower())
        ).first()

    def active(self) -> list[Employee]:
        return list(
            self._session.scalars(
                select(Employee)
                .where(tables.employees.c.is_active)
                .order_by(tables.employees.c.name)
            )
        )

    def all(self) -> list[Employee]:
        return list(self._session.scalars(select(Employee)))

    def with_role(self, role: Role) -> list[Employee]:
        return [person for person in self.active() if person.has_role(role)]

    def add(self, employee: Employee) -> None:
        # A flush sends updates before inserts, so a colleague changed later in this
        # transaction to report to the new person would otherwise point at nobody.
        self._session.add(employee)
        self._session.flush()

    def departments(self) -> list[Department]:
        return list(self._session.scalars(select(Department)))

    def sites(self) -> list[Site]:
        return list(self._session.scalars(select(Site)))


class SqlCatalog:
    def __init__(self, session: Session):
        self._session = session

    def categories(self) -> list[Category]:
        return list(self._session.scalars(select(Category)))

    def category(self, category_id: UUID) -> Category | None:
        return self._session.get(Category, category_id)

    def add_category(self, category: Category) -> None:
        self._session.add(category)

    def tags(self) -> list[Tag]:
        return list(self._session.scalars(select(Tag)))

    def tag(self, tag_id: UUID) -> Tag | None:
        return self._session.get(Tag, tag_id)

    def add_tag(self, tag: Tag) -> None:
        self._session.add(tag)

    def approval_policy(self) -> ApprovalPolicy:
        rows = self._session.execute(select(tables.approval_thresholds)).all()
        default = next((row.amount_usd for row in rows if row.category_id is None), None)
        return ApprovalPolicy(
            executive_threshold_usd=default,
            category_thresholds_usd={
                row.category_id: row.amount_usd for row in rows if row.category_id is not None
            },
        )

    def set_executive_threshold(self, category_id: UUID, amount_usd: Decimal) -> None:
        thresholds = tables.approval_thresholds
        changed = self._session.execute(
            update(thresholds)
            .where(thresholds.c.category_id == category_id)
            .values(amount_usd=amount_usd)
        )
        if changed.rowcount == 0:
            self._session.execute(
                insert(thresholds).values(category_id=category_id, amount_usd=amount_usd)
            )

    def usd_rate(self, currency: str) -> Decimal | None:
        if currency == BASE_CURRENCY:
            return Decimal("1")
        return self._session.execute(
            select(tables.exchange_rates.c.usd_per_unit).where(
                tables.exchange_rates.c.currency == currency
            )
        ).scalar()

    def rates(self) -> list[RateRow]:
        rates = tables.exchange_rates
        rows = self._session.execute(select(rates).order_by(rates.c.currency))
        return [RateRow(**row._mapping) for row in rows]

    def set_usd_rate(self, currency: str, usd_per_unit: Decimal, by_id: UUID, at: datetime) -> None:
        rates = tables.exchange_rates
        values = {"usd_per_unit": usd_per_unit, "updated_at": at, "updated_by_id": by_id}
        changed = self._session.execute(
            update(rates).where(rates.c.currency == currency).values(**values)
        )
        if changed.rowcount == 0:
            self._session.execute(insert(rates).values(currency=currency, **values))

    def currencies(self) -> list[str]:
        codes = set(self._session.scalars(select(tables.exchange_rates.c.currency)))
        return [BASE_CURRENCY, *sorted(codes - {BASE_CURRENCY})]


class SqlCovers:
    def __init__(self, session: Session):
        self._session = session

    def add(self, cover: Cover) -> None:
        self._session.add(cover)

    def get(self, cover_id: UUID) -> Cover | None:
        return self._session.get(Cover, cover_id)

    def for_absent(self, employee_id: UUID) -> list[Cover]:
        return list(
            self._session.scalars(select(Cover).where(tables.covers.c.absent_id == employee_id))
        )

    def active_on(self, day: date) -> list[Cover]:
        columns = tables.covers.c
        return list(
            self._session.scalars(
                select(Cover).where(
                    columns.cancelled_at.is_(None), columns.starts_on <= day, columns.ends_on >= day
                )
            )
        )

    def held_by(self, covering_id: UUID, day: date) -> list[Cover]:
        return [cover for cover in self.active_on(day) if cover.covering_id == covering_id]


class SqlAuditTrail:
    def __init__(self, session: Session):
        self._session = session

    def append(self, entry: AuditEntry) -> None:
        self._serialise_writers()
        latest = self._session.execute(
            select(tables.audit_entries.c.hash)
            .order_by(tables.audit_entries.c.sequence.desc())
            .limit(1)
        ).scalar()
        self._session.add(audit_chain.seal(entry, latest))
        self._session.flush()

    def _serialise_writers(self) -> None:
        # Each entry hashes the previous one, so writers must take turns. The lock is
        # held until the transaction ends. SQLite already allows only one writer.
        if self._session.get_bind().dialect.name == "postgresql":
            self._session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": AUDIT_LOCK})

    def for_request(self, request_id: UUID) -> list[AuditEntry]:
        return list(
            self._session.scalars(
                select(AuditEntry)
                .where(tables.audit_entries.c.request_id == request_id)
                .order_by(tables.audit_entries.c.sequence)
            )
        )

    def all(self) -> list[AuditEntry]:
        return list(
            self._session.scalars(select(AuditEntry).order_by(tables.audit_entries.c.sequence))
        )


class SqlOutbox:
    def __init__(self, session: Session):
        self._session = session

    def add(self, notification: OutgoingNotification) -> None:
        self._session.add(notification)

    def for_recipient(self, recipient_id: UUID, limit: int = 50) -> list[OutgoingNotification]:
        columns = tables.notifications.c
        return list(
            self._session.scalars(
                select(OutgoingNotification)
                .where(columns.recipient_id == recipient_id)
                .order_by(columns.created_at.desc(), columns.id.desc())
                .limit(limit)
            )
        )

    def unread_count(self, recipient_id: UUID) -> int:
        columns = tables.notifications.c
        return self._session.scalar(
            select(func.count())
            .select_from(tables.notifications)
            .where(columns.recipient_id == recipient_id, columns.read_at.is_(None))
        )

    def mark_all_read(self, recipient_id: UUID, now: datetime) -> None:
        columns = tables.notifications.c
        self._session.execute(
            update(tables.notifications)
            .where(columns.recipient_id == recipient_id, columns.read_at.is_(None))
            .values(read_at=now)
        )
