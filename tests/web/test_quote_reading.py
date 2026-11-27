from dataclasses import replace
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from greenlight.application.use_cases import SpendApprovals
from greenlight.config import Settings
from greenlight.domain.quote_reading import QuoteReading
from greenlight.infrastructure.clock import SystemClock
from greenlight.infrastructure.persistence.unit_of_work import SqlUnitOfWork
from greenlight.infrastructure.seed import seed_demo
from greenlight.infrastructure.storage.database import DatabaseFileStorage
from greenlight.web.app import create_app
from greenlight.web.container import build_container
from tests.application.fakes import CannedQuoteReader
from tests.web.test_pages import csrf_from, sign_in_as

HARD_TO_READ = QuoteReading(
    supplier="Kolwezi Cooling SARL",
    title="AC compressor replacement",
    currency="USD",
    amount=Decimal("1800"),
    unclear_fields=("supplier", "amount"),
)


@pytest.fixture
def reader():
    return CannedQuoteReader(HARD_TO_READ)


@pytest.fixture
def client(database_url, reader):
    settings = Settings(database_url=database_url, secret_key="test-secret")
    container = build_container(settings)
    seed_demo(container.session_factory, container.approvals)
    container.approvals = SpendApprovals(
        lambda: SqlUnitOfWork(container.session_factory),
        DatabaseFileStorage(container.session_factory.kw["bind"]),
        SystemClock(),
        quote_reader=reader,
    )
    container.ai_enabled = True
    client = TestClient(create_app(settings, container))
    sign_in_as(client, "Grace Mutombo")
    return client


def read(client: TestClient, **choices: str):
    form = client.get("/requests/new").text
    return client.post(
        "/requests/new/read-quote",
        data={"csrf": csrf_from(form), **choices},
        files={"quote": ("cooling.pdf", b"%PDF-1.4 test", "application/pdf")},
    )


def test_the_form_names_the_quote_ai_read_and_marks_what_to_check(client):
    page = read(client).text

    assert "Read by AI from" in page and "cooling.pdf" in page
    assert 'name="supplier"' in page and 'value="Kolwezi Cooling SARL"' in page
    assert page.count('class="unclear"') == 2
    assert page.count("Check this</span>") == 2


def test_a_price_the_reader_could_not_read_is_left_empty_to_fill_in(client, reader):
    reader.reading = replace(HARD_TO_READ, amount=None)

    page = read(client).text

    assert 'name="amount" inputmode="decimal" required value=""' in page


def test_a_confidential_quote_is_attached_without_reaching_ai(client, reader):
    page = read(client, confidential="yes").text

    assert reader.reads == 0
    assert "It was not sent to AI." in page
    assert "Read by AI" not in page
