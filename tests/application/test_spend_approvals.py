from datetime import timedelta
from decimal import Decimal

import pytest

from greenlight.application import audit_chain
from greenlight.application.queries import SpendQueries
from greenlight.application.use_cases import SpendApprovals, Upload
from greenlight.domain.errors import InvalidRequest, NotPermitted
from greenlight.domain.invoice_matching import Invoice
from greenlight.domain.money import Money
from greenlight.domain.spend_request import RequestStatus
from tests.application.fakes import (
    CannedInvoiceReader,
    FakeUnitOfWork,
    FixedClock,
    InMemoryCatalog,
    InMemoryPeople,
    MemoryStorage,
)
from tests.domain.builders import NOW, TODAY, build_company, details

PDF = Upload("quote.pdf", "application/pdf", b"%PDF-1.7 quote")


@pytest.fixture
def company():
    return build_company()


@pytest.fixture
def uow(company):
    people = InMemoryPeople(list(company.org.people.values()))
    catalog = InMemoryCatalog(
        [company.general, company.software, company.travel],
        company.policy,
        {"AED": Decimal("0.2723")},
    )
    return FakeUnitOfWork(people, catalog)


@pytest.fixture
def clock():
    return FixedClock(NOW)


@pytest.fixture
def approvals(uow, clock):
    return SpendApprovals(lambda: uow, MemoryStorage(), clock)


@pytest.fixture
def queries(uow, clock):
    return SpendQueries(lambda: uow, clock)


def recipients(uow, company):
    names = {p.id: p.first_name for p in company.org.people.values()}
    return [(names[n.recipient_id], n.subject.split(":")[0]) for n in uow.outbox.sent]


def test_submitting_notifies_the_line_manager(company, uow, approvals):
    reference = approvals.submit(company.sam.id, details(company.general), [PDF])

    assert reference == "GL-00001"
    assert recipients(uow, company) == [("Priya", "Approval needed")]
    assert uow.commits == 1


def test_quote_must_be_a_document_or_photo(company, approvals):
    script = Upload("quote.exe", "application/x-msdownload", b"MZ")

    with pytest.raises(InvalidRequest, match="PDF or a photo"):
        approvals.submit(company.sam.id, details(company.general), [script])


def test_full_journey_is_audited_and_finance_is_told(company, uow, approvals):
    reference = approvals.submit(company.sam.id, details(company.general, "7000"), [PDF])
    approvals.approve(company.priya.id, reference)
    approvals.approve(company.nadia.id, reference, comment="Fine for this quarter")

    actions = [entry.action for entry in uow.audit.entries]
    assert actions == [
        "submitted",
        "route_set",
        "approval_requested",
        "approved",
        "approval_requested",
        "approved",
        "fully_approved",
    ]
    assert ("Tom", "Approved spend to expect") in recipients(uow, company)
    assert audit_chain.broken_links(uow.audit.entries) == []


def test_tampering_with_the_audit_log_is_detectable(company, uow, approvals):
    reference = approvals.submit(company.sam.id, details(company.general), [PDF])
    approvals.approve(company.priya.id, reference)

    uow.audit.entries[3].details["comment"] = "edited later"

    assert [e.action for e in audit_chain.broken_links(uow.audit.entries)] == ["approved"]


def test_covering_colleague_is_notified_and_can_approve(company, uow, approvals, queries):
    approvals.arrange_cover(
        company.nadia.id,
        absent_id=company.nadia.id,
        covering_id=company.tom.id,
        starts_on=TODAY,
        ends_on=TODAY + timedelta(days=14),
        note="Flying to Kinshasa",
    )
    reference = approvals.submit(company.sam.id, details(company.general, "9000"), [PDF])
    approvals.approve(company.priya.id, reference)

    assert [r.reference for r in queries.waiting_for(company.tom.id)] == [reference]
    approvals.approve(company.tom.id, reference)

    approved = next(e for e in reversed(uow.audit.entries) if e.action == "approved")
    assert approved.actor_id == company.tom.id
    assert approved.on_behalf_of_id == company.nadia.id
    assert uow.requests.by_reference(reference).status == RequestStatus.APPROVED


def test_rejection_tells_the_requester_why(company, uow, approvals):
    reference = approvals.submit(company.sam.id, details(company.general), [PDF])

    approvals.reject(company.priya.id, reference, "Use the framework supplier")

    last = uow.outbox.sent[-1]
    assert last.recipient_id == company.sam.id
    assert "Use the framework supplier" in last.body


def test_price_rise_after_approval_restarts_approval(company, uow, approvals):
    reference = approvals.submit(company.sam.id, details(company.general, "4000"), [PDF])
    approvals.approve(company.priya.id, reference)

    needs_reapproval = approvals.revise(
        company.sam.id, reference, details(company.general, "6000"), [], "Scope grew"
    )

    request = uow.requests.by_reference(reference)
    assert needs_reapproval
    assert request.pending_step.approver_id == company.priya.id
    assert [s.stages for s in request.current_steps] == [["line_manager"], ["executive"]]


def test_reminders_go_to_approvers_who_have_not_acted(company, uow, approvals, clock):
    approvals.submit(company.sam.id, details(company.general), [PDF])

    assert approvals.send_reminders(timedelta(hours=24)) == 0
    clock.moment = NOW + timedelta(hours=25)
    assert approvals.send_reminders(timedelta(hours=24)) == 1
    assert approvals.send_reminders(timedelta(hours=24)) == 0
    assert uow.outbox.sent[-1].subject.startswith("Reminder")


def test_invoice_is_matched_to_its_approval(company, uow, clock):
    invoice = Invoice(
        supplier="ACME Limited",
        invoice_number="INV-778",
        currency="AED",
        total=Decimal("4407"),
    )
    approvals = SpendApprovals(lambda: uow, MemoryStorage(), clock, CannedInvoiceReader(invoice))
    acme = approvals.submit(company.sam.id, details(company.general, "1200"), [PDF])
    other = approvals.submit(
        company.sam.id, details(company.general, "1200", supplier="Globex"), [PDF]
    )
    approvals.approve(company.priya.id, acme)
    approvals.approve(company.priya.id, other)

    _, matches = approvals.read_invoice(
        company.tom.id, Upload("inv.pdf", "application/pdf", b"%PDF")
    )

    assert matches[0].request.reference == acme
    assert "Supplier matches" in matches[0].reasons
    assert "Amount matches" in matches[0].reasons


def test_only_finance_reads_invoices(company, uow, clock):
    reader = CannedInvoiceReader(Invoice("Acme", "1", "USD", Decimal("1")))
    approvals = SpendApprovals(lambda: uow, MemoryStorage(), clock, reader)

    with pytest.raises(NotPermitted):
        approvals.read_invoice(company.sam.id, PDF)


def test_dashboard_splits_approved_paid_and_pending(company, approvals, queries):
    paid = approvals.submit(company.sam.id, details(company.general, "1000"), [PDF])
    approvals.approve(company.priya.id, paid)
    approvals.record_payment(
        company.tom.id,
        paid,
        invoice_reference="INV-1",
        amount=Money(Decimal("400"), "USD"),
        paid_on=TODAY,
    )
    approvals.submit(company.sam.id, details(company.software, "300"), [PDF])

    board = queries.dashboard(company.nadia.id)

    assert board.approved_usd == Decimal("1000.00")
    assert board.paid_usd == Decimal("400.00")
    assert board.outstanding_usd == Decimal("600.00")
    assert board.pending_usd == Decimal("300.00")
    assert {b.label for b in board.by_category} == {
        "Professional services",
        "Software and subscriptions",
    }


def test_trip_spend_adds_up_across_travellers(company, approvals, queries):
    trip_id = approvals.open_trip(company.priya.id, "Mining Indaba 2027")
    for traveller in (company.sam, company.ana):
        reference = approvals.submit(
            traveller.id, details(company.travel, "1500", tag_id=trip_id), [PDF]
        )
        manager = company.priya if traveller is company.sam else company.daniel
        approvals.approve(manager.id, reference)

    trip = queries.dashboard(company.nadia.id).by_trip[0]

    assert (trip.label, trip.count, trip.committed_usd) == (
        "Mining Indaba 2027",
        2,
        Decimal("3000.00"),
    )


def test_staff_cannot_open_someone_elses_request(company, approvals, queries):
    reference = approvals.submit(company.sam.id, details(company.general), [PDF])

    with pytest.raises(NotPermitted):
        queries.request_view(company.ana.id, reference)
