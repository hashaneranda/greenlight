from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from greenlight.application.ports import AuditEntry, Clock, UnitOfWork
from greenlight.application.use_cases import NotFound
from greenlight.domain.catalog import Category, Tag
from greenlight.domain.cover import Cover
from greenlight.domain.errors import NotPermitted
from greenlight.domain.people import Department, Employee, Role
from greenlight.domain.spend_request import RequestStatus, SpendRequest
from greenlight.domain.visibility import can_view

ZERO = Decimal("0")


@dataclass
class Lookups:
    people: dict[UUID, Employee]
    departments: dict[UUID, Department]
    categories: dict[UUID, Category]
    tags: dict[UUID, Tag]

    def person(self, person_id: UUID | None) -> Employee | None:
        return self.people.get(person_id) if person_id else None


@dataclass
class RequestView:
    request: SpendRequest
    lookups: Lookups
    history: list[AuditEntry]
    can_decide: bool
    can_revise: bool
    can_withdraw: bool
    can_record_payment: bool
    covering_for: Employee | None


@dataclass
class Breakdown:
    label: str
    committed_usd: Decimal = ZERO
    paid_usd: Decimal = ZERO
    pending_usd: Decimal = ZERO
    count: int = 0
    budget_usd: Decimal | None = None


@dataclass
class Dashboard:
    scope: str
    waiting_for_me: list[SpendRequest]
    approved_usd: Decimal
    paid_usd: Decimal
    pending_usd: Decimal
    by_category: list[Breakdown]
    by_department: list[Breakdown]
    by_project: list[Breakdown]
    by_trip: list[Breakdown]
    recent: list[SpendRequest] = field(default_factory=list)

    @property
    def outstanding_usd(self) -> Decimal:
        return self.approved_usd - self.paid_usd


class SpendQueries:
    def __init__(self, unit_of_work: Callable[[], UnitOfWork], clock: Clock):
        self._unit_of_work = unit_of_work
        self._clock = clock

    def lookups(self) -> Lookups:
        with self._unit_of_work() as uow:
            return _lookups(uow)

    def person(self, person_id: UUID) -> Employee | None:
        with self._unit_of_work() as uow:
            return uow.people.get(person_id)

    def person_by_email(self, email: str) -> Employee | None:
        with self._unit_of_work() as uow:
            return uow.people.by_email(email.strip().lower())

    def people(self) -> list[Employee]:
        with self._unit_of_work() as uow:
            return sorted(uow.people.active(), key=lambda person: person.name)

    def form_options(self) -> tuple[list[Category], list[Tag], list[str]]:
        with self._unit_of_work() as uow:
            categories = sorted(
                (c for c in uow.catalog.categories() if c.is_active), key=lambda c: c.name
            )
            tags = sorted(
                (t for t in uow.catalog.tags() if t.is_active), key=lambda t: (t.kind, t.name)
            )
            return categories, tags, uow.catalog.currencies()

    def waiting_for(self, viewer_id: UUID) -> list[SpendRequest]:
        with self._unit_of_work() as uow:
            return self._waiting_for(uow, viewer_id)

    def my_requests(self, viewer_id: UUID) -> list[SpendRequest]:
        with self._unit_of_work() as uow:
            mine = [r for r in uow.requests.all() if r.requester_id == viewer_id]
            return sorted(mine, key=lambda r: r.submitted_at, reverse=True)

    def visible_requests(
        self,
        viewer_id: UUID,
        *,
        status: RequestStatus | None = None,
        tag_id: UUID | None = None,
        search: str = "",
    ) -> list[SpendRequest]:
        with self._unit_of_work() as uow:
            viewer = uow.people.get(viewer_id)
            requests = self._visible(uow, viewer)
            if status:
                requests = [r for r in requests if r.status == status]
            if tag_id:
                requests = [r for r in requests if r.tag_id == tag_id]
            if search.strip():
                needle = search.strip().lower()
                requests = [
                    r for r in requests if needle in f"{r.reference} {r.title} {r.supplier}".lower()
                ]
            return sorted(requests, key=lambda r: r.submitted_at, reverse=True)

    def request_view(self, viewer_id: UUID, reference: str) -> RequestView:
        today = self._clock.now().date()
        with self._unit_of_work() as uow:
            viewer = uow.people.get(viewer_id)
            request = uow.requests.by_reference(reference.upper())
            if request is None:
                raise NotFound(f"No request {reference}.")
            if not can_view(viewer, request, **self._viewer_scope(uow, viewer)):
                raise NotPermitted("You do not have access to this request.")

            step = request.pending_step
            cover = _cover_for(uow, step.approver_id, viewer.id, today) if step else None
            paid_or_closed = request.payments or request.status == RequestStatus.WITHDRAWN
            return RequestView(
                request=request,
                lookups=_lookups(uow),
                history=uow.audit.for_request(request.id),
                can_decide=request.can_be_decided_by(viewer, cover, today),
                can_revise=viewer.id == request.requester_id and not paid_or_closed,
                can_withdraw=(
                    viewer.id == request.requester_id
                    and request.status in (RequestStatus.PENDING, RequestStatus.APPROVED)
                    and not request.payments
                ),
                can_record_payment=(
                    viewer.has_role(Role.FINANCE) and request.status == RequestStatus.APPROVED
                ),
                covering_for=uow.people.get(cover.absent_id) if cover else None,
            )

    def dashboard(self, viewer_id: UUID) -> Dashboard:
        with self._unit_of_work() as uow:
            viewer = uow.people.get(viewer_id)
            lookups = _lookups(uow)
            visible = self._visible(uow, viewer)
            scope = "Whole company" if viewer.sees_all_spend else "Spend you can see"
            live = [
                r for r in visible if r.status in (RequestStatus.PENDING, RequestStatus.APPROVED)
            ]

            def label_for(mapping, key, fallback="Unassigned"):
                item = mapping.get(key)
                return getattr(item, "name", fallback) if item else fallback

            projects = {k: t for k, t in lookups.tags.items() if t.kind == "project"}
            trips = {k: t for k, t in lookups.tags.items() if t.kind == "trip"}
            return Dashboard(
                scope=scope,
                waiting_for_me=self._waiting_for(uow, viewer_id),
                approved_usd=_sum(r.approved_total_usd for r in live if r.approved_total_usd),
                paid_usd=_sum(r.paid_usd for r in live),
                pending_usd=_sum(r.total_usd for r in live if r.status == RequestStatus.PENDING),
                by_category=_breakdown(
                    live, lambda r: label_for(lookups.categories, r.category_id)
                ),
                by_department=_breakdown(
                    live, lambda r: label_for(lookups.departments, r.department_id)
                ),
                by_project=_breakdown(
                    [r for r in live if r.tag_id in projects],
                    lambda r: projects[r.tag_id].name,
                    budgets={t.name: t.budget_usd for t in projects.values()},
                ),
                by_trip=_breakdown(
                    [r for r in live if r.tag_id in trips], lambda r: trips[r.tag_id].name
                ),
                recent=sorted(visible, key=lambda r: r.submitted_at, reverse=True)[:8],
            )

    def finance_ledger(self, viewer_id: UUID) -> list[SpendRequest]:
        with self._unit_of_work() as uow:
            viewer = uow.people.get(viewer_id)
            if not viewer.sees_all_spend:
                raise NotPermitted("The payments view is for finance.")
            approved = [r for r in uow.requests.all() if r.status == RequestStatus.APPROVED]
            return sorted(approved, key=lambda r: (r.outstanding_usd == 0, r.decided_at))

    def covers_for(self, viewer_id: UUID) -> tuple[list[Cover], list[Cover]]:
        """Cover the viewer has arranged, and cover the viewer is providing."""
        today = self._clock.now().date()
        with self._unit_of_work() as uow:
            relevant = [
                c
                for c in uow.covers.for_absent(viewer_id)
                if c.cancelled_at is None and c.ends_on >= today
            ]
            providing = [c for c in uow.covers.active_on(today) if c.covering_id == viewer_id]
            return relevant, providing

    def audit_trail(self, viewer_id: UUID) -> list[AuditEntry]:
        with self._unit_of_work() as uow:
            viewer = uow.people.get(viewer_id)
            if not (viewer.has_role(Role.ADMIN) or viewer.has_role(Role.FINANCE)):
                raise NotPermitted("The audit log is for finance and admins.")
            return list(reversed(uow.audit.all()))

    def _waiting_for(self, uow: UnitOfWork, viewer_id: UUID) -> list[SpendRequest]:
        today = self._clock.now().date()
        viewer = uow.people.get(viewer_id)
        covering_for = {
            c.absent_id for c in uow.covers.active_on(today) if c.covering_id == viewer_id
        }
        waiting = []
        for request in uow.requests.all():
            step = request.pending_step
            if step is None:
                continue
            cover = (
                _cover_for(uow, step.approver_id, viewer_id, today)
                if step.approver_id in covering_for
                else None
            )
            if request.can_be_decided_by(viewer, cover, today):
                waiting.append(request)
        return sorted(waiting, key=lambda r: r.pending_step.activated_at)

    def _visible(self, uow: UnitOfWork, viewer: Employee) -> list[SpendRequest]:
        scope = self._viewer_scope(uow, viewer)
        return [r for r in uow.requests.all() if can_view(viewer, r, **scope)]

    def _viewer_scope(self, uow: UnitOfWork, viewer: Employee) -> dict:
        today = self._clock.now().date()
        return {
            "headed_department_ids": {
                d.id for d in uow.people.departments() if d.head_id == viewer.id
            },
            "covering_for_ids": {
                c.absent_id for c in uow.covers.active_on(today) if c.covering_id == viewer.id
            },
        }


def _lookups(uow: UnitOfWork) -> Lookups:
    return Lookups(
        people={p.id: p for p in uow.people.all()},
        departments={d.id: d for d in uow.people.departments()},
        categories={c.id: c for c in uow.catalog.categories()},
        tags={t.id: t for t in uow.catalog.tags()},
    )


def _cover_for(uow: UnitOfWork, absent_id: UUID, covering_id: UUID, today) -> Cover | None:
    return next(
        (
            c
            for c in uow.covers.for_absent(absent_id)
            if c.covering_id == covering_id and c.is_active_on(today)
        ),
        None,
    )


def _sum(values: Iterable[Decimal]) -> Decimal:
    return sum(values, ZERO)


def _breakdown(
    requests: list[SpendRequest],
    key: Callable[[SpendRequest], str],
    budgets: dict[str, Decimal | None] | None = None,
) -> list[Breakdown]:
    groups: dict[str, Breakdown] = defaultdict(lambda: Breakdown(label=""))
    for request in requests:
        label = key(request)
        group = groups[label]
        group.label = label
        group.count += 1
        if request.status == RequestStatus.APPROVED:
            group.committed_usd += request.approved_total_usd or ZERO
            group.paid_usd += request.paid_usd
        else:
            group.pending_usd += request.total_usd
    for label, budget in (budgets or {}).items():
        if label in groups:
            groups[label].budget_usd = budget
    return sorted(groups.values(), key=lambda g: g.committed_usd + g.pending_usd, reverse=True)
