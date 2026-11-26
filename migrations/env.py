from alembic import context

from greenlight.config import get_settings
from greenlight.infrastructure.persistence.tables import metadata
from greenlight.infrastructure.persistence.unit_of_work import make_engine


def run_migrations() -> None:
    engine = make_engine(get_settings().database_url)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=metadata,
            render_as_batch=connection.dialect.name == "sqlite",
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("Offline migrations are not supported. Point DATABASE_URL at a database.")
run_migrations()
