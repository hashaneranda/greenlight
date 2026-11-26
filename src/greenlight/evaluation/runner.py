"""Read every evaluation document with the real readers and score what comes back."""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date

from greenlight.domain.errors import InvalidRequest
from greenlight.domain.invoice_matching import Invoice
from greenlight.domain.quote_reading import QuoteReading
from greenlight.evaluation.cases import Case
from greenlight.evaluation.recording import (
    Answer,
    AnswerRecorder,
    RecordedModel,
    RecordedRead,
    Recording,
    document_key,
)
from greenlight.evaluation.scoring import CaseScore, score_case
from greenlight.infrastructure.ai.document_readers import (
    AIInvoiceReader,
    AIQuoteReader,
    DocumentModel,
    ModelUnavailable,
    Usage,
)
from greenlight.infrastructure.seed import CATEGORIES

CATEGORY_NAMES = [name for name, _ in CATEGORIES]

ModelReportingUsage = Callable[[Callable[[Usage], None]], DocumentModel]
"""Builds the model under test, given where it should report the tokens each read used."""


@dataclass(frozen=True)
class CaseResult:
    case: Case
    score: CaseScore
    usage: Usage | None
    seconds: float
    document_key: str
    answer: Answer


@dataclass(frozen=True)
class Run:
    label: str
    model_name: str
    ran_on: date
    results: tuple[CaseResult, ...]

    @property
    def scores(self) -> list[CaseScore]:
        return [result.score for result in self.results]

    def recording(self) -> Recording:
        return Recording(
            label=self.label,
            model_name=self.model_name,
            recorded_on=self.ran_on,
            reads={
                result.document_key: RecordedRead(
                    result.case.id, result.answer, result.usage, result.seconds
                )
                for result in self.results
            },
        )


def run(
    cases: Sequence[Case],
    model: ModelReportingUsage,
    *,
    label: str,
    model_name: str,
    ran_on: date,
) -> Run:
    """Every case really reaches the model: nothing is remembered between reads.

    A case the model never saw says nothing about how well it reads, so a run stops with
    `ModelUnavailable` rather than scoring it as a refusal.
    """
    spent: list[Usage] = []
    recorder = AnswerRecorder(model(spent.append))
    return Run(label, model_name, ran_on, tuple(_run_case(case, recorder, spent) for case in cases))


def replay(cases: Sequence[Case], recording: Recording) -> Run:
    """Score a recording again. Latency is what was measured when it was recorded."""
    replayed = run(
        cases,
        lambda on_usage: RecordedModel(recording, on_usage),
        label=recording.label,
        model_name=recording.model_name,
        ran_on=recording.recorded_on,
    )
    return replace(
        replayed,
        results=tuple(
            replace(result, seconds=recording.reads[result.document_key].seconds)
            for result in replayed.results
        ),
    )


def _run_case(case: Case, recorder: AnswerRecorder, spent: list[Usage]) -> CaseResult:
    content = case.document.read_bytes()
    spent.clear()
    started = time.perf_counter()
    try:
        actual, unclear = _read(case, recorder, content)
        refused = False
    except InvalidRequest as refusal:
        if isinstance(refusal.__cause__, ModelUnavailable):
            raise refusal.__cause__ from None
        actual, unclear, refused = {}, (), True
    seconds = round(time.perf_counter() - started, 3)

    score = score_case(
        case.id,
        case.expected,
        actual,
        unclear,
        refused=refused,
        expected_refusal=case.expect_refusal,
    )
    key = document_key(content)
    return CaseResult(case, score, _added_up(spent), seconds, key, recorder.answers[key])


def _read(
    case: Case, model: DocumentModel, content: bytes
) -> tuple[dict[str, object], tuple[str, ...]]:
    if case.kind == "quote":
        quote = AIQuoteReader(model).read(content, case.content_type, CATEGORY_NAMES)
        return _quote_fields(quote), quote.unclear_fields
    invoice = AIInvoiceReader(model).read(content, case.content_type)
    return _invoice_fields(invoice), invoice.unclear_fields


def _quote_fields(quote: QuoteReading) -> dict[str, object]:
    return {
        "supplier": quote.supplier,
        "currency": quote.currency,
        "amount": quote.amount,
        "billing_period": quote.billing_period.value if quote.billing_period else "none",
        "term_months": quote.term_months,
    }


def _invoice_fields(invoice: Invoice) -> dict[str, object]:
    return {
        "supplier": invoice.supplier,
        "invoice_number": invoice.invoice_number,
        "currency": invoice.currency,
        "total": invoice.total,
        "invoice_date": invoice.invoice_date.isoformat() if invoice.invoice_date else None,
        "purchase_reference": invoice.purchase_reference,
    }


def _added_up(spent: list[Usage]) -> Usage | None:
    if not spent:
        return None
    return Usage(
        prompt_tokens=sum(usage.prompt_tokens for usage in spent),
        completion_tokens=sum(usage.completion_tokens for usage in spent),
    )
