from decimal import Decimal

from greenlight.evaluation.scoring import (
    field_accuracy,
    field_matches,
    flag_precision,
    flag_recall,
    score_case,
)


def test_amounts_match_within_a_cent():
    assert field_matches("amount", Decimal("52000000.00"), Decimal("52000000"))
    assert field_matches("total", Decimal("1200.004"), "1200")
    assert not field_matches("amount", Decimal("1"), Decimal("52000000"))


def test_suppliers_match_when_only_the_legal_suffix_differs():
    assert field_matches("supplier", "Katanga Power Services SARL", "Katanga Power Services")
    assert not field_matches("supplier", "Dar Catering Co", "Katanga Power Services")


def test_currency_and_references_must_match_exactly():
    assert not field_matches("currency", "CDF", "USD")
    assert field_matches("purchase_reference", "GL-00001", "gl-00001 ")


def test_a_missing_value_matches_an_empty_one():
    assert field_matches("purchase_reference", "", None)
    assert field_matches("term_months", None, None)
    assert field_matches("billing_period", None, "none")
    assert not field_matches("purchase_reference", "", "GL-00001")
    assert not field_matches("term_months", None, 12)


def test_a_case_records_errors_and_whether_the_model_flagged_them():
    score = score_case(
        "q03-generator-cdf",
        expected={"currency": "CDF", "amount": "52000000"},
        actual={"currency": "USD", "amount": Decimal("52000000.00")},
        unclear=["currency"],
    )

    assert score.fields == ("currency", "amount")
    assert score.wrong == ("currency",)
    assert score.flagged_and_wrong == ("currency",)
    assert score.flagged_but_right == ()
    assert not score.passed


def test_a_case_with_every_field_right_passes_even_if_some_were_flagged():
    score = score_case(
        "i01-clean-usd",
        expected={"supplier": "Ledgerline", "total": "1200.00"},
        actual={"supplier": "Ledgerline Ltd", "total": Decimal("1200")},
        unclear=["total", "invoice_date"],
    )

    assert score.passed
    assert score.flagged_but_right == ("total",)


def test_refusing_a_document_that_is_not_a_quote_passes():
    score = score_case(
        "q08-not-a-quote", expected={}, actual={}, unclear=[], refused=True, expected_refusal=True
    )

    assert score.passed


def test_refusing_a_readable_document_fails_without_comparing_fields():
    score = score_case(
        "q01-saas-annual",
        expected={"currency": "USD", "amount": "500"},
        actual={},
        unclear=[],
        refused=True,
    )

    assert not score.passed
    assert score.fields == ()


def test_reading_a_document_that_should_have_been_refused_fails():
    score = score_case(
        "q08-not-a-quote",
        expected={},
        actual={"currency": "USD", "amount": Decimal("1")},
        unclear=[],
        expected_refusal=True,
    )

    assert not score.passed


def calibration_cases():
    return [
        score_case(
            "q01",
            expected={"currency": "CDF", "amount": "100"},
            actual={"currency": "USD", "amount": "1"},
            unclear=["currency"],
        ),
        score_case(
            "q02",
            expected={"currency": "AED", "amount": "200"},
            actual={"currency": "AED", "amount": "200"},
            unclear=["amount"],
        ),
    ]


def test_precision_is_the_share_of_flags_that_pointed_at_a_wrong_field():
    assert flag_precision(calibration_cases()) == 0.5


def test_recall_is_the_share_of_wrong_fields_the_model_flagged():
    assert flag_recall(calibration_cases()) == 0.5


def test_calibration_is_unknown_when_nothing_was_flagged_or_wrong():
    perfect = score_case("q02", expected={"amount": "200"}, actual={"amount": "200"}, unclear=[])

    assert flag_precision([perfect]) is None
    assert flag_recall([perfect]) is None


def test_field_accuracy_is_the_share_of_cases_that_read_each_field_right():
    assert field_accuracy(calibration_cases()) == {"currency": 0.5, "amount": 0.5}
