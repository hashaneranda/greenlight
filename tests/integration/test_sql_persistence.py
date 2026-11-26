from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import sessionmaker

from greenlight.application import audit_chain
from greenlight.application.administration import Administration
from greenlight.application.queries import SpendQueries
from greenlight.application.use_cases import SpendApprovals, Upload
from greenlight.domain.people import Department, Role
from greenlight.domain.spend_request import RequestStatus
from greenlight.infrastructure.persistence import tables
from greenlight.infrastructure.persistence.unit_of_work import SqlUnitOfWork, make_engine
from greenlight.infrastructure.storage.database import DatabaseFileStorage
from tests.application.fakes import FixedClock
from tests.domain.builders import NOW, TODAY, build_company, details

PDF = Upload("quote.pdf", "application/pdf", b"%PDF-1.7 quote")


@pytest.fixture
def engine(database_url):
    engine = make_engine(database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def company(engine):
    company = build_company()
    company.ceo.roles.add(Role.ADMIN)
    session = sessionmaker(engine, expire_on_commit=False)()
    operations = Department("Operations", head_id=None)
    session.add(operations)
    session.add_all(company.org.people.values())
    session.add_all([company.general, company.software, company.travel])
    session.flush()
    session.execute(
        tables.approval_thresholds.insert(),
        [
            {"category_id": None, "amount_usd": Decimal("5000")},
            {"category_id": company.travel.id, "amount_usd": Decimal("10000")},
        ],
    )
    session.execute(
        tables.exchange_rates.insert().values(
            currency="AED", usd_per_unit=Decimal("0.2723"), updated_at=NOW
        )
    )
    session.commit()
    session.close()
    return company


@pytest.fixture
def services(engine):
    factory = sessionmaker(engine, expire_on_commit=False)
    clock = FixedClock(NOW)
    approvals = SpendApprovals(lambda: SqlUnitOfWork(factory), DatabaseFileStorage(engine), clock)
    return approvals, SpendQueries(lambda: SqlUnitOfWork(factory), clock), factory, clock


def test_a_request_survives_a_round_trip_through_postgres_shaped_storage(company, services):
    approvals, queries, factory, _ = services

    reference = approvals.submit(company.sam.id, details(company.general, "20000", "AED"), [PDF])
    approvals.approve(company.priya.id, reference)
    approvals.approve(company.nadia.id, reference, comment="OK")

    view = queries.request_view(company.tom.id, reference)
    request = view.request
    assert request.status == RequestStatus.APPROVED
    assert request.total_usd == Decimal("5446.00")
    assert [s.stages for s in request.current_steps] == [["line_manager"], ["executive"]]
    assert view.can_record_payment
    assert approvals.quote_content(request.quotes[0]) == PDF.content

    with SqlUnitOfWork(factory) as uow:
        entries = uow.audit.all()
        assert audit_chain.broken_links(entries) == []
        assert entries[-1].action == "fully_approved"


def test_references_are_sequential(company, services):
    approvals = services[0]

    first = approvals.submit(company.sam.id, details(company.general), [PDF])
    second = approvals.submit(company.sam.id, details(company.general), [PDF])

    assert (first, second) == ("GL-00001", "GL-00002")


def test_cover_and_reminders_work_against_the_database(company, services):
    approvals, queries, _, clock = services
    approvals.arrange_cover(
        company.nadia.id,
        absent_id=company.nadia.id,
        covering_id=company.tom.id,
        starts_on=TODAY,
        ends_on=TODAY + timedelta(days=7),
    )
    reference = approvals.submit(company.priya.id, details(company.general, "9000"), [PDF])

    assert [r.reference for r in queries.waiting_for(company.tom.id)] == [reference]
    clock.moment = NOW + timedelta(days=2)
    assert approvals.send_reminders(timedelta(hours=24)) == 1


@pytest.fixture
def admin(services):
    factory, clock = services[2], services[3]
    return Administration(lambda: SqlUnitOfWork(factory), clock)


def test_an_import_saves_new_managers_before_the_people_who_report_to_them(
    company, admin, services
):
    factory = services[2]
    preview = admin.apply_import(
        company.ceo.id,
        "email,name,job_title,department,site,manager_email,active\n"
        "lee.park@orvane.example,Lee Park,Buyer,,,mo.diaz@orvane.example,yes\n"
        "sam.okafor@orvane.example,Sam Okafor,,,,mo.diaz@orvane.example,yes\n"
        "mo.diaz@orvane.example,Mo Diaz,Head of buying,Operations,,nadia.frost@orvane.example,yes",
    )

    assert preview.errors == []
    with SqlUnitOfWork(factory) as uow:
        mo = uow.people.by_email("mo.diaz@orvane.example")
        assert uow.people.by_email("lee.park@orvane.example").manager_id == mo.id
        assert uow.people.get(company.sam.id).manager_id == mo.id


def test_a_leavers_approvals_are_handed_over_in_the_database(company, admin, services):
    approvals, queries = services[0], services[1]
    reference = approvals.submit(company.sam.id, details(company.general), [PDF])

    admin.deactivate_person(company.ceo.id, company.priya.id, hand_over_to_id=company.nadia.id)

    assert [r.reference for r in queries.waiting_for(company.nadia.id)] == [reference]
    assert queries.person(company.sam.id).manager_id == company.nadia.id
