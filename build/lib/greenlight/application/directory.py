from greenlight.application.ports import People
from greenlight.domain.people import Employee, Role


class PeopleDirectory:
    def __init__(self, people: People):
        self._people = people

    def line_manager_of(self, employee: Employee) -> Employee | None:
        return self._people.get(employee.manager_id) if employee.manager_id else None

    def department_head_of(self, employee: Employee) -> Employee | None:
        department = next(
            (d for d in self._people.departments() if d.id == employee.department_id), None
        )
        if department is None or department.head_id is None:
            return None
        return self._people.get(department.head_id)

    def it_approver(self) -> Employee | None:
        return self._first_with(Role.IT_APPROVER)

    def executive_approver(self) -> Employee | None:
        return self._first_with(Role.EXECUTIVE_APPROVER)

    def _first_with(self, role: Role) -> Employee | None:
        return next(iter(self._people.with_role(role)), None)
