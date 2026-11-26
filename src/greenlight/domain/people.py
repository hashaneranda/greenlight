import enum
from dataclasses import dataclass, field
from uuid import UUID, uuid4


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
    id: UUID = field(default_factory=uuid4)

    def has_role(self, role: Role) -> bool:
        return role in self.roles

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
