from sqlalchemy import select

from greenlight import cli
from greenlight.config import Settings
from greenlight.infrastructure.persistence import tables
from greenlight.web.container import build_container


def test_the_demo_has_already_shown_its_notifications(database_url, monkeypatch):
    settings = Settings(database_url=database_url)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    cli.seed()

    with build_container(settings).session_factory() as session:
        channels = set(session.scalars(select(tables.notifications.c.channel)))
    assert channels == {"logged"}
