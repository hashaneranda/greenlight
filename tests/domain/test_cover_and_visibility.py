from datetime import timedelta

import pytest

from greenlight.domain.cover import Cover
from greenlight.domain.errors import InvalidRequest, NotPermitted
from greenlight.domain.people import Department, Role
from greenlight.domain.visibility import can_view
from tests.domain.builders import NOW, TODAY, build_company, submitted


@pytest.fixture
def company():
    return build_company()


def arrange(company, **overrides):
    values = {
        "arranged_by": company.nadia,
        "absent": company.nadia,
        "covering": company.tom,
        "starts_on": TODAY,
        "ends_on": TODAY + timedelta(days=10),
        "today": TODAY,
        "existing": [],
    }
    values.update(overrides)
    return Cover.arrange(**values)


def test_people_arrange_their_own_cover(company):
    with pytest.raises(NotPermitted):
        arrange(company, arranged_by=company.sam)


def test_admins_can_arrange_cover_for_others(company):
    company.ceo.roles.add(Role.ADMIN)

    cover = arrange(company, arranged_by=company.ceo)

    assert cover.is_active_on(TODAY)


def test_cover_cannot_overlap(company):
    first = arrange(company)

    with pytest.raises(InvalidRequest, match="already has cover"):
        arrange(
            company,
            existing=[first],
            starts_on=TODAY + timedelta(days=5),
            ends_on=TODAY + timedelta(days=20),
        )


def test_cover_is_time_boxed(company):
    with pytest.raises(InvalidRequest, match="at most"):
        arrange(company, ends_on=TODAY + timedelta(days=120))


def test_cancelled_cover_stops_working(company):
    cover = arrange(company)

    cover.cancel(company.nadia, NOW)

    assert not cover.is_active_on(TODAY)


class TestVisibility:
    def test_staff_see_their_own_requests_only(self, company):
        request = submitted(company, company.sam, company.general)

        assert can_view(company.sam, request)
        assert not can_view(company.ana, request)

    def test_approvers_see_what_they_are_asked_to_approve(self, company):
        request = submitted(company, company.sam, company.general)

        assert can_view(company.priya, request)

    def test_finance_and_the_coo_see_everything(self, company):
        request = submitted(company, company.sam, company.general)

        assert can_view(company.tom, request)
        assert can_view(company.nadia, request)

    def test_department_heads_see_their_department_but_not_confidential_items(self, company):
        operations = Department("Operations", head_id=company.priya.id)
        company.sam.department_id = operations.id
        open_request = submitted(company, company.sam, company.general)
        confidential = submitted(company, company.sam, company.general, is_confidential=True)
        outsider = company.org.hire("Lee Park", department_id=operations.id)

        assert can_view(outsider, open_request, headed_department_ids={operations.id}) is True
        assert not can_view(outsider, confidential, headed_department_ids={operations.id})
