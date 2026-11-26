import enum
from collections.abc import Iterable
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from greenlight.domain.errors import InvalidRequest, NotPermitted


class Role(enum.StrEnum):
    EXECUTIVE_APPROVER = "executive_approver"
    IT_APPROVER = "it_approver"
    FINANCE = "finance"
    ADMIN = "admin"


@dataclass(eq=False)
class Employee:
    email: str
    name: str
    job_title: str = ""
    department_id: UUID | None = None
    site_id: UUID | None = None
    manager_id: UUID | None = None
    roles: set[Role] = field(default_factory=set)
    is_active: bool = True
    microsoft_object_id: str | None = None
    id: UUID = field(default_factory=uuid4)

    def link_microsoft_account(self, object_id: str) -> None:
        if not object_id:
            raise NotPermitted("Microsoft did not say which account signed in.")
        if self.microsoft_object_id not in (None, object_id):
            raise NotPermitted(
                f"{self.email} is linked to a different Microsoft account. Ask IT to check it."
            )
        self.microsoft_object_id = object_id

    def has_role(self, role: Role) -> bool:
        return role in self.roles

    @property
    def can_record_payments(self) -> bool:
        return Role.FINANCE in self.roles

    @property
    def can_read_audit_log(self) -> bool:
        return bool(self.roles & {Role.FINANCE, Role.ADMIN})

    @property
    def can_change_rules(self) -> bool:
        return bool(self.roles & {Role.EXECUTIVE_APPROVER, Role.ADMIN})

    @property
    def can_manage_people(self) -> bool:
        return Role.ADMIN in self.roles

    @property
    def can_manage_reference_data(self) -> bool:
        return Role.FINANCE in self.roles

    @property
    def sees_all_spend(self) -> bool:
        return bool(self.roles & {Role.FINANCE, Role.EXECUTIVE_APPROVER, Role.ADMIN})

    @property
    def first_name(self) -> str:
        return self.name.split()[0]

    @property
    def initials(self) -> str:
        return "".join(part[0] for part in self.name.split()[:2]).upper()


_APPROVER_ROLES = {
    Role.EXECUTIVE_APPROVER: "COO approver",
    Role.IT_APPROVER: "IT approver",
}


def check_reporting_line(
    person: Employee, manager_id: UUID | None, everyone: Iterable[Employee]
) -> None:
    if manager_id == person.id:
        raise InvalidRequest(f"{person.first_name} can't report to themselves.")
    by_id = {someone.id: someone for someone in everyone}
    line = [person]
    manager = by_id.get(manager_id)
    while manager is not None and manager not in line[1:]:
        line.append(manager)
        if manager.id == person.id:
            names = " → ".join(someone.first_name for someone in line)
            raise InvalidRequest(f"That would make a loop: {names}.")
        manager = by_id.get(manager.manager_id)


def check_approvers_remain(everyone: Iterable[Employee], without: Employee) -> None:
    others = [someone for someone in everyone if someone.is_active and someone.id != without.id]
    for role, title in _APPROVER_ROLES.items():
        if without.has_role(role) and not any(someone.has_role(role) for someone in others):
            raise InvalidRequest(
                f"{without.first_name} is the only active {title}. "
                "Give the role to someone else first."
            )


@dataclass(eq=False)
class Department:
    name: str
    head_id: UUID | None = None
    id: UUID = field(default_factory=uuid4)


@dataclass(eq=False)
class Site:
    name: str
    country: str
    local_currency: str
    id: UUID = field(default_factory=uuid4)
