from decimal import Decimal

from greenlight.domain.invoice_matching import Invoice, rank_matches
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
