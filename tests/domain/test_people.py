from dataclasses import replace

import pytest

from greenlight.domain.errors import InvalidRequest
from greenlight.domain.people import Role, check_approvers_remain, check_reporting_line
from tests.domain.builders import build_company


@pytest.fixture
def company():
    return build_company()


def everyone(company):
    return company.org.people.values()


def test_nobody_reports_to_themselves(company):
    with pytest.raises(InvalidRequest, match="Sam can't report to themselves"):
        check_reporting_line(company.sam, company.sam.id, everyone(company))


def test_a_reporting_line_cannot_loop(company):
    with pytest.raises(InvalidRequest, match="loop: Nadia → Sam → Priya → Nadia"):
        check_reporting_line(company.nadia, company.sam.id, everyone(company))


def test_moving_someone_to_a_new_manager_is_fine(company):
    check_reporting_line(company.sam, company.daniel.id, everyone(company))


def test_someone_can_have_no_manager(company):
    check_reporting_line(company.sam, None, everyone(company))


def test_the_last_active_coo_approver_cannot_be_removed(company):
    with pytest.raises(InvalidRequest, match="only active COO approver"):
        check_approvers_remain(everyone(company), without=company.nadia)


def test_the_last_active_it_approver_cannot_be_removed(company):
    with pytest.raises(InvalidRequest, match="only active IT approver"):
        check_approvers_remain(everyone(company), without=company.daniel)


def test_an_approver_can_go_once_someone_else_holds_the_role(company):
    company.tom.roles.add(Role.EXECUTIVE_APPROVER)

    check_approvers_remain(everyone(company), without=company.nadia)


def test_an_inactive_colleague_does_not_count_as_another_approver(company):
    company.tom.roles.add(Role.IT_APPROVER)
    company.tom.is_active = False

    with pytest.raises(InvalidRequest, match="IT approver"):
        check_approvers_remain(everyone(company), without=company.daniel)


def test_people_without_approver_roles_can_always_go(company):
    check_approvers_remain(everyone(company), without=company.sam)


def test_admins_manage_people_and_finance_manages_reference_data(company):
    company.ceo.roles.add(Role.ADMIN)

    assert company.ceo.can_manage_people
    assert not company.tom.can_manage_people
    assert company.tom.can_manage_reference_data
    assert not company.ceo.can_manage_reference_data


def test_a_loop_already_in_the_data_does_not_hang_the_check(company):
    company.priya.manager_id = company.sam.id  # sam -> priya -> sam

    check_reporting_line(company.ana, company.sam.id, company.org.people.values())


def test_the_same_person_loaded_again_is_still_the_person_leaving(company):
    loaded_again = replace(company.nadia)

    with pytest.raises(InvalidRequest, match="COO"):
        check_approvers_remain([*company.org.people.values(), loaded_again], without=company.nadia)
