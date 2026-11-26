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
    events.StepApproved: "approved",
    events.RequestApproved: "fully_approved",
    events.RequestRejected: "rejected",
    events.RequestWithdrawn: "withdrawn",
    events.RequestRevised: "revised",
    events.PaymentRecorded: "payment_recorded",
}


def handle(uow: UnitOfWork, request: SpendRequest, now: datetime) -> None:
    for event in request.pull_events():
        _audit(uow, event, now)
        _notify(uow, request, event, now)


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
    request_id: UUID | None,
    now: datetime,
) -> None:
    uow.outbox.add(OutgoingNotification(recipient_id, subject, body, request_id, now))


def _audit(uow: UnitOfWork, event: events.DomainEvent, now: datetime) -> None:
    details = asdict(event)
    details.pop("request_id")
    details.pop("actor_id")
    on_behalf_of_id = details.pop("on_behalf_of_id", None)
    audit(
        uow,
        _AUDIT_ACTIONS[type(event)],
        now,
        actor_id=event.actor_id,
        request_id=event.request_id,
        on_behalf_of_id=on_behalf_of_id,
        **details,
    )


def _notify(
    uow: UnitOfWork, request: SpendRequest, event: events.DomainEvent, now: datetime
) -> None:
    requester = uow.people.get(request.requester_id)
    actor = uow.people.get(event.actor_id) if event.actor_id else None
    label = f"{request.title} ({request.reference})"

    def send(recipient_id: UUID, subject: str, body: str) -> None:
        notify(uow, recipient_id, subject, body, request.id, now)

    match event:
        case events.ApprovalRequested(approver_id=approver_id, stage_label=stage_label):
            body = (
                f"{requester.name} wants to spend {request.total} with {request.supplier}. "
                f"You are approving as {stage_label}."
            )
            send(approver_id, f"Approval needed: {label}", body)
            for cover in uow.covers.active_on(now.date()):
                if cover.absent_id == approver_id and cover.covering_id != request.requester_id:
                    absent = uow.people.get(approver_id)
                    send(
                        cover.covering_id,
                        f"Approval needed: {label}",
                        f"{body}\nYou are covering for {absent.name}.",
                    )

        case events.RequestApproved():
            send(
                request.requester_id,
                f"Approved: {label}",
                "Your request is fully approved. You can go ahead.",
            )
            for person in uow.people.active():
                if person.has_role(Role.FINANCE):
                    send(
                        person.id,
                        f"Approved spend to expect: {request.reference}",
                        f"{request.supplier}, USD {request.total_usd:,.2f}, raised by "
                        f"{requester.name}. Match the invoice to {request.reference}.",
                    )

        case events.RequestRejected(reason=reason):
            send(
                request.requester_id,
                f"Not approved: {label}",
                f"{actor.name} did not approve this.\nReason: {reason}",
            )

        case events.RequestRevised(needs_reapproval=False, reason=reason):
            for person in uow.people.active():
                if person.has_role(Role.FINANCE):
                    send(
                        person.id,
                        f"Approved request changed: {request.reference}",
                        f"{requester.name} changed {request.title}. The new total of USD "
                        f"{request.total_usd:,.2f} is within the approval.\nReason: {reason}",
                    )

        case events.PaymentRecorded(over_approval_usd=over) if over:
            for person in uow.people.active():
                if person.has_role(Role.EXECUTIVE_APPROVER):
                    send(
                        person.id,
                        f"Paid above approval: {request.reference}",
                        f"Payments on {label} are USD {over:,.2f} above what was approved.",
                    )
