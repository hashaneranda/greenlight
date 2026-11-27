import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from uuid import UUID

from greenlight.application import event_handlers
from greenlight.application.errors import NotFound
from greenlight.application.loading import (
    active_person,
    approvals_waiting_on,
    request_by_reference,
)
from greenlight.application.people_import import ImportPreview, PeopleImport
from greenlight.application.ports import Clock, UnitOfWork
from greenlight.domain.catalog import Category
from greenlight.domain.errors import InvalidRequest, NotPermitted
from greenlight.domain.money import BASE_CURRENCY
from greenlight.domain.people import (
    Employee,
    Role,
    check_approvers_remain,
    check_reporting_line,
    pass_reports_up,
)

_DETAILS = ("email", "name", "job_title", "department_id", "site_id", "manager_id")


@dataclass(frozen=True)
class PersonForm:
    email: str
    name: str
    job_title: str = ""
    department_id: UUID | None = None
    site_id: UUID | None = None
    manager_id: UUID | None = None
    roles: frozenset[Role] = frozenset()


class Administration:
    def __init__(self, unit_of_work: Callable[[], UnitOfWork], clock: Clock):
        self._unit_of_work = unit_of_work
        self._clock = clock

    def add_person(self, actor_id: UUID, form: PersonForm) -> UUID:
        now = self._clock.now()
        form = _tidy(form)
        with self._unit_of_work() as uow:
            _people_manager(uow, actor_id)
            person = Employee(**_details(form), roles=set(form.roles))
            _check_form(uow, person, form)
            uow.people.add(person)
            event_handlers.audit(uow, "person_added", now, actor_id=actor_id, **_profile(person))
            uow.commit()
            return person.id

    def update_person(self, actor_id: UUID, person_id: UUID, form: PersonForm) -> None:
        now = self._clock.now()
        form = _tidy(form)
        with self._unit_of_work() as uow:
            _people_manager(uow, actor_id)
            person = _person(uow, person_id)
            _check_form(uow, person, form)
            if dropped := person.roles - form.roles:
                check_approvers_remain(uow.people.all(), without=replace(person, roles=dropped))

            before = _profile(person)
            for name, value in _details(form).items():
                setattr(person, name, value)
            person.roles = set(form.roles)
            event_handlers.audit(
                uow,
                "person_updated",
                now,
                actor_id=actor_id,
                person=person.email,
                **_changes(before, _profile(person)),
            )
            uow.commit()

    def deactivate_person(
        self, actor_id: UUID, person_id: UUID, hand_over_to_id: UUID | None
    ) -> None:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            actor = _people_manager(uow, actor_id)
            leaver = _person(uow, person_id)
            everyone = uow.people.all()
            check_approvers_remain(everyone, without=leaver)
            waiting = approvals_waiting_on(uow, leaver)
            taking_over = _taking_over(uow, leaver, len(waiting), hand_over_to_id)
            for request in waiting:
                locked = request_by_reference(uow, request.reference, for_update=True)
                locked.hand_over_approvals(leaver, taking_over, actor=actor, now=now)
                event_handlers.handle(uow, locked, now)

            moved = pass_reports_up(leaver, everyone)
            leaver.is_active = False
            event_handlers.audit(
                uow,
                "person_deactivated",
                now,
                actor_id=actor_id,
                person=leaver.email,
                handed_over=[request.reference for request in waiting],
                handed_over_to=taking_over.email if taking_over else None,
                reports_moved=[report.email for report in moved],
            )
            uow.commit()

    def preview_import(self, actor_id: UUID, csv_text: str) -> ImportPreview:
        with self._unit_of_work() as uow:
            _people_manager(uow, actor_id)
            return PeopleImport(uow, csv_text).preview

    def apply_import(self, actor_id: UUID, csv_text: str) -> ImportPreview:
        """Applies the whole file, or nothing when any row has an error."""
        now = self._clock.now()
        with self._unit_of_work() as uow:
            _people_manager(uow, actor_id)
            people_import = PeopleImport(uow, csv_text)
            preview = people_import.preview
            if preview.errors:
                return preview
            people_import.apply()
            event_handlers.audit(
                uow,
                "people_imported",
                now,
                actor_id=actor_id,
                added=[person.email for person in preview.adds],
                changed=[changed.person.email for changed in preview.changes],
                deactivated=[person.email for person in preview.deactivations],
            )
            uow.commit()
            return preview

    def add_category(self, actor_id: UUID, name: str, requires_it_review: bool) -> UUID:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            _reference_data_manager(uow, actor_id)
            category = Category.create(name, requires_it_review, uow.catalog.categories())
            uow.catalog.add_category(category)
            event_handlers.audit(
                uow,
                "category_added",
                now,
                actor_id=actor_id,
                name=category.name,
                requires_it_review=category.requires_it_review,
            )
            uow.commit()
            return category.id

    def update_category(
        self,
        actor_id: UUID,
        category_id: UUID,
        *,
        name: str,
        requires_it_review: bool,
        is_active: bool,
    ) -> None:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            _reference_data_manager(uow, actor_id)
            category = uow.catalog.category(category_id)
            if category is None:
                raise NotFound("That category no longer exists.")
            before = _category_profile(category)
            category.update(
                name=name,
                requires_it_review=requires_it_review,
                is_active=is_active,
                existing=uow.catalog.categories(),
            )
            event_handlers.audit(
                uow,
                "category_updated",
                now,
                actor_id=actor_id,
                category=category.name,
                **_changes(before, _category_profile(category)),
            )
            uow.commit()

    def set_rate(self, actor_id: UUID, currency: str, usd_per_unit: Decimal) -> None:
        now = self._clock.now()
        currency = currency.strip().upper()
        with self._unit_of_work() as uow:
            _reference_data_manager(uow, actor_id)
            if not re.fullmatch(r"[A-Z]{3}", currency):
                raise InvalidRequest("Enter the three-letter currency code, such as AED.")
            if currency == BASE_CURRENCY:
                raise InvalidRequest(
                    f"Amounts are kept in {BASE_CURRENCY}, so its rate is always 1."
                )
            if usd_per_unit <= 0:
                raise InvalidRequest("The rate must be more than zero.")
            rate_from = uow.catalog.usd_rate(currency)
            uow.catalog.set_usd_rate(currency, usd_per_unit, actor_id, now)
            event_handlers.audit(
                uow,
                "exchange_rate_set",
                now,
                actor_id=actor_id,
                currency=currency,
                rate_from=str(rate_from) if rate_from is not None else None,
                rate_to=str(usd_per_unit),
            )
            uow.commit()


def _people_manager(uow: UnitOfWork, actor_id: UUID) -> Employee:
    actor = active_person(uow, actor_id)
    if not actor.can_manage_people:
        raise NotPermitted("Only an admin can manage people.")
    return actor


def _reference_data_manager(uow: UnitOfWork, actor_id: UUID) -> None:
    if not active_person(uow, actor_id).can_manage_reference_data:
        raise NotPermitted("Only finance can change categories and exchange rates.")


def _person(uow: UnitOfWork, person_id: UUID) -> Employee:
    person = uow.people.get(person_id)
    if person is None:
        raise NotFound("That person is not in Greenlight.")
    return person


def _taking_over(
    uow: UnitOfWork, leaver: Employee, waiting: int, hand_over_to_id: UUID | None
) -> Employee | None:
    if not waiting:
        return None
    if hand_over_to_id is None:
        approvals, them = ("approval", "it") if waiting == 1 else ("approvals", "them")
        raise InvalidRequest(
            f"{leaver.first_name} has {waiting} {approvals} waiting. Choose who takes {them} over."
        )
    return _person(uow, hand_over_to_id)


def _tidy(form: PersonForm) -> PersonForm:
    return replace(
        form,
        email=form.email.strip().lower(),
        name=form.name.strip(),
        job_title=form.job_title.strip(),
    )


def _check_form(uow: UnitOfWork, person: Employee, form: PersonForm) -> None:
    if "@" not in form.email:
        raise InvalidRequest("Enter a work email.")
    if not form.name:
        raise InvalidRequest("Enter their name.")
    holder = uow.people.by_email(form.email)
    if holder is not None and holder.id != person.id:
        raise InvalidRequest(f"{holder.name} already uses {form.email}.")
    if form.manager_id is not None:
        manager = uow.people.get(form.manager_id)
        if manager is None or not manager.is_active:
            raise InvalidRequest("Choose a manager who still works here.")
    check_reporting_line(person, form.manager_id, uow.people.all())


def _details(source: Employee | PersonForm) -> dict:
    return {name: getattr(source, name) for name in _DETAILS}


def _profile(person: Employee) -> dict:
    return {**_details(person), "roles": sorted(person.roles)}


def _category_profile(category: Category) -> dict:
    return {
        "name": category.name,
        "requires_it_review": category.requires_it_review,
        "is_active": category.is_active,
    }


def _changes(before: dict, after: dict) -> dict[str, dict]:
    changed = [name for name in before if before[name] != after[name]]
    return {
        "before": {name: before[name] for name in changed},
        "after": {name: after[name] for name in changed},
    }
