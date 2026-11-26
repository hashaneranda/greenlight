"""Score what the model read from a document against what a person says the document holds."""

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from greenlight.domain.invoice_matching import supplier_similarity
from greenlight.domain.learning import SAME_SUPPLIER

MONEY_FIELDS = frozenset({"amount", "total"})
A_CENT = Decimal("0.01")


def _as_text(value: object) -> str:
    text = "" if value is None else str(value).strip().upper()
    return "" if text == "NONE" else text


def field_matches(name: str, actual: object, expected: object) -> bool:
    if name in MONEY_FIELDS:
        return abs(Decimal(str(actual)) - Decimal(str(expected))) <= A_CENT
    if name == "supplier":
        return supplier_similarity(str(actual), str(expected)) >= SAME_SUPPLIER
    return _as_text(actual) == _as_text(expected)


@dataclass(frozen=True)
class CaseScore:
    case_id: str
    fields: tuple[str, ...]
    wrong: tuple[str, ...]
    flagged_and_wrong: tuple[str, ...]
    flagged_but_right: tuple[str, ...]
    refused: bool
    expected_refusal: bool

    @property
    def passed(self) -> bool:
        return self.refused == self.expected_refusal and not self.wrong


def score_case(
    case_id: str,
    expected: Mapping[str, object],
    actual: Mapping[str, object],
    unclear: Collection[str],
    *,
    refused: bool = False,
    expected_refusal: bool = False,
) -> CaseScore:
    fields = () if refused else tuple(expected)
    wrong = tuple(name for name in fields if not field_matches(name, actual[name], expected[name]))
    return CaseScore(
        case_id=case_id,
        fields=fields,
        wrong=wrong,
        flagged_and_wrong=tuple(name for name in wrong if name in unclear),
        flagged_but_right=tuple(name for name in fields if name in unclear and name not in wrong),
        refused=refused,
        expected_refusal=expected_refusal,
    )


def _share(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def flag_precision(scores: Sequence[CaseScore]) -> float | None:
    flagged_and_wrong = sum(len(score.flagged_and_wrong) for score in scores)
    flagged = flagged_and_wrong + sum(len(score.flagged_but_right) for score in scores)
    return _share(flagged_and_wrong, flagged)


def flag_recall(scores: Sequence[CaseScore]) -> float | None:
    flagged_and_wrong = sum(len(score.flagged_and_wrong) for score in scores)
    return _share(flagged_and_wrong, sum(len(score.wrong) for score in scores))


def field_accuracy(scores: Sequence[CaseScore]) -> dict[str, float]:
    compared = Counter(name for score in scores for name in score.fields)
    wrong = Counter(name for score in scores for name in score.wrong)
    return {name: (count - wrong[name]) / count for name, count in compared.items()}
