import enum
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from greenlight.domain.errors import NotPermitted


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
    def sees_all_spend(self) -> bool:
        return bool(self.roles & {Role.FINANCE, Role.EXECUTIVE_APPROVER, Role.ADMIN})

    @property
    def first_name(self) -> str:
        return self.name.split()[0]

    @property
    def initials(self) -> str:
        return "".join(part[0] for part in self.name.split()[:2]).upper()


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
