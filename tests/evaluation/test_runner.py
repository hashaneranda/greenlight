from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel

from greenlight.evaluation.cases import Case, load_cases
from greenlight.evaluation.recording import Recording, document_key
from greenlight.evaluation.report import Price, render
from greenlight.evaluation.runner import Run, replay, run
from greenlight.evaluation.scoring import CaseScore
from greenlight.infrastructure.ai.document_readers import (
    InvoiceFields,
    ModelUnavailable,
    QuoteFields,
    ReadFailed,
    Usage,
)

EVALS = Path(__file__).resolve().parents[2] / "evals"
CASES = load_cases(EVALS)
A_READ = Usage(prompt_tokens=1_000, completion_tokens=200)


def _right_answer(case: Case) -> BaseModel:
    expected = case.expected
    if case.kind == "quote":
        return QuoteFields(
            supplier=expected["supplier"],
            title="Whatever the quote is for",
            currency=expected["currency"],
            amount=Decimal(expected["amount"]),
            billing_period=expected["billing_period"],
            term_months=expected["term_months"],
            category="",
            unclear_fields=[],
        )
    return InvoiceFields(
        supplier=expected["supplier"],
        invoice_number=expected["invoice_number"],
        currency=expected["currency"],
        total=Decimal(expected["total"]),
        invoice_date=expected["invoice_date"],
        purchase_reference=expected["purchase_reference"],
        lines=[],
        unclear_fields=[],
    )


def _a_letter_read_as_free() -> QuoteFields:
    return QuoteFields(
        supplier="Ridgeway Assay Laboratories Ltd",
        title="Letter",
        currency="USD",
        amount=Decimal("0"),
        billing_period="none",
        term_months=None,
        category="",
        unclear_fields=[],
    )


class FakeModel:
    """Answers each document with whatever the test put down for its case."""

    def __init__(
        self, answers: dict[str, BaseModel | ReadFailed], on_usage: Callable[[Usage], None]
    ):
        self._answers = {
            document_key(case.document.read_bytes()): answers[case.id] for case in CASES
        }
        self._on_usage = on_usage

    def fill[T: BaseModel](
        self, content: bytes, content_type: str, instructions: str, schema: type[T]
    ) -> T:
        self._on_usage(A_READ)
        answer = self._answers[document_key(content)]
        if isinstance(answer, ReadFailed):
            raise answer
        return schema.model_validate(answer.model_dump())


def _case(case_id: str) -> Case:
    return next(case for case in CASES if case.id == case_id)


def _answers(
    changed: dict[str, BaseModel | ReadFailed] | None = None,
) -> dict[str, BaseModel | ReadFailed]:
    answers = {
        case.id: _a_letter_read_as_free() if case.expect_refusal else _right_answer(case)
        for case in CASES
    }
    return answers | (changed or {})


def _run(answers: dict[str, BaseModel | ReadFailed]) -> Run:
    return run(
        CASES,
        lambda on_usage: FakeModel(answers, on_usage),
        label="fake-2026-11-26",
        model_name="fake/reader",
        ran_on=date(2026, 11, 26),
    )


def _score(outcome: Run, case_id: str) -> CaseScore:
    return next(result.score for result in outcome.results if result.case.id == case_id)


def test_every_case_passes_when_the_model_reads_what_the_document_says():
    outcome = _run(_answers())

    assert len(outcome.results) == 15
    assert all(result.score.passed for result in outcome.results)
    assert all(result.usage == A_READ for result in outcome.results)


def test_refusing_the_letter_with_no_price_passes():
    outcome = _run(_answers())

    letter = _score(outcome, "q08-not-a-quote")
    assert letter.refused
    assert letter.passed


def test_an_answer_that_does_not_fit_the_schema_is_a_refusal():
    outcome = _run(_answers({"q08-not-a-quote": ReadFailed("no JSON")}))

    assert _score(outcome, "q08-not-a-quote").refused


def test_a_run_stops_when_the_model_cannot_be_reached():
    rate_limited = ModelUnavailable("429 Too Many Requests")

    with pytest.raises(ModelUnavailable):
        _run(_answers({"q07-injection-quote": rate_limited}))


def test_taking_the_injected_total_fails_the_injection_case():
    taken_in = _right_answer(_case("q07-injection-quote"))
    taken_in.amount = Decimal("1")

    outcome = _run(_answers({"q07-injection-quote": taken_in}))

    injection = _score(outcome, "q07-injection-quote")
    assert injection.wrong == ("amount",)
    assert not injection.passed


def test_fields_the_model_was_unsure_of_are_scored_as_flags():
    unsure = _right_answer(_case("i03-scan-invoice"))
    unsure.total = Decimal("12462.75")
    unsure.unclear_fields = ["total", "supplier"]

    outcome = _run(_answers({"i03-scan-invoice": unsure}))

    scan = _score(outcome, "i03-scan-invoice")
    assert scan.flagged_and_wrong == ("total",)
    assert scan.flagged_but_right == ("supplier",)


def test_a_recording_replays_to_the_same_scores(tmp_path):
    flawed = _right_answer(_case("q03-generator-cdf"))
    flawed.currency = "USD"
    recorded = _run(
        _answers(
            {"q03-generator-cdf": flawed, "i02-multipage": ReadFailed("did not fit the schema")}
        )
    )
    path = tmp_path / "fake-2026-11-26.json"

    recorded.recording().save(path)
    replayed = replay(CASES, Recording.load(path))

    assert [result.score for result in replayed.results] == [
        result.score for result in recorded.results
    ]
    assert [result.usage for result in replayed.results] == [
        result.usage for result in recorded.results
    ]
    assert [result.seconds for result in replayed.results] == [
        result.seconds for result in recorded.results
    ]
    assert replayed.model_name == "fake/reader"


def test_the_report_leads_with_the_headline_numbers():
    taken_in = _right_answer(_case("i05-injection-invoice"))
    taken_in.total = Decimal("1")

    report = render(_run(_answers({"i05-injection-invoice": taken_in})), price=None)

    assert "14 of 15" in report
    assert "Injection attempts ignored | 1 of 2" in report
    assert "Documents refused correctly | 1 of 1" in report
    assert "| `i05-injection-invoice` | fail | total |" in report
    assert "n/a, fill in evals/pricing.json" in report


def test_the_report_prices_a_read_only_from_the_pricing_file():
    price = Price(input_per_million_usd=Decimal("0.50"), output_per_million_usd=Decimal("1.00"))

    report = render(_run(_answers()), price=price)

    assert "$0.70 per 1,000 reads" in report


def test_a_run_pauses_between_reads_but_not_before_the_first():
    pauses = []

    run(
        CASES,
        lambda on_usage: FakeModel(_answers(), on_usage),
        label="fake-2026-11-26",
        model_name="fake/reader",
        ran_on=date(2026, 11, 26),
        pause=lambda: pauses.append(1),
    )

    assert len(pauses) == len(CASES) - 1
