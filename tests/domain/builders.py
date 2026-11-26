from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from greenlight.domain.approval_policy import ApprovalPolicy
from greenlight.domain.catalog import Category
from greenlight.domain.money import Money
from greenlight.domain.people import Department, Employee, Role
from greenlight.domain.spend_request import Quote, SpendDetails, SpendRequest

NOW = datetime(2026, 11, 26, 9, 0, tzinfo=UTC)
TODAY = NOW.date()


@dataclass
class Org:
    people: dict[UUID, Employee] = field(default_factory=dict)
    departments: dict[UUID, Department] = field(default_factory=dict)

    def hire(self, name: str, *, manager: Employee | None = None, roles=(), **extra) -> Employee:
        email = name.lower().replace(" ", ".") + "@orvane.example"
        person = Employee(
            email=email,
            name=name,
            manager_id=manager.id if manager else None,
            roles=set(roles),
            **extra,
        )
        self.people[person.id] = person
        return person

    def line_manager_of(self, employee: Employee) -> Employee | None:
        return self.people.get(employee.manager_id) if employee.manager_id else None

    def department_head_of(self, employee: Employee) -> Employee | None:
        department = self.departments.get(employee.department_id)
        return self.people.get(department.head_id) if department and department.head_id else None

    def it_approver(self) -> Employee | None:
        return self._with_role(Role.IT_APPROVER)

    def executive_approver(self) -> Employee | None:
        return self._with_role(Role.EXECUTIVE_APPROVER)

    def _with_role(self, role: Role) -> Employee | None:
        return next((p for p in self.people.values() if p.has_role(role) and p.is_active), None)


@dataclass
class Company:
    """A small Orvane: CEO, COO Nadia, IT lead Daniel, finance head Tom and a few staff."""

    org: Org
    ceo: Employee
    nadia: Employee
    daniel: Employee
    tom: Employee
    priya: Employee
    sam: Employee
    ana: Employee
    general: Category
    software: Category
    travel: Category
    policy: ApprovalPolicy


def build_company() -> Company:
    org = Org()
    ceo = org.hire("Elena Moss")
    nadia = org.hire("Nadia Frost", manager=ceo, roles=[Role.EXECUTIVE_APPROVER])
    daniel = org.hire("Daniel Reyes", manager=nadia, roles=[Role.IT_APPROVER])
    tom = org.hire("Tom Becker", manager=nadia, roles=[Role.FINANCE])
    priya = org.hire("Priya Shah", manager=nadia)
    sam = org.hire("Sam Okafor", manager=priya)
    ana = org.hire("Ana Lima", manager=daniel)

    general = Category("Professional services")
    software = Category("Software and subscriptions", requires_it_review=True)
    travel = Category("Travel")
    policy = ApprovalPolicy(
        executive_threshold_usd=Decimal("5000"),
        category_thresholds_usd={travel.id: Decimal("10000")},
    )
    return Company(org, ceo, nadia, daniel, tom, priya, sam, ana, general, software, travel, policy)


def details(category: Category, amount: str = "1200", currency: str = "USD", **overrides):
    values = {
        "title": "Annual licence",
        "supplier": "Acme Ltd",
        "justification": "Needed for the finance system rollout",
        "category_id": category.id,
        "amount": Money(Decimal(amount), currency),
    }
    values.update(overrides)
    return SpendDetails(**values)


def a_quote(uploaded_by: Employee, sha: str = "a" * 64) -> Quote:
    return Quote(
        filename="quote.pdf",
        content_type="application/pdf",
        size_bytes=1024,
        sha256=sha,
        storage_key=f"quotes/{sha}",
        uploaded_by_id=uploaded_by.id,
        uploaded_at=NOW,
    )


def submitted(
    company: Company,
    requester: Employee,
    category: Category,
    amount: str = "1200",
    usd_rate: str = "1",
    **overrides,
) -> SpendRequest:
    request = SpendRequest.submit(
        reference="GL-00001",
        requester=requester,
        details=details(category, amount, **overrides),
        usd_rate=Decimal(usd_rate),
        quotes=[a_quote(requester)],
        now=NOW,
    )
    route = company.policy.plan(requester, category, request.total_usd, company.org)
    request.start_approval(route, NOW)
    return request
