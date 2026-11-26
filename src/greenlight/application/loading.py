from decimal import Decimal
from uuid import UUID

from greenlight.application.errors import NotFound
from greenlight.application.ports import UnitOfWork
from greenlight.domain.errors import InvalidRequest, NotPermitted
from greenlight.domain.people import Employee
from greenlight.domain.spend_request import SpendRequest


def active_person(uow: UnitOfWork, employee_id: UUID) -> Employee:
    person = uow.people.get(employee_id)
    if person is None or not person.is_active:
        raise NotPermitted("Your account is not active in Greenlight.")
    return person


def request_by_reference(uow: UnitOfWork, reference: str) -> SpendRequest:
    request = uow.requests.by_reference(reference.upper())
    if request is None:
        raise NotFound(f"No request {reference}.")
    return request


def usd_rate(uow: UnitOfWork, currency: str) -> Decimal:
    rate = uow.catalog.usd_rate(currency)
    if rate is None:
        raise InvalidRequest(f"Greenlight has no exchange rate for {currency}.")
    return rate
