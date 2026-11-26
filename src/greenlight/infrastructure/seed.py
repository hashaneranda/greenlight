"""Demo organisation for Orvane Group, so reviewers can sign in as different people."""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from greenlight.application.use_cases import SpendApprovals, Upload
from greenlight.domain.catalog import Category, Tag, TagKind
from greenlight.domain.money import Money
from greenlight.domain.people import Department, Employee, Role, Site
from greenlight.domain.spend_request import BillingPeriod, SpendDetails
from greenlight.infrastructure.clock import SystemClock
from greenlight.infrastructure.persistence import tables

DEMO_DOMAIN = "orvane.example"

USD_RATES = {
    "AED": "0.27229",
    "CDF": "0.00035",
    "TZS": "0.00038",
    "EUR": "1.08",
    "GBP": "1.27",
    "ZAR": "0.055",
}

CATEGORIES = [
    ("IT hardware", True),
    ("Software and subscriptions", True),
    ("Travel", False),
    ("Visas and government fees", False),
    ("Professional services", False),
    ("Marketing and events", False),
    ("Site equipment and supplies", False),
    ("Facilities", False),
    ("Training", False),
]

SAMPLE_QUOTE = Upload("quote.pdf", "application/pdf", b"%PDF-1.4\n% Sample quote for demo\n")


def seed_demo(session_factory: sessionmaker[Session], approvals: SpendApprovals) -> bool:
    with session_factory() as session:
        if session.scalars(select(Employee).limit(1)).first():
            return False
        people = _organisation(session)
        categories = _catalog(session)
        tags = _tags(session)
        session.commit()

    _sample_requests(approvals, people, categories, tags)
    return True


def _organisation(session: Session) -> dict[str, Employee]:
    dubai = Site("Dubai head office", "United Arab Emirates", "AED")
    kolwezi = Site("Kolwezi site", "DR Congo", "CDF")
    geita = Site("Geita site", "Tanzania", "TZS")
    session.add_all([dubai, kolwezi, geita])

    departments = {
        name: Department(name)
        for name in ["Executive", "Finance", "Technology", "Operations", "People"]
    }
    session.add_all(departments.values())
    session.flush()

    people: dict[str, Employee] = {}

    def hire(key, name, title, department, site, manager=None, roles=()):
        person = Employee(
            email=f"{name.split()[0].lower()}.{name.split()[-1].lower()}@{DEMO_DOMAIN}",
            name=name,
            job_title=title,
            department_id=departments[department].id,
            site_id=site.id,
            manager_id=people[manager].id if manager else None,
            roles=set(roles),
        )
        session.add(person)
        session.flush()
        people[key] = person
        return person

    hire("ceo", "Karim Haddad", "Chief Executive Officer", "Executive", dubai, roles=[Role.ADMIN])
    hire(
        "nadia",
        "Nadia Frost",
        "Chief Operating Officer",
        "Executive",
        dubai,
        "ceo",
        [Role.EXECUTIVE_APPROVER],
    )
    hire("tom", "Tom Becker", "Head of Finance", "Finance", dubai, "nadia", [Role.FINANCE])
    hire("leila", "Leila Mansour", "Accounts Payable Lead", "Finance", dubai, "tom", [Role.FINANCE])
    hire(
        "daniel",
        "Daniel Reyes",
        "Technology Lead",
        "Technology",
        dubai,
        "nadia",
        [Role.IT_APPROVER, Role.ADMIN],
    )
    hire("omar", "Omar Saleh", "Systems Engineer", "Technology", dubai, "daniel")
    hire("priya", "Priya Raman", "Head of Operations", "Operations", dubai, "nadia")
    hire("joseph", "Joseph Kabila Mwamba", "Site Manager, Kolwezi", "Operations", kolwezi, "priya")
    hire("grace", "Grace Mutombo", "Site Administrator", "Operations", kolwezi, "joseph")
    hire("baraka", "Baraka Mushi", "Site Manager, Geita", "Operations", geita, "priya")
    hire("amina", "Amina Juma", "Logistics Coordinator", "Operations", geita, "baraka")
    hire("sofia", "Sofia Lindqvist", "Head of People", "People", dubai, "nadia")

    departments["Executive"].head_id = people["ceo"].id
    departments["Finance"].head_id = people["tom"].id
    departments["Technology"].head_id = people["daniel"].id
    departments["Operations"].head_id = people["priya"].id
    departments["People"].head_id = people["sofia"].id
    return people


def _catalog(session: Session) -> dict[str, Category]:
    categories = {name: Category(name, requires_it_review=it) for name, it in CATEGORIES}
    session.add_all(categories.values())
    session.flush()

    session.execute(
        tables.approval_thresholds.insert(),
        [
            {"category_id": None, "amount_usd": Decimal("5000")},
            {"category_id": categories["Travel"].id, "amount_usd": Decimal("10000")},
        ],
    )
    now = SystemClock().now()
    session.execute(
        tables.exchange_rates.insert(),
        [
            {"currency": code, "usd_per_unit": Decimal(rate), "updated_at": now}
            for code, rate in USD_RATES.items()
        ],
    )
    return categories


def _tags(session: Session) -> dict[str, Tag]:
    today = SystemClock().now().date()
    tags = {
        "finance_system": Tag(TagKind.PROJECT, "New finance system", budget_usd=Decimal("250000")),
        "kolwezi": Tag(TagKind.PROJECT, "Kolwezi plant expansion", budget_usd=Decimal("1200000")),
        "indaba": Tag(
            TagKind.TRIP,
            "Mining Indaba, Cape Town",
            starts_on=today + timedelta(days=60),
            ends_on=today + timedelta(days=64),
        ),
    }
    session.add_all(tags.values())
    return tags


def _sample_requests(approvals, people, categories, tags) -> None:
    def raise_request(who, category, amount, currency, **extra):
        values = {
            "title": extra.pop("title"),
            "supplier": extra.pop("supplier"),
            "justification": extra.pop("why"),
            "category_id": categories[category].id,
            "amount": Money(Decimal(amount), currency),
        }
        values.update(extra)
        uploads = [] if values.get("no_quote_reason") else [SAMPLE_QUOTE]
        return approvals.submit(people[who].id, SpendDetails(**values), uploads)

    erp = raise_request(
        "omar",
        "Software and subscriptions",
        "2400",
        "USD",
        title="Finance system licences",
        supplier="Ledgerline Ltd",
        why="Named-user licences for the new finance system, 3-year term.",
        billing_period=BillingPeriod.MONTHLY,
        term_months=36,
        tag_id=tags["finance_system"].id,
    )
    approvals.approve(people["daniel"].id, erp)
    approvals.approve(people["nadia"].id, erp, comment="Agreed, matches the business case.")

    laptops = raise_request(
        "omar",
        "IT hardware",
        "4200",
        "USD",
        title="Laptops for finance team",
        supplier="Gulf Computers LLC",
        why="Three laptops for new finance hires.",
        tag_id=tags["finance_system"].id,
    )
    approvals.approve(people["daniel"].id, laptops)
    approvals.record_payment(
        people["leila"].id,
        laptops,
        invoice_reference="GC-10442",
        amount=Money(Decimal("4200"), "USD"),
        paid_on=SystemClock().now().date(),
    )

    generator = raise_request(
        "joseph",
        "Site equipment and supplies",
        "52000000",
        "CDF",
        title="Backup generator service",
        supplier="Katanga Power Services SARL",
        why="Annual service of the backup generators before the rainy season.",
        tag_id=tags["kolwezi"].id,
    )
    approvals.approve(people["priya"].id, generator)

    for traveller, manager in (("baraka", "priya"), ("joseph", "priya"), ("priya", "nadia")):
        trip = raise_request(
            traveller,
            "Travel",
            "2350",
            "USD",
            title="Mining Indaba flights and hotel",
            supplier="Emirates and Radisson",
            why="Attending Mining Indaba to meet offtake partners.",
            tag_id=tags["indaba"].id,
        )
        approvals.approve(people[manager].id, trip)

    raise_request(
        "amina",
        "Visas and government fees",
        "350000",
        "TZS",
        title="Work permit renewal",
        supplier="Tanzania Immigration Services",
        why="Renewal of the site engineer's work permit.",
        no_quote_reason="Government fee, no quote is issued.",
    )

    raise_request(
        "grace",
        "Facilities",
        "1800",
        "USD",
        title="Air conditioning repair, site office",
        supplier="Kolwezi Cooling SARL",
        why="Two units failed in the site office.",
    )

    rejected = raise_request(
        "sofia",
        "Training",
        "6500",
        "USD",
        title="Leadership course",
        supplier="Insight Academy",
        why="Leadership programme for two managers.",
    )
    approvals.reject(people["nadia"].id, rejected, "Let's use the in-house programme this year.")

    _decided_earlier(approvals, people, raise_request)
    _waiting_for_nadia(approvals, people, tags, raise_request)


def _waiting_for_nadia(approvals, people, tags, raise_request) -> None:
    """Enough in the COO's queue that the one-at-a-time review has something to show."""
    pump = raise_request(
        "joseph",
        "Site equipment and supplies",
        "12800",
        "USD",
        title="Replacement water pump, Kolwezi plant",
        supplier="Katanga Power Services",
        why="The main dewatering pump failed its inspection and can't be repaired.",
        tag_id=tags["kolwezi"].id,
    )
    approvals.approve(people["priya"].id, pump)

    raise_request(
        "priya",
        "Professional services",
        "3400",
        "USD",
        title="Safety audit, Geita site",
        supplier="SafeMine Consulting",
        why="The yearly external safety audit the mining licence requires.",
    )


def _decided_earlier(approvals, people, raise_request) -> None:
    """Past decisions, paid and closed, so there is something for Greenlight to learn from."""
    today = SystemClock().now().date()

    def approved_and_paid(who, category, amount, title, supplier, approvers):
        reference = raise_request(
            who,
            category,
            amount,
            "USD",
            title=title,
            supplier=supplier,
            why="Renewal agreed in the annual plan.",
        )
        for approver in approvers:
            approvals.approve(people[approver].id, reference)
        approvals.record_payment(
            people["leila"].id,
            reference,
            invoice_reference=f"INV-{reference[-3:]}",
            amount=Money(Decimal(amount), "USD"),
            paid_on=today,
        )

    for title, supplier, amount in (
        ("Microsoft 365 renewal", "Microsoft", "6800"),
        ("Design tool licences", "Figma Inc", "5400"),
        ("Atlassian cloud renewal", "Atlassian", "7200"),
        ("Endpoint security renewal", "SentinelOne", "5900"),
        ("Video call licences", "Zoom Communications", "5600"),
    ):
        approved_and_paid(
            "omar", "Software and subscriptions", amount, title, supplier, ["daniel", "nadia"]
        )

    for title, supplier, amount, approvers in (
        ("Safety boots for the site crew", "Copperbelt Safety", "2100", ["priya"]),
        ("PPE restock", "Copperbelt Safety", "2600", ["priya"]),
        ("Generator service, last season", "Katanga Power Services", "16500", ["priya", "nadia"]),
    ):
        approved_and_paid(
            "joseph", "Site equipment and supplies", amount, title, supplier, approvers
        )

    for who, manager, title, supplier, amount, reason in (
        (
            "grace",
            "joseph",
            "Paint the site office",
            "Kolwezi Decor",
            "1200",
            "Please get a written quote first.",
        ),
        (
            "amina",
            "baraka",
            "Deep clean of the Geita office",
            "CleanPro Geita",
            "800",
            "Attach the quote so finance can check the price.",
        ),
    ):
        reference = raise_request(
            who,
            "Facilities",
            amount,
            "USD",
            title=title,
            supplier=supplier,
            why="Overdue maintenance.",
            no_quote_reason="The supplier doesn't do written quotes.",
        )
        approvals.reject(people[manager].id, reference, reason)
