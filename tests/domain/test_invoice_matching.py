from decimal import Decimal

from greenlight.domain.invoice_matching import Invoice, MatchStrength, check_invoice, rank_matches
from tests.domain.builders import NOW, build_company, submitted


def approved(company, supplier, amount):
    request = submitted(company, company.sam, company.general, amount, supplier=supplier)
    request.approve(company.priya, NOW)
    return request


def test_a_quoted_reference_alone_does_not_win_against_the_wrong_supplier():
    company = build_company()
    big = approved(company, "Ledgerline Ltd", "4900")
    big.reference = "GL-00042"
    invoice = Invoice("Shady Traders", "X-1", "USD", Decimal("4900"), purchase_reference="GL-00042")

    matches = rank_matches(invoice, [big], Decimal("1"))

    assert matches == []


def test_short_names_do_not_count_as_supplier_matches():
    company = build_company()
    request = approved(company, "Digital Systems", "1000")

    matches = rank_matches(Invoice("IT", "1", "EUR", Decimal("1")), [request], Decimal("1"))

    assert matches == []


def test_an_exact_supplier_and_amount_is_a_strong_match():
    company = build_company()
    request = approved(company, "Gulf Computers LLC", "4200")

    [match] = rank_matches(
        Invoice("Gulf Computers", "GC-1", "USD", Decimal("4200")), [request], Decimal("1")
    )

    assert match.strength == MatchStrength.STRONG


def test_an_invoice_for_spend_still_waiting_for_approval_is_flagged():
    company = build_company()
    waiting = submitted(
        company, company.sam, company.general, "1800", supplier="Kolwezi Cooling SARL"
    )

    check = check_invoice(
        Invoice("Kolwezi Cooling", "KC-7", "USD", Decimal("1800")), [waiting], Decimal("1")
    )

    assert check.matches == []
    assert check.awaiting_approval == [waiting]
    assert not check.has_approval


def test_an_invoice_from_an_unknown_supplier_has_no_approval():
    company = build_company()
    request = approved(company, "Ledgerline Ltd", "2400")

    check = check_invoice(
        Invoice("Nobody Ltd", "N-1", "USD", Decimal("900")), [request], Decimal("1")
    )

    assert not check.has_approval and check.awaiting_approval == []
