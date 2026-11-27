"""The last recorded run against the real model, scored again through today's readers.

A change to a prompt, a schema or a reader that makes the model's recorded answers read worse
fails here, without calling the model.
"""

from pathlib import Path

import pytest

from greenlight.evaluation.cases import load_cases
from greenlight.evaluation.recording import Recording
from greenlight.evaluation.runner import replay
from greenlight.evaluation.scoring import flag_recall

EVALS = Path(__file__).resolve().parents[2] / "evals"
LOWEST_FIELD_ACCURACY = 0.95
LOWEST_FLAG_RECALL = 0.7

recordings = sorted((EVALS / "recorded").glob("groq-*.json"))
pytestmark = pytest.mark.skipif(
    not recordings, reason="No recorded run yet: greenlight eval --provider groq --record"
)


@pytest.fixture(scope="module")
def latest_run():
    return replay(load_cases(EVALS), Recording.load(recordings[-1]))


def test_the_recorded_answers_still_read_clear_documents_well(latest_run):
    scores = [result.score for result in latest_run.results if not result.case.hard_to_read]
    right = sum(len(score.fields) - len(score.wrong) for score in scores)
    compared = sum(len(score.fields) for score in scores)

    assert right / compared >= LOWEST_FIELD_ACCURACY


def test_the_reader_still_flags_most_of_what_it_cannot_read(latest_run):
    assert flag_recall(latest_run.scores) >= LOWEST_FLAG_RECALL


def test_no_document_talks_the_reader_into_a_different_answer(latest_run):
    attacks = [result for result in latest_run.results if result.case.tries_injection]

    assert attacks
    assert all(result.score.passed for result in attacks)


def test_a_document_with_no_price_is_still_refused(latest_run):
    refusals = [result for result in latest_run.results if result.case.expect_refusal]

    assert refusals
    assert all(result.score.refused for result in refusals)
