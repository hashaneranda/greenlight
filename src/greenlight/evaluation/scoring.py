"""Score what the model read from a document against what a person says the document holds."""

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from greenlight.domain.invoice_matching import SAME_SUPPLIER, supplier_similarity

MONEY_FIELDS = frozenset({"amount", "total"})
A_CENT = Decimal("0.01")


def _as_text(value: object) -> str:
    text = "" if value is None else str(value).strip().upper()
    return "" if text == "NONE" else text


def field_matches(name: str, actual: object, expected: object) -> bool:
    if name in MONEY_FIELDS:
        return (
            _as_text(actual) != "" and abs(Decimal(str(actual)) - Decimal(str(expected))) <= A_CENT
        )
    if name == "supplier":
        return supplier_similarity(str(actual), str(expected)) >= SAME_SUPPLIER
    return _as_text(actual) == _as_text(expected)


@dataclass(frozen=True)
class CaseScore:
    """How a reading compares with the document.

    A field is worth flagging when the reader got it wrong or the document makes it hard to
    read. A wrong value on a hard-to-read field is forgiven if the reader flagged it, because
    then a person is told to check it.
    """

    case_id: str
    fields: tuple[str, ...]
    wrong: tuple[str, ...]
    flagged: tuple[str, ...]
    hard_to_read: tuple[str, ...]
    refused: bool
    expected_refusal: bool

    @property
    def worth_flagging(self) -> tuple[str, ...]:
        return tuple(name for name in self.fields if name in self.wrong + self.hard_to_read)

    @property
    def flags_missed(self) -> tuple[str, ...]:
        return tuple(name for name in self.worth_flagging if name not in self.flagged)

    @property
    def needless_flags(self) -> tuple[str, ...]:
        return tuple(name for name in self.flagged if name not in self.worth_flagging)

    @property
    def passed(self) -> bool:
        forgiven = set(self.hard_to_read) & set(self.flagged)
        return self.refused == self.expected_refusal and set(self.wrong) <= forgiven


def score_case(
    case_id: str,
    expected: Mapping[str, object],
    actual: Mapping[str, object],
    unclear: Collection[str],
    *,
    expected_unclear: Collection[str] = (),
    refused: bool = False,
    expected_refusal: bool = False,
) -> CaseScore:
    fields = () if refused else tuple(expected)
    return CaseScore(
        case_id=case_id,
        fields=fields,
        wrong=tuple(
            name for name in fields if not field_matches(name, actual[name], expected[name])
        ),
        flagged=tuple(name for name in fields if name in unclear),
        hard_to_read=tuple(name for name in fields if name in expected_unclear),
        refused=refused,
        expected_refusal=expected_refusal,
    )


def _share(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _rightly_flagged(scores: Sequence[CaseScore]) -> int:
    return sum(len(score.flagged) - len(score.needless_flags) for score in scores)


def flag_precision(scores: Sequence[CaseScore]) -> float | None:
    return _share(_rightly_flagged(scores), sum(len(score.flagged) for score in scores))


def flag_recall(scores: Sequence[CaseScore]) -> float | None:
    return _share(_rightly_flagged(scores), sum(len(score.worth_flagging) for score in scores))


def field_accuracy(scores: Sequence[CaseScore]) -> dict[str, float]:
    compared = Counter(name for score in scores for name in score.fields)
    wrong = Counter(name for score in scores for name in score.wrong)
    return {name: (count - wrong[name]) / count for name, count in compared.items()}
