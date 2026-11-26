from decimal import Decimal

import pytest

from greenlight.domain.approval_policy import ApprovalStage
from greenlight.domain.errors import NoApproverAvailable
from tests.domain.builders import build_company


@pytest.fixture
def company():
    return build_company()


def stages_and_approvers(route):
    return [(step.approver.name, step.stages) for step in route.steps]


def test_small_everyday_spend_needs_only_the_line_manager(company):
    route = company.policy.plan(company.sam, company.general, Decimal("800"), company.org)

    assert stages_and_approvers(route) == [("Priya Shah", [ApprovalStage.LINE_MANAGER])]


def test_spend_at_the_threshold_also_goes_to_the_coo(company):
    route = company.policy.plan(company.sam, company.general, Decimal("5000"), company.org)

    assert stages_and_approvers(route) == [
        ("Priya Shah", [ApprovalStage.LINE_MANAGER]),
        ("Nadia Frost", [ApprovalStage.EXECUTIVE]),
    ]


def test_it_spend_goes_to_it_even_when_small(company):
    route = company.policy.plan(company.sam, company.software, Decimal("40"), company.org)

    assert stages_and_approvers(route) == [
        ("Priya Shah", [ApprovalStage.LINE_MANAGER]),
        ("Daniel Reyes", [ApprovalStage.IT]),
    ]


def test_travel_has_its_own_threshold(company):
    under_travel_limit = company.policy.plan(
        company.sam, company.travel, Decimal("7000"), company.org
    )
    over_travel_limit = company.policy.plan(
        company.sam, company.travel, Decimal("10000"), company.org
    )

    assert len(under_travel_limit.steps) == 1
    assert over_travel_limit.steps[-1].stages == [ApprovalStage.EXECUTIVE]


def test_coo_approves_once_when_she_is_also_the_line_manager(company):
    route = company.policy.plan(company.priya, company.general, Decimal("9000"), company.org)

    assert stages_and_approvers(route) == [
        ("Nadia Frost", [ApprovalStage.LINE_MANAGER, ApprovalStage.EXECUTIVE]),
    ]
    assert "approves once" in route.notes[0]


def test_it_approver_does_not_review_his_own_it_spend(company):
    route = company.policy.plan(company.daniel, company.software, Decimal("300"), company.org)

    assert stages_and_approvers(route) == [("Nadia Frost", [ApprovalStage.LINE_MANAGER])]
    assert any("requester is the IT approver" in note for note in route.notes)


def test_coo_spend_goes_to_her_own_manager_not_to_herself(company):
    route = company.policy.plan(company.nadia, company.general, Decimal("50000"), company.org)

    assert stages_and_approvers(route) == [("Elena Moss", [ApprovalStage.LINE_MANAGER])]


def test_falls_back_to_the_coo_when_nobody_manages_the_requester(company):
    newcomer = company.org.hire("New Starter")

    route = company.policy.plan(newcomer, company.general, Decimal("100"), company.org)

    assert route.steps[0].approver is company.nadia
    assert "goes to the COO" in route.notes[0]


def test_skips_a_manager_who_has_left(company):
    company.priya.is_active = False

    route = company.policy.plan(company.sam, company.general, Decimal("100"), company.org)

    assert route.steps[0].approver is company.nadia


def test_refuses_when_it_spend_has_no_it_approver(company):
    company.daniel.is_active = False

    with pytest.raises(NoApproverAvailable):
        company.policy.plan(company.sam, company.software, Decimal("100"), company.org)
