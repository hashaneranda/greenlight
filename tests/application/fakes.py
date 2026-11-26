from datetime import date, datetime
from decimal import Decimal

from greenlight.application import audit_chain
from greenlight.application.ports import AuditEntry, OutgoingNotification
from greenlight.domain.approval_policy import ApprovalPolicy
from greenlight.domain.catalog import Category, Tag
from greenlight.domain.cover import Cover
from greenlight.domain.invoice_matching import Invoice
from greenlight.domain.people import Department, Employee, Site


class InMemoryRequests:
    def __init__(self):
        self.items = {}

    def add(self, request):
        self.items[request.id] = request

    def get(self, request_id):
        return self.items.get(request_id)

    def by_reference(self, reference):
        return next((r for r in self.items.values() if r.reference == reference), None)

    def next_reference(self):
        return f"GL-{len(self.items) + 1:05d}"

    def all(self):
        return list(self.items.values())


class InMemoryPeople:
    def __init__(self, people: list[Employee], departments: list[Department] = ()):
        self.items = {p.id: p for p in people}
        self._departments = list(departments)

    def get(self, employee_id):
        return self.items.get(employee_id)

    def by_email(self, email):
        return next((p for p in self.items.values() if p.email == email), None)

    def active(self):
        return [p for p in self.items.values() if p.is_active]

    def all(self):
        return list(self.items.values())

    def add(self, employee):
        self.items[employee.id] = employee

    def departments(self) -> list[Department]:
        return self._departments

    def sites(self) -> list[Site]:
        return []


class InMemoryCatalog:
    def __init__(self, categories: list[Category], policy: ApprovalPolicy, rates: dict):
        self._categories = {c.id: c for c in categories}
        self._tags: dict = {}
        self._policy = policy
        self._rates = {"USD": Decimal("1"), **rates}

    def categories(self):
        return list(self._categories.values())

    def category(self, category_id):
        return self._categories.get(category_id)

    def tags(self):
        return list(self._tags.values())

    def tag(self, tag_id):
        return self._tags.get(tag_id)

    def add_tag(self, tag: Tag):
        self._tags[tag.id] = tag

    def approval_policy(self):
        return self._policy

    def usd_rate(self, currency):
        return self._rates.get(currency)

    def currencies(self):
        return sorted(self._rates)


class InMemoryCovers:
    def __init__(self):
        self.items: dict = {}

    def add(self, cover: Cover):
        self.items[cover.id] = cover

    def get(self, cover_id):
        return self.items.get(cover_id)

    def for_absent(self, employee_id):
        return [c for c in self.items.values() if c.absent_id == employee_id]

    def active_on(self, day: date):
        return [c for c in self.items.values() if c.is_active_on(day)]


class InMemoryAudit:
    def __init__(self):
        self.entries: list[AuditEntry] = []

    def append(self, entry: AuditEntry):
        previous = self.entries[-1].hash if self.entries else None
        self.entries.append(audit_chain.seal(entry, previous))

    def for_request(self, request_id):
        return [e for e in self.entries if e.request_id == request_id]

    def all(self):
        return list(self.entries)


class InMemoryOutbox:
    def __init__(self):
        self.sent: list[OutgoingNotification] = []

    def add(self, notification):
        self.sent.append(notification)


class FakeUnitOfWork:
    def __init__(self, people, catalog):
        self.requests = InMemoryRequests()
        self.people = people
        self.catalog = catalog
        self.covers = InMemoryCovers()
        self.audit = InMemoryAudit()
        self.outbox = InMemoryOutbox()
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        pass

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


class MemoryStorage:
    def __init__(self):
        self.files = {}

    def save(self, key, content, content_type):
        self.files[key] = content

    def load(self, key):
        return self.files[key]


class FixedClock:
    def __init__(self, moment: datetime):
        self.moment = moment

    def now(self):
        return self.moment


class CannedInvoiceReader:
    def __init__(self, invoice: Invoice):
        self.invoice = invoice

    def read(self, content, content_type):
        return self.invoice
