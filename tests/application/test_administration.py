from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from greenlight.application.administration import Administration, PersonForm
from greenlight.application.errors import NotFound
from greenlight.application.people_import import RowError
from greenlight.application.queries import SpendQueries
from greenlight.application.use_cases import SpendApprovals, Upload
from greenlight.domain.errors import InvalidRequest, NotPermitted
from greenlight.domain.people import Department, Role, Site
from tests.application.fakes import (
    FakeUnitOfWork,
    FixedClock,
    InMemoryCatalog,
    InMemoryPeople,
    MemoryStorage,
)
from tests.domain.builders import NOW, build_company, details

PDF = Upload("quote.pdf", "application/pdf", b"%PDF-1.7 quote")
HEADER = "email,name,job_title,department,site,manager_email,active"


@pytest.fixture
def company():
    company = build_company()
    company.ceo.roles.add(Role.ADMIN)
    return company


@pytest.fixture
def operations():
    return Department("Operations")


@pytest.fixture
def dubai():
    return Site("Dubai", "United Arab Emirates", "AED")


@pytest.fixture
def uow(company, operations, dubai):
    people = InMemoryPeople(list(company.org.people.values()), [operations], [dubai])
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
def admin(uow, clock):
    return Administration(lambda: uow, clock)


@pytest.fixture
def approvals(uow, clock):
    return SpendApprovals(lambda: uow, MemoryStorage(), clock)


def form_for(person, **changes) -> PersonForm:
    form = PersonForm(
        email=person.email,
        name=person.name,
        job_title=person.job_title,
        department_id=person.department_id,
        site_id=person.site_id,
        manager_id=person.manager_id,
        roles=frozenset(person.roles),
    )
    return replace(form, **changes)


def csv_of(*rows: str) -> str:
    return "\n".join([HEADER, *rows])


class TestPeople:
    def test_an_admin_adds_a_person_with_a_tidy_email(self, admin, company, uow):
        person_id = admin.add_person(
            company.ceo.id,
            PersonForm(
                email="  Andre.Silva@Orvane.example ",
                name="Andre Silva",
                job_title="Buyer",
                manager_id=company.priya.id,
            ),
        )

        andre = uow.people.get(person_id)
        assert (andre.email, andre.manager_id) == ("andre.silva@orvane.example", company.priya.id)
        assert uow.audit.entries[-1].action == "person_added"
        assert uow.audit.entries[-1].details["email"] == "andre.silva@orvane.example"
        assert uow.commits == 1

    @pytest.mark.parametrize("who", ["sam", "tom"])
    def test_only_an_admin_manages_people(self, admin, company, who):
        actor = getattr(company, who)

        with pytest.raises(NotPermitted, match="Only an admin"):
            admin.add_person(actor.id, PersonForm(email="new@orvane.example", name="New Person"))

    def test_an_email_belongs_to_one_person_whatever_the_case(self, admin, company):
        with pytest.raises(InvalidRequest, match="Sam Okafor already uses"):
            admin.add_person(
                company.ceo.id, PersonForm(email="SAM.OKAFOR@orvane.example", name="Sam Again")
            )

    def test_a_manager_must_still_work_here(self, admin, company):
        company.priya.is_active = False

        with pytest.raises(InvalidRequest, match="still works here"):
            admin.update_person(
                company.ceo.id, company.sam.id, form_for(company.sam, manager_id=company.priya.id)
            )

    def test_an_update_records_only_what_changed(self, admin, company, uow, operations):
        admin.update_person(
            company.ceo.id,
            company.sam.id,
            form_for(company.sam, job_title="Senior buyer", department_id=operations.id),
        )

        assert company.sam.job_title == "Senior buyer"
        entry = uow.audit.entries[-1]
        assert entry.action == "person_updated"
        assert entry.details["before"] == {"job_title": "", "department_id": None}
        assert entry.details["after"] == {
            "job_title": "Senior buyer",
            "department_id": str(operations.id),
        }

    def test_an_update_cannot_make_a_reporting_loop(self, admin, company):
        with pytest.raises(InvalidRequest, match="loop"):
            admin.update_person(
                company.ceo.id, company.nadia.id, form_for(company.nadia, manager_id=company.sam.id)
            )

    def test_the_only_coo_approver_keeps_the_role(self, admin, company):
        with pytest.raises(InvalidRequest, match="only active COO approver"):
            admin.update_person(
                company.ceo.id, company.nadia.id, form_for(company.nadia, roles=frozenset())
            )

    def test_dropping_one_approver_role_leaves_the_other_unquestioned(self, admin, company):
        company.nadia.roles.add(Role.IT_APPROVER)

        admin.update_person(
            company.ceo.id,
            company.nadia.id,
            form_for(company.nadia, roles=frozenset({Role.EXECUTIVE_APPROVER})),
        )

        assert company.nadia.roles == {Role.EXECUTIVE_APPROVER}


class TestDeactivating:
    def test_a_leaver_with_approvals_waiting_must_hand_them_over(self, admin, approvals, company):
        approvals.submit(company.sam.id, details(company.general), [PDF])
        approvals.submit(company.sam.id, details(company.travel), [PDF])

        with pytest.raises(InvalidRequest, match="Priya has 2 approvals waiting. Choose who"):
            admin.deactivate_person(company.ceo.id, company.priya.id, hand_over_to_id=None)
        assert company.priya.is_active

    def test_the_person_taking_over_is_asked_to_approve(self, admin, approvals, company, uow):
        reference = approvals.submit(company.sam.id, details(company.general), [PDF])

        admin.deactivate_person(company.ceo.id, company.priya.id, hand_over_to_id=company.nadia.id)

        assert not company.priya.is_active
        assert uow.requests.by_reference(reference).pending_step.approver_id == company.nadia.id
        assert uow.outbox.sent[-1].recipient_id == company.nadia.id
        assert [e.action for e in uow.audit.entries[-3:]] == [
            "approvals_handed_over",
            "approval_requested",
            "person_deactivated",
        ]
        assert uow.audit.entries[-1].details["handed_over"] == [reference]

    def test_a_leavers_reports_move_to_the_leavers_manager(self, admin, company, uow):
        admin.deactivate_person(company.ceo.id, company.priya.id, hand_over_to_id=None)

        assert company.sam.manager_id == company.nadia.id
        assert uow.audit.entries[-1].details["reports_moved"] == [company.sam.email]

    def test_the_only_it_approver_cannot_leave(self, admin, company):
        with pytest.raises(InvalidRequest, match="only active IT approver"):
            admin.deactivate_person(company.ceo.id, company.daniel.id, hand_over_to_id=None)


class TestImport:
    def test_a_preview_lists_adds_changes_and_leavers_without_changing_anyone(
        self, admin, company, uow
    ):
        preview = admin.preview_import(
            company.ceo.id,
            csv_of(
                "andre.silva@orvane.example,Andre Silva,Buyer,Operations,Dubai,"
                "priya.shah@orvane.example,yes",
                "sam.okafor@orvane.example,Sam Okafor,Analyst,,,priya.shah@orvane.example,yes",
                "ana.lima@orvane.example,Ana Lima,,,,daniel.reyes@orvane.example,no",
            ),
        )

        assert preview.errors == []
        assert [p.email for p in preview.adds] == ["andre.silva@orvane.example"]
        [sam] = preview.changes
        assert sam.person is company.sam
        assert [(c.field, c.before, c.after) for c in sam.changes] == [("job_title", "", "Analyst")]
        assert preview.deactivations == [company.ana]
        assert company.ana.is_active
        assert uow.commits == 0

    def test_applying_an_import_changes_everyone_and_audits_one_summary(
        self, admin, company, uow, operations, dubai
    ):
        admin.apply_import(
            company.ceo.id,
            csv_of(
                "andre.silva@orvane.example,Andre Silva,Buyer,operations,DUBAI,"
                "priya.shah@orvane.example,yes",
                "ana.lima@orvane.example,Ana Lima,,,,daniel.reyes@orvane.example,no",
            ),
        )

        andre = uow.people.by_email("andre.silva@orvane.example")
        assert (andre.department_id, andre.site_id) == (operations.id, dubai.id)
        assert andre.manager_id == company.priya.id
        assert not company.ana.is_active
        entry = uow.audit.entries[-1]
        assert entry.action == "people_imported"
        assert entry.details == {
            "added": ["andre.silva@orvane.example"],
            "changed": [],
            "deactivated": ["ana.lima@orvane.example"],
        }
        assert uow.commits == 1

    def test_one_bad_row_means_nothing_is_imported(self, admin, company, uow):
        preview = admin.apply_import(
            company.ceo.id,
            csv_of(
                "sam.okafor@orvane.example,Sam Okafor,Analyst,,,priya.shah@orvane.example,yes",
                "ana.lima@orvane.example,Ana Lima,,Marketing,,daniel.reyes@orvane.example,yes",
            ),
        )

        assert preview.errors == [RowError(3, "There is no department called Marketing.")]
        assert company.sam.job_title == ""
        assert uow.audit.entries == []
        assert uow.commits == 0

    def test_a_loop_made_inside_one_import_is_caught(self, admin, company):
        preview = admin.preview_import(
            company.ceo.id,
            csv_of(
                "sam.okafor@orvane.example,Sam Okafor,,,,ana.lima@orvane.example,yes",
                "ana.lima@orvane.example,Ana Lima,,,,sam.okafor@orvane.example,yes",
            ),
        )

        assert [error.row for error in preview.errors] == [2, 3]
        assert "loop" in preview.errors[0].message

    def test_a_leaver_with_approvals_waiting_is_left_for_the_person_page(
        self, admin, approvals, company
    ):
        approvals.submit(company.sam.id, details(company.general), [PDF])

        preview = admin.preview_import(
            company.ceo.id,
            csv_of("priya.shah@orvane.example,Priya Shah,,,,nadia.frost@orvane.example,no"),
        )

        assert preview.errors == [
            RowError(
                2,
                "Priya has 1 approval waiting. Deactivate them on their page "
                "to choose who takes it over.",
            )
        ]

    def test_a_leavers_reports_move_up_in_the_import(self, admin, company):
        preview = admin.preview_import(
            company.ceo.id,
            csv_of("priya.shah@orvane.example,Priya Shah,,,,nadia.frost@orvane.example,no"),
        )

        [sam] = preview.changes
        assert [(c.field, c.after) for c in sam.changes] == [
            ("manager", "nadia.frost@orvane.example")
        ]

    @pytest.mark.parametrize(
        ("row", "message"),
        [
            (
                "nadia.frost@orvane.example,Nadia Frost,,,,elena.moss@orvane.example,no",
                "Nadia is the only active COO approver",
            ),
            ("sam.okafor@orvane.example,Sam Okafor,,,Paris,,yes", "no site called Paris"),
            ("sam.okafor@orvane.example,Sam Okafor,,,,boss@orvane.example,yes", "isn't anyone"),
            ("sam.okafor@orvane.example,,,,,,yes", "Add a name"),
            ("sam.okafor@orvane.example,Sam Okafor,,,,,maybe", "yes or no"),
            ("not-an-email,Someone,,,,,yes", "work email"),
        ],
    )
    def test_each_row_is_checked(self, admin, company, row, message):
        [error] = admin.preview_import(company.ceo.id, csv_of(row)).errors

        assert error.row == 2
        assert message in error.message

    def test_an_email_appears_once_in_a_file(self, admin, company):
        row = "sam.okafor@orvane.example,Sam Okafor,,,,,yes"

        [error] = admin.preview_import(company.ceo.id, csv_of(row, row.upper())).errors

        assert (error.row, error.message) == (3, "sam.okafor@orvane.example is already on row 2.")

    def test_the_file_needs_every_column(self, admin, company):
        [error] = admin.preview_import(company.ceo.id, "email,name\nx@y.z,X").errors

        assert error.row == 1
        assert HEADER in error.message

    def test_only_an_admin_imports_people(self, admin, company):
        with pytest.raises(NotPermitted):
            admin.preview_import(company.tom.id, csv_of())


class TestCategories:
    def test_finance_adds_a_category(self, admin, company, uow):
        category_id = admin.add_category(company.tom.id, "Training", requires_it_review=False)

        assert uow.catalog.category(category_id).name == "Training"
        assert uow.audit.entries[-1].action == "category_added"
        assert uow.audit.entries[-1].details == {"name": "Training", "requires_it_review": False}
        assert uow.commits == 1

    def test_category_names_are_unique_whatever_the_case(self, admin, company):
        with pytest.raises(InvalidRequest, match="already a category called Travel"):
            admin.add_category(company.tom.id, "TRAVEL", requires_it_review=False)

    def test_retiring_a_category_records_only_what_changed(self, admin, company, uow):
        admin.update_category(
            company.tom.id,
            company.travel.id,
            name="Travel",
            requires_it_review=False,
            is_active=False,
        )

        assert not company.travel.is_active
        entry = uow.audit.entries[-1]
        assert entry.action == "category_updated"
        assert (entry.details["before"], entry.details["after"]) == (
            {"is_active": True},
            {"is_active": False},
        )

    def test_a_missing_category_cannot_be_changed(self, admin, company):
        with pytest.raises(NotFound):
            admin.update_category(
                company.tom.id,
                uuid4(),
                name="Travel",
                requires_it_review=False,
                is_active=True,
            )

    @pytest.mark.parametrize("who", ["sam", "ceo"])
    def test_only_finance_manages_categories(self, admin, company, who):
        with pytest.raises(NotPermitted, match="Only finance"):
            admin.add_category(getattr(company, who).id, "Training", requires_it_review=False)


class TestExchangeRates:
    def test_finance_changes_a_rate_and_the_old_one_is_kept_in_the_audit(self, admin, company, uow):
        admin.set_rate(company.tom.id, "AED", Decimal("0.2730"))

        [aed] = uow.catalog.rates()
        assert (aed.usd_per_unit, aed.updated_at, aed.updated_by_id) == (
            Decimal("0.2730"),
            NOW,
            company.tom.id,
        )
        assert uow.audit.entries[-1].action == "exchange_rate_set"
        assert uow.audit.entries[-1].details == {
            "currency": "AED",
            "rate_from": "0.2723",
            "rate_to": "0.2730",
        }

    def test_a_new_currency_starts_with_no_old_rate(self, admin, company, uow):
        admin.set_rate(company.tom.id, " gbp", Decimal("1.27"))

        assert uow.catalog.usd_rate("GBP") == Decimal("1.27")
        assert uow.audit.entries[-1].details["rate_from"] is None

    @pytest.mark.parametrize("currency", ["EURO", "E1R", ""])
    def test_a_currency_is_three_letters(self, admin, company, currency):
        with pytest.raises(InvalidRequest, match="three-letter"):
            admin.set_rate(company.tom.id, currency, Decimal("1"))

    @pytest.mark.parametrize("rate", ["0", "-0.5"])
    def test_a_rate_is_more_than_zero(self, admin, company, rate):
        with pytest.raises(InvalidRequest, match="more than zero"):
            admin.set_rate(company.tom.id, "AED", Decimal(rate))

    def test_the_us_dollar_is_always_one(self, admin, company):
        with pytest.raises(InvalidRequest, match="always 1"):
            admin.set_rate(company.tom.id, "USD", Decimal("1.1"))

    def test_it_cannot_set_rates(self, admin, company):
        with pytest.raises(NotPermitted, match="Only finance"):
            admin.set_rate(company.daniel.id, "AED", Decimal("0.28"))

    @pytest.mark.parametrize(("age", "is_stale"), [(35, False), (36, True)])
    def test_a_rate_goes_stale_after_five_weeks(self, uow, clock, age, is_stale):
        clock.moment = NOW + timedelta(days=age)

        [aed] = SpendQueries(lambda: uow, clock).rates_view()

        assert (aed.rate.currency, aed.is_stale) == ("AED", is_stale)
