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


def test_an_amount_left_empty_does_not_match_the_printed_one():
    assert not field_matches("amount", None, "9632.64")
    assert not field_matches("total", "", "9749.25")


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
    assert score.flagged == ("currency",)
    assert score.flags_missed == ()
    assert score.needless_flags == ()
    assert not score.passed


def test_a_case_with_every_field_right_passes_even_if_some_were_flagged():
    score = score_case(
        "i01-clean-usd",
        expected={"supplier": "Ledgerline", "total": "1200.00"},
        actual={"supplier": "Ledgerline Ltd", "total": Decimal("1200")},
        unclear=["total", "invoice_date"],
    )

    assert score.passed
    assert score.needless_flags == ("total",)


def test_a_field_the_document_makes_hard_to_read_is_worth_flagging_even_when_read_right():
    score = score_case(
        "q11-stamped-total",
        expected={"supplier": "Likasi Hydraulics", "amount": "9632.64"},
        actual={"supplier": "Likasi Hydraulics", "amount": Decimal("9632.64")},
        unclear=[],
        expected_unclear=["amount"],
    )

    assert score.worth_flagging == ("amount",)
    assert score.flags_missed == ("amount",)
    assert score.passed


def test_flagging_a_hard_to_read_field_for_a_person_passes_even_when_the_value_is_wrong():
    score = score_case(
        "q09-blurred-photo",
        expected={"supplier": "Musonoi Catering", "amount": "24768.32"},
        actual={"supplier": "Greenfield Catering", "amount": None},
        unclear=["supplier", "amount"],
        expected_unclear=["supplier", "amount"],
    )

    assert score.wrong == ("supplier", "amount")
    assert score.flags_missed == ()
    assert score.passed


def test_a_confident_wrong_value_on_a_hard_to_read_field_fails():
    score = score_case(
        "q09-blurred-photo",
        expected={"supplier": "Musonoi Catering", "amount": "24768.32"},
        actual={"supplier": "Greenfield Catering", "amount": Decimal("24768.32")},
        unclear=[],
        expected_unclear=["supplier", "amount"],
    )

    assert score.flags_missed == ("supplier", "amount")
    assert not score.passed


def test_a_wrong_value_on_a_clearly_printed_field_fails_even_when_flagged():
    score = score_case(
        "q09-blurred-photo",
        expected={"currency": "USD", "amount": "24768.32"},
        actual={"currency": "EUR", "amount": None},
        unclear=["currency", "amount"],
        expected_unclear=["amount"],
    )

    assert not score.passed


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
        score_case(
            "q11",
            expected={"currency": "USD", "amount": "300", "term_months": None},
            actual={"currency": "USD", "amount": "300", "term_months": None},
            unclear=["amount"],
            expected_unclear=["currency", "amount", "term_months"],
        ),
    ]


def test_precision_is_the_share_of_flags_on_fields_worth_flagging():
    assert flag_precision(calibration_cases()) == 2 / 3


def test_recall_is_the_share_of_fields_worth_flagging_that_the_model_flagged():
    assert flag_recall(calibration_cases()) == 2 / 5


def test_calibration_is_unknown_when_nothing_was_flagged_or_wrong():
    perfect = score_case("q02", expected={"amount": "200"}, actual={"amount": "200"}, unclear=[])

    assert flag_precision([perfect]) is None
    assert flag_recall([perfect]) is None


def test_field_accuracy_is_the_share_of_cases_that_read_each_field_right():
    assert field_accuracy(calibration_cases()[:2]) == {"currency": 0.5, "amount": 0.5}
