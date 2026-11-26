"""The documents the evaluation reads, and what a person says each one holds."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

CONTENT_TYPES = {".pdf": "application/pdf", ".png": "image/png"}


@dataclass(frozen=True)
class Case:
    id: str
    kind: Literal["quote", "invoice"]
    document: Path
    what_it_tests: str
    expected: Mapping[str, object]
    expect_refusal: bool

    @property
    def content_type(self) -> str:
        return CONTENT_TYPES[self.document.suffix]

    @property
    def tries_injection(self) -> bool:
        return "injection" in self.id


def load_cases(evals: Path) -> list[Case]:
    return [
        Case(
            id=case["id"],
            kind=case["kind"],
            document=evals / "documents" / case["file"],
            what_it_tests=case["what_it_tests"],
            expected=case.get("expected", {}),
            expect_refusal=case.get("expect_refusal", False),
        )
        for case in json.loads((evals / "cases.json").read_text())
    ]
