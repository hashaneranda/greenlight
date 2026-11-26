from typing import Self

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from greenlight.infrastructure.persistence import repositories


def make_engine(database_url: str) -> Engine:
    for prefix in ("postgres://", "postgresql://"):
        if database_url.startswith(prefix):
            database_url = "postgresql+psycopg://" + database_url.removeprefix(prefix)

    if not database_url.startswith("sqlite"):
        return create_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            # A transaction-mode pooler such as PgBouncer may hand each statement a different
            # backend, so server-side prepared statements are left off.
            connect_args={"prepare_threshold": None},
        )

    engine = create_engine(database_url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _enforce_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    return engine


class SqlUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def __enter__(self) -> Self:
        self.session = self._session_factory()
        self.requests = repositories.SqlSpendRequests(self.session)
        self.people = repositories.SqlPeople(self.session)
        self.catalog = repositories.SqlCatalog(self.session)
        self.covers = repositories.SqlCovers(self.session)
        self.audit = repositories.SqlAuditTrail(self.session)
        self.outbox = repositories.SqlOutbox(self.session)
        return self

    def __exit__(self, *exc_info) -> None:
        # Closing rolls back anything uncommitted without expiring loaded objects,
        # so query results stay readable after the unit of work ends.
        self.session.close()

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()
