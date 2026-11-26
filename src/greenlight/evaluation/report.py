"""Write up a run for people who want to know how far to trust the AI reading."""

import json
import math
import statistics
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from greenlight.evaluation.runner import CaseResult, Run
from greenlight.evaluation.scoring import field_accuracy, flag_precision, flag_recall
from greenlight.infrastructure.ai.document_readers import Usage

NO_PRICE = "n/a, fill in evals/pricing.json"
A_MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Price:
    input_per_million_usd: Decimal
    output_per_million_usd: Decimal
    checked_on: str | None = None


def load_price(path: Path, model_name: str) -> Price | None:
    """The model's price from the pricing file, or None until someone has looked it up."""
    listed = json.loads(path.read_text()).get(model_name, {})
    if listed.get("input_per_million_usd") is None or listed.get("output_per_million_usd") is None:
        return None
    return Price(
        Decimal(str(listed["input_per_million_usd"])),
        Decimal(str(listed["output_per_million_usd"])),
        listed.get("checked_on"),
    )


@dataclass(frozen=True)
class Headline:
    passed: int
    cases: int
    right_fields: int
    fields: int
    injections_held: int
    injections: int
    refusals_right: int
    refusals: int

    @classmethod
    def of(cls, run: Run) -> "Headline":
        scores = run.scores
        injections = [result.score for result in run.results if result.case.tries_injection]
        refusals = [score for score in scores if score.expected_refusal]
        return cls(
            passed=sum(score.passed for score in scores),
            cases=len(scores),
            right_fields=sum(len(score.fields) - len(score.wrong) for score in scores),
            fields=sum(len(score.fields) for score in scores),
            injections_held=sum(score.passed for score in injections),
            injections=len(injections),
            refusals_right=sum(score.refused for score in refusals),
            refusals=len(refusals),
        )

    @property
    def accuracy(self) -> str:
        return _percent(self.right_fields / self.fields) if self.fields else "n/a"


def render(run: Run, price: Price | None) -> str:
    headline = Headline.of(run)
    sections = [
        _introduction(run),
        _results(headline),
        _fields(run),
        _cases(run),
        _calibration(run),
        _cost_and_speed(run, price),
        _reproducing(run),
    ]
    return "\n\n".join(sections) + "\n"


def summary(run: Run) -> str:
    headline = Headline.of(run)
    lines = [
        f"{run.label}: {run.model_name}",
        f"Cases passed:          {headline.passed} of {headline.cases}",
        f"Fields read correctly: {headline.accuracy}",
        f"Injections ignored:    {headline.injections_held} of {headline.injections}",
        f"Refused correctly:     {headline.refusals_right} of {headline.refusals}",
        f"Median latency:        {_seconds(statistics.median(_latencies(run)))}",
    ]
    failed = [result for result in run.results if not result.score.passed]
    lines += [f"  failed {result.case.id}: {_what_went_wrong(result)}" for result in failed]
    return "\n".join(lines)


def _introduction(run: Run) -> str:
    return (
        "# How well Greenlight reads quotes and invoices\n\n"
        "Greenlight asks a language model to read supplier quotes, to fill in a spend request, "
        "and invoices, to match them to an approval. People still check every reading before "
        "it counts, but a reading that is often wrong wastes their time and one that obeys a "
        "document's hidden instructions is a risk. This page measures both on "
        f"{len(run.results)} documents whose answers we know, from tidy PDFs to tilted scans "
        "and documents that try to give the model orders. The documents and answers are in "
        "`evals/`, and `evals/README.md` says what each one tests.\n\n"
        f"Model: `{run.model_name}`. Run on {run.ran_on.day} {run.ran_on:%B %Y}, "
        f"recorded as `{run.label}`."
    )


def _results(headline: Headline) -> str:
    return "\n".join(
        [
            "## Results",
            "",
            "| | |",
            "| --- | --- |",
            f"| Cases passed | {headline.passed} of {headline.cases} |",
            f"| Fields read correctly | {headline.accuracy} "
            f"({headline.right_fields} of {headline.fields}) |",
            f"| Injection attempts ignored | {headline.injections_held} of {headline.injections} |",
            f"| Documents refused correctly | {headline.refusals_right} of {headline.refusals} |",
            "",
            "A case passes when every field is right, or when the reader refuses a document it "
            "should refuse. Fields of a document the reader refused are not counted above.",
        ]
    )


def _fields(run: Run) -> str:
    rows = [
        f"| `{name}` | {_percent(accuracy)} |"
        for name, accuracy in field_accuracy(run.scores).items()
    ]
    return "\n".join(["## By field", "", "| Field | Right |", "| --- | --- |", *rows])


def _cases(run: Run) -> str:
    rows = [
        f"| `{result.case.id}` | {'pass' if result.score.passed else 'fail'} "
        f"| {_what_went_wrong(result)} | {', '.join(_flagged(result))} "
        f"| {result.seconds:.1f} | {_tokens(result)} |"
        for result in run.results
    ]
    return "\n".join(
        [
            "## By case",
            "",
            "| Case | Result | Wrong | Flagged as unsure | Seconds | Tokens in / out |",
            "| --- | --- | --- | --- | --- | --- |",
            *rows,
        ]
    )


def _calibration(run: Run) -> str:
    precision = flag_precision(run.scores)
    recall = flag_recall(run.scores)
    when_unsure = (
        "The model never said it was unsure of a field."
        if precision is None
        else f"When the model says it's unsure, it is wrong {_percent(precision)} of the time."
    )
    mistakes_flagged = (
        "It made no mistakes, so there was nothing to flag."
        if recall is None
        else f"It flags {_percent(recall)} of its mistakes."
    )
    return (
        "## Does it know when it's unsure?\n\n"
        "The reader lists the fields it could not read with certainty, and Greenlight marks "
        "them “Check this” for the person reviewing. That only helps if the marks land on "
        f"the mistakes.\n\n{when_unsure} {mistakes_flagged}"
    )


def _cost_and_speed(run: Run, price: Price | None) -> str:
    used = [result.usage for result in run.results if result.usage]
    tokens_in = sum(usage.prompt_tokens for usage in used)
    tokens_out = sum(usage.completion_tokens for usage in used)
    latencies = _latencies(run)
    lines = [
        "## Cost and speed",
        "",
        f"- Tokens: {tokens_in:,} in and {tokens_out:,} out over {len(used)} reads, "
        f"about {_mean(tokens_in, used):,} in and {_mean(tokens_out, used):,} out per read.",
        f"- Latency: median {_seconds(statistics.median(latencies))}, "
        f"95th percentile {_seconds(_95th_percentile(latencies))}.",
        f"- Cost: {_cost(tokens_in, tokens_out, len(used), price)}.",
    ]
    return "\n".join(lines)


def _reproducing(run: Run) -> str:
    return (
        "## Reproducing this\n\n"
        "```sh\n"
        "greenlight eval --provider groq --record\n"
        f"greenlight eval --replay evals/recorded/{run.label}.json\n"
        "```\n\n"
        "The first reads every document with the model set in `GROQ_MODEL`, which needs "
        "`GROQ_API_KEY`, and saves the answers to `evals/recorded/`. The second scores a saved "
        "recording again without calling the model, through the same readers. Both rewrite "
        "this page."
    )


def _what_went_wrong(result: CaseResult) -> str:
    score = result.score
    if score.refused and not score.expected_refusal:
        return "refused the document"
    if score.expected_refusal and not score.refused:
        return "read it instead of refusing"
    return ", ".join(score.wrong)


def _flagged(result: CaseResult) -> list[str]:
    flagged = set(result.score.flagged_and_wrong) | set(result.score.flagged_but_right)
    return [name for name in result.score.fields if name in flagged]


def _tokens(result: CaseResult) -> str:
    if result.usage is None:
        return "n/a"
    return f"{result.usage.prompt_tokens:,} / {result.usage.completion_tokens:,}"


def _cost(tokens_in: int, tokens_out: int, reads: int, price: Price | None) -> str:
    if price is None or not reads:
        return NO_PRICE
    total = (
        tokens_in * price.input_per_million_usd + tokens_out * price.output_per_million_usd
    ) / A_MILLION
    per_read = total / reads
    checked = f", at prices checked on {price.checked_on}" if price.checked_on else ""
    return f"about ${per_read:.4f} a read, or ${per_read * 1000:.2f} per 1,000 reads{checked}"


def _latencies(run: Run) -> list[float]:
    return [result.seconds for result in run.results]


def _95th_percentile(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def _mean(total: int, reads: list[Usage]) -> int:
    return round(total / len(reads)) if reads else 0


def _seconds(value: float) -> str:
    return f"{value:.1f} s"


def _percent(share: float) -> str:
    return f"{share:.0%}"
