from datetime import timedelta
from decimal import Decimal

import pytest

from greenlight.domain import events
from greenlight.domain.cover import Cover
from greenlight.domain.errors import InvalidRequest, InvalidState, NotPermitted
from greenlight.domain.money import Money
from greenlight.domain.spend_request import (
    BillingPeriod,
    RequestStatus,
    SpendRequest,
    StepStatus,
)
from tests.domain.builders import NOW, TODAY, a_quote, build_company, details, submitted


@pytest.fixture
def company():
    return build_company()


def event_types(request):
    return [type(event) for event in request.pull_events()]


class TestSubmitting:
    def test_needs_a_quote_or_a_reason(self, company):
        with pytest.raises(InvalidRequest, match="quote"):
            SpendRequest.submit(
                reference="GL-1",
                requester=company.sam,
                details=details(company.general),
                usd_rate=Decimal("1"),
                quotes=[],
                now=NOW,
            )

    def test_accepts_a_reason_instead_of_a_quote(self, company):
        request = SpendRequest.submit(
            reference="GL-1",
            requester=company.sam,
            details=details(company.travel, no_quote_reason="Visa fee, fixed by the embassy"),
            usd_rate=Decimal("1"),
            quotes=[],
            now=NOW,
        )

        assert request.status == RequestStatus.PENDING

    def test_converts_local_currency_to_usd(self, company):
        request = submitted(
            company, company.sam, company.general, "18365", "0.2723", currency="AED"
        )

        assert request.total_usd == Decimal("5000.79")
        assert request.current_steps[-1].stages == ["executive"]

    def test_recurring_spend_is_judged_on_the_whole_contract(self, company):
        request = submitted(
            company,
            company.sam,
            company.software,
            "250",
            billing_period=BillingPeriod.MONTHLY,
            term_months=36,
        )

        assert request.total == Money(Decimal("9000.00"), "USD")
        assert [step.stages for step in request.current_steps] == [
            ["line_manager"],
            ["it"],
            ["executive"],
        ]

    def test_recurring_spend_needs_a_contract_length(self, company):
        with pytest.raises(InvalidRequest, match="contract length"):
            details(company.software, billing_period=BillingPeriod.ANNUALLY).validate(
                has_quote=True
            )

    def test_first_approver_is_asked_straight_away(self, company):
        request = submitted(company, company.sam, company.general)

        assert request.pending_step.approver_id == company.priya.id
        assert events.ApprovalRequested in event_types(request)


class TestDeciding:
    def test_full_approval_moves_through_every_step(self, company):
        request = submitted(company, company.sam, company.software, "6000")

        request.approve(company.priya, NOW)
        assert request.pending_step.approver_id == company.daniel.id
        request.approve(company.daniel, NOW)
        request.approve(company.nadia, NOW)

        assert request.status == RequestStatus.APPROVED
        assert request.approved_total_usd == Decimal("6000.00")
        assert events.RequestApproved in event_types(request)

    def test_only_the_current_approver_can_decide(self, company):
        request = submitted(company, company.sam, company.software, "6000")

        with pytest.raises(NotPermitted):
            request.approve(company.nadia, NOW)

    def test_nobody_approves_their_own_request(self, company):
        request = submitted(company, company.sam, company.general)
        company.sam.manager_id = company.sam.id

        assert not request.can_be_decided_by(company.sam, None, TODAY)

    def test_rejection_needs_a_reason(self, company):
        request = submitted(company, company.sam, company.general)

        with pytest.raises(InvalidRequest, match="reason"):
            request.reject(company.priya, NOW, reason="  ")

    def test_rejection_stops_the_route(self, company):
        request = submitted(company, company.sam, company.general, "8000")

        request.reject(company.priya, NOW, reason="Use the existing supplier")

        assert request.status == RequestStatus.REJECTED
        assert [step.status for step in request.current_steps] == [
            StepStatus.REJECTED,
            StepStatus.CANCELLED,
        ]

    def test_cannot_decide_twice(self, company):
        request = submitted(company, company.sam, company.general)
        request.approve(company.priya, NOW)

        with pytest.raises(InvalidState):
            request.approve(company.priya, NOW)


class TestCover:
    def nadia_away(self, company, covering):
        return Cover.arrange(
            arranged_by=company.nadia,
            absent=company.nadia,
            covering=covering,
            starts_on=TODAY,
            ends_on=TODAY + timedelta(days=14),
            today=TODAY,
            existing=[],
        )

    def test_covering_colleague_approves_on_behalf(self, company):
        request = submitted(company, company.sam, company.general, "8000")
        request.approve(company.priya, NOW)
        cover = self.nadia_away(company, covering=company.tom)
        request.pull_events()

        request.approve(company.tom, NOW, cover=cover)

        approved = request.pull_events()[0]
        assert request.status == RequestStatus.APPROVED
        assert approved.actor_id == company.tom.id
        assert approved.on_behalf_of_id == company.nadia.id

    def test_cover_does_not_let_you_approve_your_own_request(self, company):
        request = submitted(company, company.tom, company.general, "8000")
        cover = self.nadia_away(company, covering=company.tom)

        assert request.pending_step.approver_id == company.nadia.id
        assert not request.can_be_decided_by(company.tom, cover, TODAY)

    def test_cover_does_not_let_one_person_fill_two_steps(self, company):
        request = submitted(company, company.sam, company.general, "8000")
        cover = self.nadia_away(company, covering=company.priya)
        request.approve(company.priya, NOW)

        assert not request.can_be_decided_by(company.priya, cover, TODAY)

    def test_cover_only_works_on_its_dates(self, company):
        request = submitted(company, company.sam, company.general, "8000")
        request.approve(company.priya, NOW)
        cover = self.nadia_away(company, covering=company.tom)

        assert not request.can_be_decided_by(company.tom, cover, TODAY + timedelta(days=30))


class TestChangesAfterApproval:
    def approved_request(self, company, amount="4000"):
        request = submitted(company, company.sam, company.general, amount)
        request.approve(company.priya, NOW)
        request.pull_events()
        return request

    def test_lower_price_keeps_the_approval(self, company):
        request = self.approved_request(company)

        needs_reapproval = request.revise(
            company.sam, details(company.general, "3800"), Decimal("1"), [], "Supplier discount"
        )

        assert not needs_reapproval
        assert request.status == RequestStatus.APPROVED
        assert request.approved_total_usd == Decimal("4000.00")

    def test_higher_price_goes_back_for_approval_and_keeps_history(self, company):
        request = self.approved_request(company)
        quote_v2 = a_quote(company.sam, sha="b" * 64)

        needs_reapproval = request.revise(
            company.sam,
            details(company.general, "8000"),
            Decimal("1"),
            [quote_v2],
            "Quote went up after scoping",
        )

        assert needs_reapproval
        assert request.status == RequestStatus.PENDING
        assert request.revision == 2
        assert request.approved_total_usd is None
        assert len(request.steps) == 1 and request.steps[0].status == StepStatus.APPROVED
        revised = request.pull_events()[0]
        assert revised.before["amount"] == "USD 4,000.00"
        assert revised.after["amount"] == "USD 8,000.00"

    def test_only_the_requester_can_change_it(self, company):
        request = self.approved_request(company)

        with pytest.raises(NotPermitted):
            request.revise(company.priya, details(company.general), Decimal("1"), [], "tweak")

    def test_change_needs_a_reason(self, company):
        request = self.approved_request(company)

        with pytest.raises(InvalidRequest, match="what changed"):
            request.revise(company.sam, details(company.general), Decimal("1"), [], "")

    def test_no_changes_once_paid(self, company):
        request = self.approved_request(company)
        request.record_payment(
            company.tom,
            invoice_reference="INV-1",
            amount=Money(Decimal("4000"), "USD"),
            usd_rate=Decimal("1"),
            paid_on=TODAY,
            now=NOW,
        )

        with pytest.raises(InvalidState, match="Payments"):
            request.revise(company.sam, details(company.general), Decimal("1"), [], "tweak")


class TestPayments:
    def test_finance_tracks_what_is_left_to_pay(self, company):
        request = submitted(company, company.sam, company.general, "4000")
        request.approve(company.priya, NOW)

        request.record_payment(
            company.tom,
            invoice_reference="INV-1",
            amount=Money(Decimal("1500"), "USD"),
            usd_rate=Decimal("1"),
            paid_on=TODAY,
            now=NOW,
        )

        assert request.paid_usd == Decimal("1500.00")
        assert request.outstanding_usd == Decimal("2500.00")

    def test_flags_payments_above_the_approved_amount(self, company):
        request = submitted(company, company.sam, company.general, "4000")
        request.approve(company.priya, NOW)
        request.pull_events()

        request.record_payment(
            company.tom,
            invoice_reference="INV-1",
            amount=Money(Decimal("4400"), "USD"),
            usd_rate=Decimal("1"),
            paid_on=TODAY,
            now=NOW,
        )

        assert request.pull_events()[0].over_approval_usd == Decimal("400.00")

    def test_only_finance_records_payments(self, company):
        request = submitted(company, company.sam, company.general)
        request.approve(company.priya, NOW)

        with pytest.raises(NotPermitted):
            request.record_payment(
                company.sam,
                invoice_reference="INV-1",
                amount=Money(Decimal("10"), "USD"),
                usd_rate=Decimal("1"),
                paid_on=TODAY,
                now=NOW,
            )

    def test_unapproved_requests_cannot_be_paid(self, company):
        request = submitted(company, company.sam, company.general)

        with pytest.raises(InvalidState):
            request.record_payment(
                company.tom,
                invoice_reference="INV-1",
                amount=Money(Decimal("10"), "USD"),
                usd_rate=Decimal("1"),
                paid_on=TODAY,
                now=NOW,
            )


def test_requester_can_withdraw_before_payment(company):
    request = submitted(company, company.sam, company.general)

    request.withdraw(company.sam, NOW, "No longer needed")

    assert request.status == RequestStatus.WITHDRAWN
    assert request.current_steps[0].status == StepStatus.CANCELLED
