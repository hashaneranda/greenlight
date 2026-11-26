import os

import pytest

from greenlight.infrastructure.persistence import tables
from greenlight.infrastructure.persistence.orm import start_mappers
from greenlight.infrastructure.persistence.unit_of_work import make_engine


@pytest.fixture
def database_url(tmp_path):
    """SQLite by default. Set TEST_DATABASE_URL to run the same tests against Postgres."""
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    start_mappers()
    engine = make_engine(url)
    tables.metadata.drop_all(engine)
    tables.metadata.create_all(engine)
    engine.dispose()
    return url
