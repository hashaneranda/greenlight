"""Write up a run for people who want to know how far to trust the AI reading."""

import json
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from greenlight.evaluation.cases import Case
from greenlight.evaluation.recording import Recording, document_key
from greenlight.evaluation.runner import CaseResult, Run, replay
from greenlight.evaluation.scoring import CaseScore, field_accuracy, flag_precision, flag_recall
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
class Change:
    what_changed: str
    run: Run


def load_changes(evals: Path, cases: Sequence[Case]) -> list[Change]:
    """Each change listed in evals/changes.json, scored again from the run recorded after it.

    A recording made before a case was added is scored on the cases it holds.
    """
    changes = []
    for change in json.loads((evals / "changes.json").read_text()):
        recording = Recording.load(evals / "recorded" / change["recording"])
        held = [
            case for case in cases if document_key(case.document.read_bytes()) in recording.reads
        ]
        changes.append(Change(change["change"], replay(held, recording)))
    return changes


@dataclass(frozen=True)
class Headline:
    passed: int
    cases: int
    right_fields: int
    fields: int
    right_clear_fields: int
    clear_fields: int
    injections_held: int
    injections: int
    refusals_right: int
    refusals: int
    flag_precision: float | None
    flag_recall: float | None
    median_seconds: float

    @classmethod
    def of(cls, run: Run) -> "Headline":
        scores = run.scores
        clear = [result.score for result in run.results if not result.case.hard_to_read]
        injections = [result.score for result in run.results if result.case.tries_injection]
        refusals = [score for score in scores if score.expected_refusal]
        return cls(
            passed=sum(score.passed for score in scores),
            cases=len(scores),
            right_fields=_right_fields(scores),
            fields=_fields_compared(scores),
            right_clear_fields=_right_fields(clear),
            clear_fields=_fields_compared(clear),
            injections_held=sum(score.passed for score in injections),
            injections=len(injections),
            refusals_right=sum(score.refused for score in refusals),
            refusals=len(refusals),
            flag_precision=flag_precision(scores),
            flag_recall=flag_recall(scores),
            median_seconds=statistics.median(_latencies(run)),
        )

    @property
    def accuracy(self) -> str:
        return _share(self.right_fields, self.fields)

    @property
    def clear_accuracy(self) -> str:
        return _share(self.right_clear_fields, self.clear_fields)


def render(run: Run, price: Price | None, changes: Sequence[Change] = ()) -> str:
    headline = Headline.of(run)
    sections = [
        _introduction(run),
        _results(headline),
        _fields(run),
        _cases(run),
        _calibration(run),
        *([_changes(changes)] if changes else []),
        _cost_and_speed(run, price),
        _reproducing(run),
    ]
    return "\n\n".join(sections) + "\n"


def summary(run: Run) -> str:
    headline = Headline.of(run)
    lines = [
        f"{run.label}: {run.model_name}",
        f"Cases passed:          {headline.passed} of {headline.cases}",
        f"Fields read correctly: {headline.accuracy}, {headline.clear_accuracy} on clear documents",
        f"Flag precision:        {_maybe_percent(headline.flag_precision)}",
        f"Flag recall:           {_maybe_percent(headline.flag_recall)}",
        f"Injections ignored:    {headline.injections_held} of {headline.injections}",
        f"Refused correctly:     {headline.refusals_right} of {headline.refusals}",
        f"Median latency:        {_seconds(headline.median_seconds)}",
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
        f"{len(run.results)} documents whose answers we know, from tidy PDFs to blurred photos "
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
            f"| Fields read correctly on clear documents | {headline.clear_accuracy} "
            f"({headline.right_clear_fields} of {headline.clear_fields}) |",
            f"| Injection attempts ignored | {headline.injections_held} of {headline.injections} |",
            f"| Documents refused correctly | {headline.refusals_right} of {headline.refusals} |",
            "",
            "Clear documents are the ones printed or scanned well enough to read every field. "
            "The others are hard to read on purpose: a blurred photo, a stamp across the total, "
            "glare, or a contract length that is never printed.",
            "",
            "A case passes when every field is right, or when the reader refuses a document it "
            "should refuse. On a hard document, a wrong value also passes if the reader flagged "
            "it for a person to check. Fields of a document the reader refused are not counted.",
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
        f"| {_what_went_wrong(result)} | {', '.join(result.score.flagged)} "
        f"| {', '.join(result.score.flags_missed)} | {result.seconds:.1f} | {_tokens(result)} |"
        for result in run.results
    ]
    return "\n".join(
        [
            "## By case",
            "",
            "| Case | Result | Wrong | Flagged as unsure | Should have flagged | Seconds "
            "| Tokens in / out |",
            "| --- | --- | --- | --- | --- | --- | --- |",
            *rows,
        ]
    )


def _calibration(run: Run) -> str:
    scores = run.scores
    flags = sum(len(score.flagged) for score in scores)
    worth = sum(len(score.worth_flagging) for score in scores)
    needless = sum(len(score.needless_flags) for score in scores)
    mistakes = sum(len(score.wrong) for score in scores)
    mistakes_flagged = sum(len(set(score.wrong) & set(score.flagged)) for score in scores)
    recall = (
        "No field was worth flagging."
        if not worth
        else f"It flagged {flags - needless} of the {worth} fields worth flagging "
        f"(flag recall {_percent(flag_recall(scores))})."
    )
    precision = (
        "It never said it was unsure of a field."
        if not flags
        else f"Of its {_counted(flags, 'flag')}, {_was_were(flags - needless)} worth it "
        f"(flag precision {_percent(flag_precision(scores))})."
    )
    if needless:
        precision += (
            f" The other {_was_were(needless)} on "
            f"{'a clearly printed field' if needless == 1 else 'clearly printed fields'} "
            "it read right."
        )
    own_mistakes = (
        "It made no mistakes."
        if not mistakes
        else f"Of its {_counted(mistakes, 'wrong field')}, it flagged {mistakes_flagged}."
    )
    return (
        "## Does it know when it's unsure?\n\n"
        "The reader lists the fields it could not read with certainty, and Greenlight marks "
        "them “Check this” for the person reviewing. That only helps if the marks land where a "
        "person should look: on the reader's own mistakes, and on fields the document makes "
        "hard to read, like a total under a stamp. `evals/README.md` lists those fields for "
        f"each hard document.\n\n{recall} {precision} {own_mistakes}"
    )


def _changes(changes: Sequence[Change]) -> str:
    rows = []
    for change in changes:
        headline = Headline.of(change.run)
        rows.append(
            f"| `{change.run.label}` | {headline.clear_accuracy} | {headline.accuracy} "
            f"| {_maybe_percent(headline.flag_precision)} "
            f"| {_maybe_percent(headline.flag_recall)} "
            f"| {headline.injections_held} of {headline.injections} "
            f"| {headline.refusals_right} of {headline.refusals} "
            f"| {_seconds(headline.median_seconds)} |"
        )
    what_changed = [f"- `{change.run.label}`: {change.what_changed}" for change in changes]
    return "\n".join(
        [
            "## Changes",
            "",
            "Each change to the reading is measured on the same documents before it is kept. "
            "The numbers are scored again from each run's recording, listed in "
            "`evals/changes.json`.",
            "",
            "| Run | Fields right, clear documents | Fields right, all | Flag precision "
            "| Flag recall | Injections ignored | Refused correctly | Median latency |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
            *rows,
            "",
            *what_changed,
        ]
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


def _maybe_percent(share: float | None) -> str:
    return "n/a" if share is None else _percent(share)


def _share(part: int, whole: int) -> str:
    return _percent(part / whole) if whole else "n/a"


def _right_fields(scores: Sequence[CaseScore]) -> int:
    return sum(len(score.fields) - len(score.wrong) for score in scores)


def _fields_compared(scores: Sequence[CaseScore]) -> int:
    return sum(len(score.fields) for score in scores)


def _counted(count: int, thing: str) -> str:
    return f"{count} {thing}" if count == 1 else f"{count} {thing}s"


def _was_were(count: int) -> str:
    return f"{count} was" if count == 1 else f"{count} were"
