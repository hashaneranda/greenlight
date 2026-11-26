from dataclasses import asdict
from datetime import datetime
from uuid import UUID

from greenlight.application.ports import AuditEntry, OutgoingNotification, UnitOfWork
from greenlight.domain import events
from greenlight.domain.people import Role
from greenlight.domain.spend_request import SpendRequest

_AUDIT_ACTIONS = {
    events.RequestSubmitted: "submitted",
    events.ApprovalRouteSet: "route_set",
    events.ApprovalRequested: "approval_requested",
    events.ApprovalReminded: "reminder_sent",
    events.ApprovalsHandedOver: "approvals_handed_over",
    events.StepApproved: "approved",
    events.RequestApproved: "fully_approved",
    events.RequestRejected: "rejected",
    events.RequestWithdrawn: "withdrawn",
    events.RequestRevised: "revised",
    events.PaymentRecorded: "payment_recorded",
}


def audit(
    uow: UnitOfWork,
    action: str,
    now: datetime,
    *,
    actor_id: UUID | None,
    request_id: UUID | None = None,
    on_behalf_of_id: UUID | None = None,
    **details,
) -> None:
    uow.audit.append(
        AuditEntry(
            action=action,
            occurred_at=now,
            actor_id=actor_id,
            request_id=request_id,
            on_behalf_of_id=on_behalf_of_id,
            details=details,
        )
    )


def notify(
    uow: UnitOfWork,
    recipient_id: UUID,
    subject: str,
    body: str,
    now: datetime,
    request_id: UUID | None = None,
) -> None:
    uow.outbox.add(OutgoingNotification(recipient_id, subject, body, request_id, now))


def handle(uow: UnitOfWork, request: SpendRequest, now: datetime) -> None:
    recorded = request.pull_events()
    if not recorded:
        return
    notifier = _RequestNotifier(uow, request, now)
    for event in recorded:
        _audit_event(uow, event, now)
        notifier.on(event)


def _audit_event(uow: UnitOfWork, event: events.DomainEvent, now: datetime) -> None:
    details = asdict(event)
    del details["request_id"], details["actor_id"]
    audit(
        uow,
        _AUDIT_ACTIONS[type(event)],
        now,
        actor_id=event.actor_id,
        request_id=event.request_id,
        on_behalf_of_id=details.pop("on_behalf_of_id", None),
        **details,
    )


class _RequestNotifier:
    def __init__(self, uow: UnitOfWork, request: SpendRequest, now: datetime):
        self._uow = uow
        self._request = request
        self._now = now
        self._requester = uow.people.get(request.requester_id)
        self._label = f"{request.title} ({request.reference})"

    def on(self, event: events.DomainEvent) -> None:
        request = self._request
        match event:
            case events.ApprovalRequested(approver_id=approver_id, stage_label=stage):
                self._ask_for_approval(approver_id, stage, "Approval needed")

            case events.ApprovalReminded(approver_id=approver_id, stage_label=stage):
                self._ask_for_approval(approver_id, stage, "Reminder, still waiting for you")

            case events.RequestApproved():
                self._send(
                    request.requester_id,
                    f"Approved: {self._label}",
                    "Your request is fully approved. You can go ahead.",
                )
                self._send_to_role(
                    Role.FINANCE,
                    f"Approved spend to expect: {request.reference}",
                    f"{request.supplier}, USD {request.total_usd:,.2f}, raised by "
                    f"{self._requester.name}. Match the invoice to {request.reference}.",
                )

            case events.RequestRejected(actor_id=actor_id, reason=reason):
                decider = self._uow.people.get(actor_id)
                self._send(
                    request.requester_id,
                    f"Not approved: {self._label}",
                    f"{decider.name} did not approve this.\nReason: {reason}",
                )

            case events.RequestRevised(needs_reapproval=False, reason=reason):
                self._send_to_role(
                    Role.FINANCE,
                    f"Approved request changed: {request.reference}",
                    f"{self._requester.name} changed {request.title}. The new total of USD "
                    f"{request.total_usd:,.2f} is within the approval.\nReason: {reason}",
                )

            case events.PaymentRecorded(over_approval_usd=over) if over:
                self._send_to_role(
                    Role.EXECUTIVE_APPROVER,
                    f"Paid above approval: {request.reference}",
                    f"Payments on {self._label} are USD {over:,.2f} above what was approved.",
                )

    def _ask_for_approval(self, approver_id: UUID, stage: str, heading: str) -> None:
        request = self._request
        subject = f"{heading}: {self._label}"
        body = (
            f"{self._requester.name} wants to spend {request.total} with {request.supplier}. "
            f"You are approving as {stage}."
        )
        self._send(approver_id, subject, body)

        absent = self._uow.people.get(approver_id)
        today = self._now.date()
        for cover in self._uow.covers.for_absent(approver_id):
            covering = self._uow.people.get(cover.covering_id)
            if request.can_be_decided_by(covering, today, [cover]):
                self._send(covering.id, subject, f"{body}\nYou are covering for {absent.name}.")

    def _send(self, recipient_id: UUID, subject: str, body: str) -> None:
        notify(self._uow, recipient_id, subject, body, self._now, self._request.id)

    def _send_to_role(self, role: Role, subject: str, body: str) -> None:
        for person in self._uow.people.with_role(role):
            self._send(person.id, subject, body)
