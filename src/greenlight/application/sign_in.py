from collections.abc import Callable

from greenlight.application.ports import UnitOfWork
from greenlight.domain.people import Employee


class MicrosoftSignIn:
    """Matches a Microsoft account to an employee by email the first time, then by account id."""

    def __init__(self, unit_of_work: Callable[[], UnitOfWork]):
        self._unit_of_work = unit_of_work

    def employee_for(self, email: str, object_id: str) -> Employee | None:
        with self._unit_of_work() as uow:
            person = uow.people.by_email(email)
            if person is None or not person.is_active:
                return None
            person.link_microsoft_account(object_id)
            uow.commit()
            return person
