"""Keep what a model answered for each document, and play it back without calling the model.

Answers are kept at the `DocumentModel.fill` level, so a replay still runs the real readers'
checks and clean-up on top of them.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from pydantic import BaseModel

from greenlight.infrastructure.ai.document_readers import DocumentModel, ReadFailed, Usage


def document_key(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class Answer:
    """The schema the model filled in for a document, or why it could not fill one."""

    reading: dict[str, object] | None
    failed: str | None = None


@dataclass(frozen=True)
class RecordedRead:
    case_id: str
    answer: Answer
    usage: Usage | None
    seconds: float


@dataclass(frozen=True)
class Recording:
    label: str
    model_name: str
    recorded_on: date
    reads: dict[str, RecordedRead]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        recording = {
            "label": self.label,
            "model": self.model_name,
            "recorded_on": self.recorded_on.isoformat(),
            "reads": {key: _read_as_json(read) for key, read in self.reads.items()},
        }
        path.write_text(json.dumps(recording, indent=2, ensure_ascii=False) + "\n")

    @classmethod
    def load(cls, path: Path) -> "Recording":
        recording = json.loads(path.read_text())
        return cls(
            label=recording["label"],
            model_name=recording["model"],
            recorded_on=date.fromisoformat(recording["recorded_on"]),
            reads={key: _read_from_json(read) for key, read in recording["reads"].items()},
        )


def _read_as_json(read: RecordedRead) -> dict[str, object]:
    answer = (
        {"reading": read.answer.reading}
        if read.answer.failed is None
        else {"failed": read.answer.failed}
    )
    return {
        "case": read.case_id,
        **answer,
        "usage": asdict(read.usage) if read.usage else None,
        "seconds": read.seconds,
    }


def _read_from_json(read: dict) -> RecordedRead:
    return RecordedRead(
        case_id=read["case"],
        answer=Answer(reading=read.get("reading"), failed=read.get("failed")),
        usage=Usage(**read["usage"]) if read["usage"] else None,
        seconds=read["seconds"],
    )


class AnswerRecorder:
    """A decorator around a `DocumentModel` that keeps what it answered for each document."""

    def __init__(self, model: DocumentModel):
        self._model = model
        self.answers: dict[str, Answer] = {}

    def fill[T: BaseModel](
        self, content: bytes, content_type: str, instructions: str, schema: type[T]
    ) -> T:
        key = document_key(content)
        try:
            reading = self._model.fill(content, content_type, instructions, schema)
        except ReadFailed as error:
            self.answers[key] = Answer(reading=None, failed=str(error))
            raise
        self.answers[key] = Answer(reading=reading.model_dump(mode="json"))
        return reading


class RecordedModel:
    """Stands in for a real `DocumentModel`, answering each document as the recording did."""

    def __init__(self, recording: Recording, on_usage: Callable[[Usage], None] | None = None):
        self._recording = recording
        self._on_usage = on_usage

    def fill[T: BaseModel](
        self, content: bytes, content_type: str, instructions: str, schema: type[T]
    ) -> T:
        read = self._recording.reads.get(document_key(content))
        if read is None:
            raise LookupError("This document is not in the recording. Record the run again.")
        if read.usage and self._on_usage:
            self._on_usage(read.usage)
        if read.answer.failed is not None:
            raise ReadFailed(read.answer.failed)
        return schema.model_validate(read.answer.reading)
