import enum
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from greenlight.domain import events
from greenlight.domain.approval_policy import ApprovalStage, Route
from greenlight.domain.cover import Cover
from greenlight.domain.errors import InvalidRequest, InvalidState, NotPermitted
from greenlight.domain.money import Money, round_money
from greenlight.domain.people import Employee

MAX_TERM_MONTHS = 120


class RequestStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class StepStatus(enum.StrEnum):
    WAITING = "waiting"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class BillingPeriod(enum.StrEnum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUALLY = "annually"

    @property
    def months(self) -> int:
        return _MONTHS_PER_PERIOD[self]


_MONTHS_PER_PERIOD = {
    BillingPeriod.MONTHLY: 1,
    BillingPeriod.QUARTERLY: 3,
    BillingPeriod.ANNUALLY: 12,
}


def number_of_payments(billing_period: BillingPeriod | None, term_months: int | None) -> int:
    if billing_period is None or not term_months:
        return 1
    return -(-term_months // billing_period.months)


@dataclass(frozen=True)
class SpendDetails:
    title: str
    supplier: str
    justification: str
    category_id: UUID
    amount: Money
    tag_id: UUID | None = None
    billing_period: BillingPeriod | None = None
    term_months: int | None = None
    no_quote_reason: str = ""
    is_confidential: bool = False

    @property
    def is_recurring(self) -> bool:
        return self.billing_period is not None

    @property
    def number_of_payments(self) -> int:
        return number_of_payments(self.billing_period, self.term_months)

    def validate(self, *, has_quote: bool) -> None:
        if not self.title.strip() or not self.supplier.strip():
            raise InvalidRequest("A title and a supplier are required.")
        if not (self.title + self.supplier).isprintable():
            raise InvalidRequest("The title and supplier must each be a single line of text.")
        if not self.justification.strip():
            raise InvalidRequest("Say what the spend is for.")
        if self.amount.amount <= 0:
            raise InvalidRequest("The amount must be more than zero.")
        if self.billing_period and not (
            self.term_months and 0 < self.term_months <= MAX_TERM_MONTHS
        ):
            raise InvalidRequest(
                f"Recurring spend needs a contract length of 1 to {MAX_TERM_MONTHS} months."
            )
        if not has_quote and not self.no_quote_reason.strip():
            raise InvalidRequest("Attach a quote, or say why there isn't one.")


@dataclass(eq=False)
class Quote:
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    storage_key: str
    uploaded_by_id: UUID
    uploaded_at: datetime
    id: UUID = field(default_factory=uuid4)


@dataclass(eq=False)
class ApprovalStep:
    revision: int
    position: int
    stages: list[str]
    approver_id: UUID
    status: StepStatus = StepStatus.WAITING
    activated_at: datetime | None = None
    decided_at: datetime | None = None
    decided_by_id: UUID | None = None
    comment: str = ""
    reminded_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)

    @property
    def stage_label(self) -> str:
        return " + ".join(ApprovalStage(stage).label for stage in self.stages)

    @property
    def decided_on_behalf(self) -> bool:
        return self.decided_by_id is not None and self.decided_by_id != self.approver_id

    @property
    def on_behalf_of_id(self) -> UUID | None:
        return self.approver_id if self.decided_on_behalf else None

    @property
    def is_open(self) -> bool:
        return self.status in (StepStatus.WAITING, StepStatus.PENDING)


@dataclass(eq=False)
class Payment:
    invoice_reference: str
    amount: Decimal
    currency: str
    amount_usd: Decimal
    paid_on: date
    recorded_by_id: UUID
    recorded_at: datetime
    id: UUID = field(default_factory=uuid4)


@dataclass(eq=False)
class SpendRequest:
    reference: str
    requester_id: UUID
    department_id: UUID | None
    site_id: UUID | None
    title: str
    supplier: str
    justification: str
    category_id: UUID
    tag_id: UUID | None
    amount: Decimal
    currency: str
    usd_rate: Decimal
    billing_period: BillingPeriod | None
    term_months: int | None
    no_quote_reason: str
    is_confidential: bool
    submitted_at: datetime
    status: RequestStatus = RequestStatus.PENDING
    revision: int = 1
    approved_total_usd: Decimal | None = None
    decided_at: datetime | None = None
    steps: list[ApprovalStep] = field(default_factory=list)
    quotes: list[Quote] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=list)
    id: UUID = field(default_factory=uuid4)

    @classmethod
    def submit(
        cls,
        *,
        reference: str,
        requester: Employee,
        details: SpendDetails,
        usd_rate: Decimal,
        quotes: list[Quote],
        now: datetime,
    ) -> "SpendRequest":
        details.validate(has_quote=bool(quotes))
        request = cls(
            reference=reference,
            requester_id=requester.id,
            department_id=requester.department_id,
            site_id=requester.site_id,
            submitted_at=now,
            quotes=list(quotes),
            **_detail_fields(details, usd_rate),
        )
        request._record(events.RequestSubmitted(request.id, requester.id, request.snapshot()))
        return request

    @property
    def details(self) -> SpendDetails:
        return SpendDetails(
            title=self.title,
            supplier=self.supplier,
            justification=self.justification,
            category_id=self.category_id,
            amount=self.unit_price,
            tag_id=self.tag_id,
            billing_period=self.billing_period,
            term_months=self.term_months,
            no_quote_reason=self.no_quote_reason,
            is_confidential=self.is_confidential,
        )

    @property
    def is_recurring(self) -> bool:
        return self.billing_period is not None

    @property
    def unit_price(self) -> Money:
        return Money(self.amount, self.currency)

    @property
    def is_live(self) -> bool:
        return self.status in (RequestStatus.PENDING, RequestStatus.APPROVED)

    @property
    def total(self) -> Money:
        return self.unit_price.times(number_of_payments(self.billing_period, self.term_months))

    @property
    def total_usd(self) -> Decimal:
        return self.total.in_usd(self.usd_rate)

    @property
    def paid_usd(self) -> Decimal:
        return round_money(sum((p.amount_usd for p in self.payments), Decimal("0")))

    @property
    def outstanding_usd(self) -> Decimal:
        if self.status != RequestStatus.APPROVED:
            return Decimal("0")
        return max(Decimal("0"), self.approved_total_usd - self.paid_usd)

    @property
    def committed_usd(self) -> Decimal:
        return self.approved_total_usd if self.status == RequestStatus.APPROVED else Decimal("0")

    @property
    def awaiting_approval_usd(self) -> Decimal:
        return self.total_usd if self.status == RequestStatus.PENDING else Decimal("0")

    @property
    def current_steps(self) -> list[ApprovalStep]:
        return sorted(
            (step for step in self.steps if step.revision == self.revision),
            key=lambda step: step.position,
        )

    @property
    def sent_back_step(self) -> ApprovalStep | None:
        return next((s for s in self.current_steps if s.status == StepStatus.REJECTED), None)

    @property
    def sent_back_reason(self) -> str:
        return self.sent_back_step.comment if self.sent_back_step else ""

    @property
    def pending_step(self) -> ApprovalStep | None:
        return next((s for s in self.current_steps if s.status == StepStatus.PENDING), None)

    def start_approval(self, route: Route, now: datetime) -> None:
        if self.status != RequestStatus.PENDING or any(s.is_open for s in self.current_steps):
            raise InvalidState("Approval has already started for this revision.")
        if not route.steps:
            raise InvalidState("An approval route needs at least one step.")

        for position, planned in enumerate(route.steps):
            self.steps.append(
                ApprovalStep(
                    revision=self.revision,
                    position=position,
                    stages=[stage.value for stage in planned.stages],
                    approver_id=planned.approver.id,
                )
            )
        self._record(
            events.ApprovalRouteSet(
                self.id,
                None,
                revision=self.revision,
                steps=[
                    {"approver_id": str(step.approver_id), "stages": step.stages}
                    for step in self.current_steps
                ],
                notes=route.notes,
            )
        )
        self._activate(self.current_steps[0], now)

    def can_be_decided_by(
        self, actor: Employee, today: date, covers_held: Iterable[Cover] = ()
    ) -> bool:
        step = self.pending_step
        if step is None or actor.id == self.requester_id or self._approved_by(actor):
            return False
        if actor.id == step.approver_id:
            return True
        holds_another_step = any(
            other.approver_id == actor.id for other in self.current_steps if other is not step
        )
        covering = any(
            cover.covering_id == actor.id
            and cover.absent_id == step.approver_id
            and cover.is_active_on(today)
            for cover in covers_held
        )
        return covering and not holds_another_step

    def _approved_by(self, actor: Employee) -> bool:
        return any(
            step.decided_by_id == actor.id and step.status == StepStatus.APPROVED
            for step in self.current_steps
        )

    def can_be_withdrawn_by(self, actor: Employee) -> bool:
        return actor.id == self.requester_id and self.is_live and not self.payments

    def can_be_revised_by(self, actor: Employee) -> bool:
        return (
            actor.id == self.requester_id
            and self.status != RequestStatus.WITHDRAWN
            and not self.payments
        )

    def can_be_paid_by(self, actor: Employee) -> bool:
        return actor.can_record_payments and self.status == RequestStatus.APPROVED

    def approve(
        self,
        actor: Employee,
        now: datetime,
        *,
        comment: str = "",
        covers_held: Iterable[Cover] = (),
    ) -> None:
        step = self._step_for_decision(actor, now, covers_held)
        self._close_step(step, actor, StepStatus.APPROVED, comment, now)
        self._record(
            events.StepApproved(
                self.id,
                actor.id,
                on_behalf_of_id=step.on_behalf_of_id,
                stages=step.stages,
                comment=step.comment,
            )
        )

        upcoming = next((s for s in self.current_steps if s.status == StepStatus.WAITING), None)
        if upcoming:
            self._activate(upcoming, now)
            return

        self.status = RequestStatus.APPROVED
        self.decided_at = now
        self.approved_total_usd = self.total_usd
        self._record(events.RequestApproved(self.id, actor.id, total_usd=self.total_usd))

    def reject(
        self,
        actor: Employee,
        now: datetime,
        *,
        reason: str,
        covers_held: Iterable[Cover] = (),
    ) -> None:
        if not reason.strip():
            raise InvalidRequest("Give a reason so the requester knows what to change.")
        step = self._step_for_decision(actor, now, covers_held)
        self._close_step(step, actor, StepStatus.REJECTED, reason, now)
        self._cancel_open_steps()
        self.status = RequestStatus.REJECTED
        self.decided_at = now
        self._record(
            events.RequestRejected(
                self.id,
                actor.id,
                on_behalf_of_id=step.on_behalf_of_id,
                stages=step.stages,
                reason=step.comment,
            )
        )

    def withdraw(self, actor: Employee, now: datetime, reason: str = "") -> None:
        if actor.id != self.requester_id:
            raise NotPermitted("Only the requester can withdraw a request.")
        if not self.can_be_withdrawn_by(actor):
            raise InvalidState("This request can no longer be withdrawn.")
        self._cancel_open_steps()
        self.status = RequestStatus.WITHDRAWN
        self.decided_at = now
        self._record(events.RequestWithdrawn(self.id, actor.id, reason=reason.strip()))

    def revise(
        self,
        actor: Employee,
        details: SpendDetails,
        usd_rate: Decimal,
        new_quotes: list[Quote],
        reason: str,
    ) -> bool:
        """Returns True when the change needs approving again."""
        if actor.id != self.requester_id:
            raise NotPermitted("Only the requester can change a request.")
        if self.status == RequestStatus.WITHDRAWN:
            raise InvalidState("A withdrawn request cannot be changed. Raise a new one.")
        if self.payments:
            raise InvalidState("Payments have been made against this request. Ask finance.")
        if not reason.strip():
            raise InvalidRequest("Say what changed and why.")
        details.validate(has_quote=bool(self.quotes or new_quotes))

        before = self.snapshot()
        approved_total = self.approved_total_usd
        category_before = self.category_id
        supplier_before = self.supplier
        for name, value in _detail_fields(details, usd_rate).items():
            setattr(self, name, value)
        self.quotes.extend(new_quotes)

        needs_reapproval = (
            self.status != RequestStatus.APPROVED
            or approved_total is None
            or self.category_id != category_before
            or self.supplier.casefold() != supplier_before.casefold()
            or self.total_usd > approved_total
        )
        self._record(
            events.RequestRevised(
                self.id,
                actor.id,
                reason=reason.strip(),
                before=before,
                after=self.snapshot(),
                needs_reapproval=needs_reapproval,
            )
        )

        if needs_reapproval:
            self._cancel_open_steps()
            self.revision += 1
            self.status = RequestStatus.PENDING
            self.decided_at = None
            self.approved_total_usd = None
        else:
            self.approved_total_usd = self.total_usd
        return needs_reapproval

    def record_payment(
        self,
        actor: Employee,
        *,
        invoice_reference: str,
        amount: Money,
        usd_rate: Decimal,
        paid_on: date,
        now: datetime,
        confirm_over_approval: bool = False,
    ) -> Payment:
        if not actor.can_record_payments:
            raise NotPermitted("Only finance can record payments.")
        if not self.can_be_paid_by(actor):
            raise InvalidState("Only approved requests can be paid.")
        if not invoice_reference.strip() or amount.amount <= 0:
            raise InvalidRequest("An invoice reference and a positive amount are required.")
        if any(p.invoice_reference == invoice_reference.strip() for p in self.payments):
            raise InvalidRequest("That invoice has already been recorded against this request.")

        amount_usd = amount.in_usd(usd_rate)
        if self.paid_usd + amount_usd > self.approved_total_usd and not confirm_over_approval:
            raise InvalidRequest(
                "This payment takes the total paid to more than was approved. "
                "Check the invoice, and confirm if it is right."
            )

        payment = Payment(
            invoice_reference=invoice_reference.strip(),
            amount=amount.amount,
            currency=amount.currency,
            amount_usd=amount_usd,
            paid_on=paid_on,
            recorded_by_id=actor.id,
            recorded_at=now,
        )
        self.payments.append(payment)
        over_by = self.paid_usd - self.approved_total_usd
        self._record(
            events.PaymentRecorded(
                self.id,
                actor.id,
                invoice_reference=payment.invoice_reference,
                amount=str(amount),
                amount_usd=payment.amount_usd,
                over_approval_usd=over_by if over_by > 0 else None,
            )
        )
        return payment

    def snapshot(self) -> dict:
        return {
            "title": self.title,
            "supplier": self.supplier,
            "category_id": str(self.category_id),
            "tag_id": str(self.tag_id) if self.tag_id else None,
            "amount": str(self.unit_price),
            "usd_rate": str(self.usd_rate),
            "billing_period": self.billing_period.value if self.billing_period else None,
            "term_months": self.term_months,
            "total_usd": str(self.total_usd),
            "quote_sha256": [quote.sha256 for quote in self.quotes],
            "no_quote_reason": self.no_quote_reason,
            "confidential": self.is_confidential,
        }

    def hand_over_approvals(
        self, from_person: Employee, to_person: Employee, *, actor: Employee, now: datetime
    ) -> None:
        if to_person.id == self.requester_id:
            raise NotPermitted(
                f"{to_person.first_name} raised {self.reference} and can't approve it."
            )
        if not to_person.is_active:
            raise NotPermitted(f"{to_person.first_name} has left and can't take over approvals.")
        if self._approved_by(to_person):
            raise NotPermitted(
                f"{to_person.first_name} has already approved {self.reference}. "
                "Choose someone else."
            )
        handed_over = self._open_steps_for(from_person)
        if not handed_over:
            raise InvalidRequest(
                f"{from_person.first_name} has nothing waiting on {self.reference}."
            )

        asked_now = self.pending_step in handed_over
        for step in handed_over:
            step.approver_id = to_person.id
        self._merge_steps_held_by(to_person)
        self._record(
            events.ApprovalsHandedOver(
                self.id, actor.id, from_id=from_person.id, to_id=to_person.id
            )
        )
        if asked_now:
            self._activate(self.pending_step, now)

    def is_waiting_on(self, approver: Employee) -> bool:
        return bool(self._open_steps_for(approver))

    def remind_approver(self, now: datetime, after: timedelta) -> bool:
        step = self.pending_step
        if step is None or now - (step.reminded_at or step.activated_at) < after:
            return False
        step.reminded_at = now
        self._record(
            events.ApprovalReminded(
                self.id, None, approver_id=step.approver_id, stage_label=step.stage_label
            )
        )
        return True

    def pull_events(self) -> list[events.DomainEvent]:
        recorded, self._events = self._recorded_events(), []
        return recorded

    def _record(self, event: events.DomainEvent) -> None:
        self._events = [*self._recorded_events(), event]

    def _recorded_events(self) -> list[events.DomainEvent]:
        # Not a dataclass field: the ORM rebuilds aggregates without calling __init__.
        return getattr(self, "_events", [])

    def _open_steps_for(self, approver: Employee) -> list[ApprovalStep]:
        return [s for s in self.current_steps if s.is_open and s.approver_id == approver.id]

    def _merge_steps_held_by(self, approver: Employee) -> None:
        first, *repeats = self._open_steps_for(approver)
        for step in repeats:
            first.stages = [*first.stages, *step.stages]
            self.steps.remove(step)

    def _activate(self, step: ApprovalStep, now: datetime) -> None:
        step.status = StepStatus.PENDING
        step.activated_at = now
        step.reminded_at = None
        self._record(
            events.ApprovalRequested(
                self.id, None, approver_id=step.approver_id, stage_label=step.stage_label
            )
        )

    def _step_for_decision(
        self, actor: Employee, now: datetime, covers_held: Iterable[Cover]
    ) -> ApprovalStep:
        if self.pending_step is None:
            raise InvalidState("This request is not waiting for a decision.")
        if not self.can_be_decided_by(actor, now.date(), covers_held):
            raise NotPermitted("You cannot decide this request.")
        return self.pending_step

    @staticmethod
    def _close_step(
        step: ApprovalStep, actor: Employee, status: StepStatus, comment: str, now: datetime
    ) -> None:
        step.status = status
        step.decided_by_id = actor.id
        step.decided_at = now
        step.comment = comment.strip()

    def _cancel_open_steps(self) -> None:
        for step in self.current_steps:
            if step.is_open:
                step.status = StepStatus.CANCELLED


def _detail_fields(details: SpendDetails, usd_rate: Decimal) -> dict:
    return {
        "title": details.title.strip(),
        "supplier": details.supplier.strip(),
        "justification": details.justification.strip(),
        "category_id": details.category_id,
        "tag_id": details.tag_id,
        "amount": details.amount.amount,
        "currency": details.amount.currency,
        "usd_rate": usd_rate,
        "billing_period": details.billing_period,
        "term_months": details.term_months if details.billing_period else None,
        "no_quote_reason": details.no_quote_reason.strip(),
        "is_confidential": details.is_confidential,
    }
