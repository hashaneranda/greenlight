from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from greenlight.application.loading import (
    active_person,
    approvals_waiting_on,
    request_by_reference,
)
from greenlight.application.ports import AuditEntry, Clock, RateRow, UnitOfWork
from greenlight.domain.catalog import Category, Tag, TagKind
from greenlight.domain.cover import Cover
from greenlight.domain.errors import NotPermitted
from greenlight.domain.learning import (
    Filing,
    LimitSuggestion,
    Precedent,
    learned_filing,
    precedent_for,
    send_back_lessons,
    suggest_limits,
)
from greenlight.domain.people import Department, Employee, Site
from greenlight.domain.spend_request import RequestStatus, SpendRequest
from greenlight.domain.visibility import can_view

ZERO = Decimal("0")
RATE_STALE_AFTER = timedelta(days=35)


@dataclass
class Lookups:
    people: dict[UUID, Employee]
    departments: dict[UUID, Department]
    sites: dict[UUID, Site]
    categories: dict[UUID, Category]
    tags: dict[UUID, Tag]

    def person(self, person_id: UUID | str | None) -> Employee | None:
        if not person_id:
            return None
        return self.people.get(person_id if isinstance(person_id, UUID) else UUID(person_id))


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
    precedent: Precedent | None = None


@dataclass
class Advice:
    filing: Filing | None
    lessons: list[str]


@dataclass
class CooLimit:
    category: Category
    limit_usd: Decimal | None
    is_default: bool


@dataclass
class RulesView:
    default_limit_usd: Decimal | None
    limits: list[CooLimit]
    suggestions: list[LimitSuggestion]
    can_change: bool


@dataclass(frozen=True)
class RateView:
    rate: RateRow
    is_stale: bool


@dataclass
class Breakdown:
    label: str
    budget_usd: Decimal | None = None
    committed_usd: Decimal = ZERO
    paid_usd: Decimal = ZERO
    pending_usd: Decimal = ZERO
    count: int = 0

    def add(self, request: SpendRequest) -> None:
        self.count += 1
        self.committed_usd += request.committed_usd
        self.paid_usd += request.paid_usd
        self.pending_usd += request.awaiting_approval_usd


@dataclass
class Dashboard:
    scope: str
    waiting_for_me: list[SpendRequest]
    approved_usd: Decimal
    paid_usd: Decimal
    outstanding_usd: Decimal
    pending_usd: Decimal
    by_category: list[Breakdown]
    by_department: list[Breakdown]
    by_project: list[Breakdown]
    by_trip: list[Breakdown]
    recent: list[SpendRequest] = field(default_factory=list)


@dataclass
class _ViewerContext:
    viewer: Employee
    today: date
    headed_department_ids: set[UUID]
    covers_held: list[Cover]

    @property
    def covering_for_ids(self) -> set[UUID]:
        return {cover.absent_id for cover in self.covers_held}

    def can_view(self, request: SpendRequest) -> bool:
        return can_view(
            self.viewer,
            request,
            headed_department_ids=self.headed_department_ids,
            covering_for_ids=self.covering_for_ids,
        )

    def can_decide(self, request: SpendRequest) -> bool:
        return request.can_be_decided_by(self.viewer, self.today, self.covers_held)


class SpendQueries:
    def __init__(self, unit_of_work: Callable[[], UnitOfWork], clock: Clock):
        self._unit_of_work = unit_of_work
        self._clock = clock

    def person(self, person_id: UUID) -> Employee | None:
        with self._unit_of_work() as uow:
            return uow.people.get(person_id)

    def person_by_email(self, email: str) -> Employee | None:
        with self._unit_of_work() as uow:
            return uow.people.by_email(email.strip().lower())

    def people(self) -> list[Employee]:
        with self._unit_of_work() as uow:
            return uow.people.active()

    def directory(self, search: str = "") -> list[Employee]:
        """Everyone who has ever been in Greenlight, people still here first."""
        needle = search.strip().lower()
        with self._unit_of_work() as uow:
            return sorted(
                (
                    p
                    for p in uow.people.all()
                    if needle in f"{p.name} {p.email} {p.job_title}".lower()
                ),
                key=lambda p: (not p.is_active, p.name),
            )

    def approvals_waiting_on(self, person_id: UUID) -> list[SpendRequest]:
        with self._unit_of_work() as uow:
            person = uow.people.get(person_id)
            return approvals_waiting_on(uow, person) if person else []

    def lookups(self) -> Lookups:
        with self._unit_of_work() as uow:
            return _lookups(uow)

    def form_options(self) -> tuple[list[Category], list[Tag], list[str]]:
        with self._unit_of_work() as uow:
            categories = sorted(
                (c for c in uow.catalog.categories() if c.is_active), key=lambda c: c.name
            )
            tags = sorted(
                (t for t in uow.catalog.tags() if t.is_active), key=lambda t: (t.kind, t.name)
            )
            return categories, tags, uow.catalog.currencies()

    def rates_view(self) -> list[RateView]:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            return [
                RateView(rate, now - rate.updated_at > RATE_STALE_AFTER)
                for rate in uow.catalog.rates()
            ]

    def waiting_for(self, viewer_id: UUID) -> list[SpendRequest]:
        with self._unit_of_work() as uow:
            return _waiting(uow, self._context(uow, viewer_id))

    def my_requests(self, viewer_id: UUID) -> list[SpendRequest]:
        with self._unit_of_work() as uow:
            return _newest_first(uow.requests.raised_by(viewer_id))

    def visible_requests(
        self,
        viewer_id: UUID,
        *,
        status: RequestStatus | None = None,
        tag_id: UUID | None = None,
        search: str = "",
    ) -> list[SpendRequest]:
        needle = search.strip().lower()
        with self._unit_of_work() as uow:
            context = self._context(uow, viewer_id)
            candidates = uow.requests.with_status(status) if status else uow.requests.all()
            return _newest_first(
                r
                for r in candidates
                if context.can_view(r)
                and (tag_id is None or r.tag_id == tag_id)
                and (not needle or needle in f"{r.reference} {r.title} {r.supplier}".lower())
            )

    def request_view(self, viewer_id: UUID, reference: str) -> RequestView:
        with self._unit_of_work() as uow:
            context = self._context(uow, viewer_id)
            viewer = context.viewer
            request = request_by_reference(uow, reference)
            if not context.can_view(request):
                raise NotPermitted("You do not have access to this request.")

            step = request.pending_step
            covering_for = (
                uow.people.get(step.approver_id)
                if step and step.approver_id in context.covering_for_ids
                else None
            )
            return RequestView(
                request=request,
                lookups=_lookups(uow),
                history=uow.audit.for_request(request.id),
                can_decide=context.can_decide(request),
                can_revise=request.can_be_revised_by(viewer),
                can_withdraw=request.can_be_withdrawn_by(viewer),
                can_record_payment=request.can_be_paid_by(viewer),
                covering_for=covering_for,
                precedent=precedent_for(
                    request, (r for r in uow.requests.all() if context.can_view(r))
                ),
            )

    def advice(
        self, viewer_id: UUID, supplier: str, category_id: UUID | None, has_quote: bool
    ) -> Advice:
        """Learned from requests anyone may see, so nothing confidential leaks into advice."""
        with self._unit_of_work() as uow:
            active_person(uow, viewer_id)
            shared = [r for r in uow.requests.all() if not r.is_confidential]
            filing = learned_filing(supplier, shared) if supplier.strip() else None
            if filing and filing.tag_id:
                tag = uow.catalog.tag(filing.tag_id)
                if tag is None or not tag.is_active:
                    filing = Filing(filing.category_id, None, filing.learned_from)
            return Advice(filing, send_back_lessons(category_id, supplier, has_quote, shared))

    def rules(self, viewer_id: UUID) -> RulesView:
        with self._unit_of_work() as uow:
            viewer = active_person(uow, viewer_id)
            if not viewer.sees_all_spend:
                raise NotPermitted("Approval rules are for finance and the COO.")
            policy = uow.catalog.approval_policy()
            categories = sorted(
                (c for c in uow.catalog.categories() if c.is_active), key=lambda c: c.name
            )
            return RulesView(
                default_limit_usd=policy.executive_threshold_usd,
                limits=[
                    CooLimit(
                        c,
                        policy.executive_threshold_for(c),
                        c.id not in policy.category_thresholds_usd,
                    )
                    for c in categories
                ],
                suggestions=suggest_limits(policy, categories, uow.requests.all()),
                can_change=viewer.can_change_rules,
            )

    def dashboard(self, viewer_id: UUID) -> Dashboard:
        with self._unit_of_work() as uow:
            context = self._context(uow, viewer_id)
            visible = [r for r in uow.requests.all() if context.can_view(r)]
            live = [r for r in visible if r.is_live]
            lookups = _lookups(uow)
            projects = {k: t for k, t in lookups.tags.items() if t.kind == TagKind.PROJECT}
            trips = {k: t for k, t in lookups.tags.items() if t.kind == TagKind.TRIP}

            return Dashboard(
                scope="Whole company" if context.viewer.sees_all_spend else "Spend you can see",
                waiting_for_me=_waiting(uow, context, visible),
                approved_usd=_sum(r.committed_usd for r in live),
                paid_usd=_sum(r.paid_usd for r in live),
                outstanding_usd=_sum(r.outstanding_usd for r in live),
                pending_usd=_sum(r.awaiting_approval_usd for r in live),
                by_category=_breakdown(live, lambda r: _name(lookups.categories, r.category_id)),
                by_department=_breakdown(
                    live, lambda r: _name(lookups.departments, r.department_id)
                ),
                by_project=_breakdown(
                    (r for r in live if r.tag_id in projects),
                    lambda r: projects[r.tag_id].name,
                    budgets={t.name: t.budget_usd for t in projects.values()},
                ),
                by_trip=_breakdown(
                    (r for r in live if r.tag_id in trips), lambda r: trips[r.tag_id].name
                ),
                recent=_newest_first(visible)[:8],
            )

    def payments_ledger(self, viewer_id: UUID) -> list[SpendRequest]:
        with self._unit_of_work() as uow:
            if not active_person(uow, viewer_id).sees_all_spend:
                raise NotPermitted("The payments view is for finance and the COO.")
            approved = uow.requests.with_status(RequestStatus.APPROVED)
            return sorted(approved, key=lambda r: (r.outstanding_usd == 0, r.decided_at))

    def covers_for(self, viewer_id: UUID) -> tuple[list[Cover], list[Cover]]:
        """Cover the viewer has arranged for themselves, and cover they are giving others."""
        today = self._clock.now().date()
        with self._unit_of_work() as uow:
            arranged = [
                c for c in uow.covers.for_absent(viewer_id) if c.is_current_or_upcoming(today)
            ]
            return arranged, uow.covers.held_by(viewer_id, today)

    def audit_log(self, viewer_id: UUID) -> list[AuditEntry]:
        with self._unit_of_work() as uow:
            if not active_person(uow, viewer_id).can_read_audit_log:
                raise NotPermitted("The audit log is for finance and admins.")
            return list(reversed(uow.audit.all()))

    def _context(self, uow: UnitOfWork, viewer_id: UUID) -> _ViewerContext:
        today = self._clock.now().date()
        viewer = active_person(uow, viewer_id)
        return _ViewerContext(
            viewer=viewer,
            today=today,
            headed_department_ids={
                d.id for d in uow.people.departments() if d.head_id == viewer.id
            },
            covers_held=uow.covers.held_by(viewer.id, today),
        )


def _waiting(
    uow: UnitOfWork, context: _ViewerContext, candidates: Iterable[SpendRequest] | None = None
) -> list[SpendRequest]:
    if candidates is None:
        candidates = uow.requests.with_status(RequestStatus.PENDING)
    waiting = [r for r in candidates if context.can_decide(r)]
    return sorted(waiting, key=lambda r: r.pending_step.activated_at)


def _lookups(uow: UnitOfWork) -> Lookups:
    return Lookups(
        people={p.id: p for p in uow.people.all()},
        departments={d.id: d for d in uow.people.departments()},
        sites={s.id: s for s in uow.people.sites()},
        categories={c.id: c for c in uow.catalog.categories()},
        tags={t.id: t for t in uow.catalog.tags()},
    )


def _name(items: dict, key: UUID | None) -> str:
    item = items.get(key)
    return item.name if item else "Unassigned"


def _newest_first(requests: Iterable[SpendRequest]) -> list[SpendRequest]:
    return sorted(requests, key=lambda r: r.submitted_at, reverse=True)


def _sum(values: Iterable[Decimal]) -> Decimal:
    return sum(values, ZERO)


def _breakdown(
    requests: Iterable[SpendRequest],
    label_of: Callable[[SpendRequest], str],
    budgets: dict[str, Decimal | None] | None = None,
) -> list[Breakdown]:
    budgets = budgets or {}
    groups: dict[str, Breakdown] = {}
    for request in requests:
        label = label_of(request)
        groups.setdefault(label, Breakdown(label, budget_usd=budgets.get(label))).add(request)
    return sorted(groups.values(), key=lambda g: g.committed_usd + g.pending_usd, reverse=True)
