from decimal import Decimal
from uuid import UUID

from greenlight.application.errors import NotFound
from greenlight.application.ports import UnitOfWork
from greenlight.domain.errors import InvalidRequest, NotPermitted
from greenlight.domain.people import Employee
from greenlight.domain.spend_request import RequestStatus, SpendRequest


def active_person(uow: UnitOfWork, employee_id: UUID) -> Employee:
    person = uow.people.get(employee_id)
    if person is None or not person.is_active:
        raise NotPermitted("Your account is not active in Greenlight.")
    return person


def request_by_reference(
    uow: UnitOfWork, reference: str, *, for_update: bool = False
) -> SpendRequest:
    request = uow.requests.by_reference(reference.upper(), for_update=for_update)
    if request is None:
        raise NotFound(f"No request {reference}.")
    return request


def approvals_waiting_on(uow: UnitOfWork, approver: Employee) -> list[SpendRequest]:
    pending = uow.requests.with_status(RequestStatus.PENDING)
    return [request for request in pending if request.is_waiting_on(approver)]


def usd_rate(uow: UnitOfWork, currency: str) -> Decimal:
    rate = uow.catalog.usd_rate(currency)
    if rate is None:
        raise InvalidRequest(f"Greenlight has no exchange rate for {currency}.")
    return rate
