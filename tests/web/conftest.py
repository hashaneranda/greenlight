import pytest
from fastapi.testclient import TestClient

from greenlight.config import Settings
from greenlight.infrastructure.seed import seed_demo
from greenlight.web.app import create_app
from greenlight.web.container import build_container


@pytest.fixture
def client(database_url):
    settings = Settings(
        database_url=database_url,
        secret_key="test-secret",
        cron_token="cron-secret",
    )
    container = build_container(settings)
    seed_demo(container.session_factory, container.approvals)
    return TestClient(create_app(settings, container))
