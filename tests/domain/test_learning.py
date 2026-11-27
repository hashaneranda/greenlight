from datetime import timedelta
from decimal import Decimal

from greenlight.domain.learning import (
    learned_filing,
    precedent_for,
    send_back_lessons,
    suggest_limits,
)
from tests.domain.builders import NOW, build_company, submitted


def approved(company, category, amount, supplier="Acme Ltd", **overrides):
    request = submitted(company, company.sam, category, amount, supplier=supplier, **overrides)
    while request.pending_step:
        approver = company.org.people[request.pending_step.approver_id]
        request.approve(approver, NOW + timedelta(hours=2))
    return request


def sent_back(company, category, amount, reason, supplier="Acme Ltd", **overrides):
    request = submitted(company, company.sam, category, amount, supplier=supplier, **overrides)
    approver = company.org.people[request.pending_step.approver_id]
    request.reject(approver, NOW, reason=reason)
    return request


def test_approvers_see_what_was_decided_for_the_same_supplier_before():
    company = build_company()
    earlier = approved(company, company.general, "900", supplier="Ledgerline Ltd")
    refused = sent_back(company, company.general, "950", "Use the framework deal", "Ledgerline")
    new = submitted(company, company.sam, company.general, "1000", supplier="Ledgerline")

    precedent = precedent_for(new, [earlier, refused, new])

    assert precedent.approved_before == [earlier]
    assert precedent.sent_back_before == [refused]


def test_a_request_far_above_what_the_category_usually_costs_is_unusual():
    company = build_company()
    history = [approved(company, company.general, amount) for amount in ("800", "1000", "1200")]
    new = submitted(company, company.sam, company.general, "2500", supplier="Other Co")

    precedent = precedent_for(new, [*history, new])

    assert precedent.typical_usd == Decimal("1000")
    assert precedent.times_typical == Decimal("2.5")
    assert precedent.is_unusually_high


def test_the_suppliers_own_history_outranks_the_category_average():
    company = build_company()
    cheap = [
        approved(company, company.general, amount, supplier="Other Co")
        for amount in ("800", "1000", "1200")
    ]
    last_time = approved(company, company.general, "16500", supplier="Katanga Power")
    new = submitted(company, company.sam, company.general, "18200", supplier="Katanga Power")

    precedent = precedent_for(new, [*cheap, last_time, new])

    assert precedent.supplier_usd == Decimal("16500")
    assert precedent.times_supplier == Decimal("1.1")
    assert not precedent.is_unusually_high


def test_a_big_jump_on_the_same_supplier_is_unusual():
    company = build_company()
    last_time = approved(company, company.general, "1000", supplier="Katanga Power")
    new = submitted(company, company.sam, company.general, "3000", supplier="Katanga Power")

    assert precedent_for(new, [last_time, new]).is_unusually_high


def test_too_little_history_says_nothing_about_typical_cost():
    company = build_company()
    history = [approved(company, company.general, "800")]
    new = submitted(company, company.sam, company.general, "9000")

    assert precedent_for(new, history).typical_usd is None


def test_filing_is_learned_from_the_latest_request_to_the_same_supplier():
    company = build_company()
    old = approved(company, company.general, "500", supplier="Gulf Computers")
    old.submitted_at = NOW - timedelta(days=30)
    latest = approved(company, company.software, "700", supplier="Gulf Computers LLC")

    filing = learned_filing("gulf computers", [old, latest])

    assert filing.category_id == company.software.id
    assert filing.learned_from == latest.reference


def test_filing_ignores_requests_that_were_sent_back():
    company = build_company()
    refused = sent_back(company, company.software, "700", "Wrong category", "Gulf Computers")

    assert learned_filing("Gulf Computers", [refused]) is None


def test_lessons_warn_when_similar_requests_without_a_quote_were_sent_back():
    company = build_company()
    history = [
        sent_back(company, company.general, "600", "Attach the quote", no_quote_reason="Later")
        for _ in range(2)
    ]
    for request in history:
        request.quotes = []

    lessons = send_back_lessons(company.general.id, "New Supplier", False, history)

    assert lessons == ["2 requests in this category without a quote were sent back."]


def test_lessons_repeat_the_reason_the_same_supplier_was_sent_back():
    company = build_company()
    refused = sent_back(company, company.general, "600", "Use the framework deal", "Acme")

    lessons = send_back_lessons(company.general.id, "Acme Ltd", True, [refused])

    assert lessons == ["Last time Acme was sent back: Use the framework deal"]


def test_a_higher_coo_limit_is_suggested_when_every_request_above_it_was_approved():
    company = build_company()
    history = [approved(company, company.general, str(5000 + n * 300)) for n in range(5)]

    [suggestion] = suggest_limits(company.policy, [company.general], history)

    assert suggestion.category == company.general
    assert suggestion.current_usd == Decimal("5000")
    assert suggestion.suggested_usd == Decimal("7000")
    assert suggestion.evidence == 5


def test_no_limit_change_is_suggested_after_the_coo_sends_one_back():
    company = build_company()
    history = [approved(company, company.general, str(5000 + n * 300)) for n in range(5)]
    refused = submitted(company, company.sam, company.general, "6000")
    refused.approve(company.priya, NOW)
    refused.reject(company.nadia, NOW, reason="Too much")

    assert suggest_limits(company.policy, [company.general], [*history, refused]) == []


def test_no_limit_change_is_suggested_on_thin_evidence():
    company = build_company()
    history = [approved(company, company.general, "6000") for _ in range(4)]

    assert suggest_limits(company.policy, [company.general], history) == []


def test_one_large_contract_does_not_pull_the_suggested_limit_up():
    company = build_company()
    history = [approved(company, company.general, str(5000 + n * 300)) for n in range(5)]
    history.append(approved(company, company.general, "90000"))

    [suggestion] = suggest_limits(company.policy, [company.general], history)

    assert suggestion.suggested_usd == Decimal("7000")
