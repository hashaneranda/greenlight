from datetime import date, datetime
from decimal import Decimal

from greenlight.application import audit_chain
from greenlight.application.ports import AuditEntry, OutgoingNotification, RateRow
from greenlight.domain.approval_policy import ApprovalPolicy
from greenlight.domain.catalog import Category, Tag
from greenlight.domain.cover import Cover
from greenlight.domain.invoice_matching import Invoice
from greenlight.domain.money import BASE_CURRENCY
from greenlight.domain.people import Department, Employee, Site
from tests.domain.builders import NOW


class InMemoryRequests:
    def __init__(self):
        self.items = {}

    def add(self, request):
        self.items[request.id] = request

    def get(self, request_id):
        return self.items.get(request_id)

    def by_reference(self, reference, *, for_update=False):
        return next((r for r in self.items.values() if r.reference == reference), None)

    def next_reference(self):
        return f"GL-{len(self.items) + 1:05d}"

    def all(self):
        return list(self.items.values())

    def raised_by(self, requester_id):
        return [r for r in self.items.values() if r.requester_id == requester_id]

    def with_status(self, status):
        return [r for r in self.items.values() if r.status == status]


class InMemoryPeople:
    def __init__(
        self, people: list[Employee], departments: list[Department] = (), sites: list[Site] = ()
    ):
        self.items = {p.id: p for p in people}
        self._departments = list(departments)
        self._sites = list(sites)

    def get(self, employee_id):
        return self.items.get(employee_id)

    def by_email(self, email):
        return next((p for p in self.items.values() if p.email.lower() == email.lower()), None)

    def active(self):
        return [p for p in self.items.values() if p.is_active]

    def all(self):
        return list(self.items.values())

    def with_role(self, role):
        return [p for p in self.active() if p.has_role(role)]

    def add(self, employee):
        self.items[employee.id] = employee

    def departments(self) -> list[Department]:
        return self._departments

    def sites(self) -> list[Site]:
        return self._sites


class InMemoryCatalog:
    def __init__(self, categories: list[Category], policy: ApprovalPolicy, rates: dict):
        self._categories = {c.id: c for c in categories}
        self._tags: dict = {}
        self._policy = policy
        self._rates = {code: RateRow(code, rate, NOW, None) for code, rate in rates.items()}

    def categories(self):
        return list(self._categories.values())

    def category(self, category_id):
        return self._categories.get(category_id)

    def add_category(self, category: Category):
        self._categories[category.id] = category

    def tags(self):
        return list(self._tags.values())

    def tag(self, tag_id):
        return self._tags.get(tag_id)

    def add_tag(self, tag: Tag):
        self._tags[tag.id] = tag

    def approval_policy(self):
        return self._policy

    def set_executive_threshold(self, category_id, amount_usd):
        self._policy.category_thresholds_usd[category_id] = amount_usd

    def usd_rate(self, currency):
        if currency == BASE_CURRENCY:
            return Decimal("1")
        rate = self._rates.get(currency)
        return rate.usd_per_unit if rate else None

    def rates(self):
        return sorted(self._rates.values(), key=lambda rate: rate.currency)

    def set_usd_rate(self, currency, usd_per_unit, by_id, at):
        self._rates[currency] = RateRow(currency, usd_per_unit, at, by_id)

    def currencies(self):
        return [BASE_CURRENCY, *sorted(self._rates)]


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

    def held_by(self, covering_id, day: date):
        return [c for c in self.active_on(day) if c.covering_id == covering_id]


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

    def for_recipient(self, recipient_id, limit=50):
        return self._sent_to(recipient_id)[:limit]

    def unread_count(self, recipient_id):
        return sum(1 for n in self._sent_to(recipient_id) if n.read_at is None)

    def mark_all_read(self, recipient_id, now):
        for notification in self._sent_to(recipient_id):
            notification.read_at = notification.read_at or now

    def _sent_to(self, recipient_id):
        return [n for n in reversed(self.sent) if n.recipient_id == recipient_id]


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
        if key not in self.files:
            raise FileNotFoundError(key)
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


class CannedQuoteReader:
    def __init__(self, reading):
        self.reading = reading
        self.reads = 0

    def read(self, content, content_type, categories):
        self.reads += 1
        return self.reading
