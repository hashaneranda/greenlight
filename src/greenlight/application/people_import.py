import csv
import io
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from uuid import UUID

from greenlight.application.loading import approvals_waiting_on
from greenlight.application.ports import UnitOfWork
from greenlight.domain.errors import InvalidRequest
from greenlight.domain.people import (
    Employee,
    check_approvers_remain,
    check_reporting_line,
    pass_reports_up,
)

COLUMNS = ("email", "name", "job_title", "department", "site", "manager_email", "active")
_ACTIVE = {"yes": True, "no": False}
_IMPORTED_FIELDS = ("name", "job_title", "department_id", "site_id", "manager_id", "is_active")


@dataclass(frozen=True)
class RowError:
    row: int
    message: str


@dataclass(frozen=True)
class Change:
    field: str
    before: str
    after: str


@dataclass(frozen=True)
class PersonChanges:
    person: Employee
    changes: list[Change]


@dataclass(frozen=True)
class ImportPreview:
    adds: list[Employee]
    changes: list[PersonChanges]
    deactivations: list[Employee]
    errors: list[RowError]


class PeopleImport:
    """Everyone as they will be once a people CSV is applied, checked as a whole first."""

    def __init__(self, uow: UnitOfWork, csv_text: str):
        self._uow = uow
        self._current = {person.email.lower(): person for person in uow.people.all()}
        self._after = {email: replace(person) for email, person in self._current.items()}
        departments, sites = uow.people.departments(), uow.people.sites()
        self._departments = {d.name.lower(): d.id for d in departments}
        self._sites = {s.name.lower(): s.id for s in sites}
        self._names = {place.id: place.name for place in [*departments, *sites]}
        self._row_of: dict[str, int] = {}
        self._errors: list[RowError] = []

        staged = []
        for number, row in self._rows(csv_text):
            with self._on_row(number):
                staged.append((number, self._stage(number, row), row["manager_email"]))
        for number, person, manager_email in staged:
            with self._on_row(number):
                self._link_manager(person, manager_email)
        self._check_leavers()
        self._check_reporting_lines()

    @property
    def preview(self) -> ImportPreview:
        leavers = self._leavers()
        return ImportPreview(
            adds=self._newcomers(),
            changes=[
                PersonChanges(self._current[email], changes)
                for email, changes in self._changes().items()
                if email not in leavers
            ],
            deactivations=[self._current[email] for email in leavers],
            errors=sorted(self._errors, key=lambda error: error.row),
        )

    def apply(self) -> None:
        for newcomer in _managers_first(self._newcomers()):
            self._uow.people.add(newcomer)
        for email in self._changes():
            current, after = self._current[email], self._after[email]
            for name in _IMPORTED_FIELDS:
                setattr(current, name, getattr(after, name))

    def _rows(self, csv_text: str) -> list[tuple[int, dict[str, str]]]:
        reader = csv.DictReader(io.StringIO(csv_text), restval="")
        if not set(COLUMNS) <= set(reader.fieldnames or ()):
            self._errors.append(RowError(1, f"The first row must be: {','.join(COLUMNS)}"))
            return []
        return list(enumerate(reader, start=2))

    @contextmanager
    def _on_row(self, number: int) -> Iterator[None]:
        try:
            yield
        except InvalidRequest as error:
            self._errors.append(RowError(number, str(error)))

    def _stage(self, number: int, row: dict[str, str]) -> Employee:
        email = row["email"].strip().lower()
        if "@" not in email:
            raise InvalidRequest("Enter a work email.")
        if email in self._row_of:
            raise InvalidRequest(f"{email} is already on row {self._row_of[email]}.")
        name = row["name"].strip()
        if not name:
            raise InvalidRequest(f"Add a name for {email}.")
        department_id = _named(self._departments, row["department"], "department")
        site_id = _named(self._sites, row["site"], "site")
        is_active = _ACTIVE.get(row["active"].strip().lower())
        if is_active is None:
            raise InvalidRequest("Active must be yes or no.")

        self._row_of[email] = number
        person = self._after.setdefault(email, Employee(email=email, name=name))
        person.name = name
        person.job_title = row["job_title"].strip()
        person.department_id = department_id
        person.site_id = site_id
        person.is_active = is_active
        return person

    def _link_manager(self, person: Employee, manager_email: str) -> None:
        manager_email = manager_email.strip().lower()
        if not manager_email:
            person.manager_id = None
            return
        manager = self._after.get(manager_email)
        if manager is None:
            raise InvalidRequest(f"{manager_email} isn't anyone in Greenlight or in this file.")
        if not manager.is_active:
            raise InvalidRequest(f"{manager.name} has left. Choose a manager who still works here.")
        person.manager_id = manager.id

    def _check_leavers(self) -> None:
        everyone = self._after.values()
        for email in self._leavers():
            leaver = self._after[email]
            with self._on_row(self._row_of[email]):
                waiting = len(approvals_waiting_on(self._uow, leaver))
                if waiting:
                    approvals, them = ("approval", "it") if waiting == 1 else ("approvals", "them")
                    raise InvalidRequest(
                        f"{leaver.first_name} has {waiting} {approvals} waiting. Deactivate them "
                        f"on their page to choose who takes {them} over."
                    )
                check_approvers_remain(everyone, without=leaver)
                pass_reports_up(leaver, everyone)

    def _check_reporting_lines(self) -> None:
        everyone = self._after.values()
        for email, number in self._row_of.items():
            person = self._after[email]
            with self._on_row(number):
                check_reporting_line(person, person.manager_id, everyone)

    def _leavers(self) -> list[str]:
        return [
            email
            for email, person in self._current.items()
            if person.is_active and not self._after[email].is_active
        ]

    def _newcomers(self) -> list[Employee]:
        return [person for email, person in self._after.items() if email not in self._current]

    def _changes(self) -> dict[str, list[Change]]:
        emails = {person.id: person.email for person in self._after.values()}
        changes = {}
        for email, current in self._current.items():
            before, after = self._shown(current, emails), self._shown(self._after[email], emails)
            if changed := [
                Change(name, before[name], after[name])
                for name in before
                if before[name] != after[name]
            ]:
                changes[email] = changed
        return changes

    def _shown(self, person: Employee, emails: dict[UUID, str]) -> dict[str, str]:
        return {
            "name": person.name,
            "job_title": person.job_title,
            "department": self._names.get(person.department_id, ""),
            "site": self._names.get(person.site_id, ""),
            "manager": emails.get(person.manager_id, ""),
            "active": "yes" if person.is_active else "no",
        }


def _managers_first(newcomers: list[Employee]) -> list[Employee]:
    """Newcomers ordered so each one's new manager is saved before them."""
    by_id = {person.id: person for person in newcomers}

    def depth(person: Employee) -> int:
        levels = 0
        while person.manager_id in by_id:
            person = by_id[person.manager_id]
            levels += 1
        return levels

    return sorted(newcomers, key=depth)


def _named(ids_by_name: dict[str, UUID], name: str, kind: str) -> UUID | None:
    name = name.strip()
    if not name:
        return None
    if name.lower() not in ids_by_name:
        raise InvalidRequest(f"There is no {kind} called {name}.")
    return ids_by_name[name.lower()]
