from sqlalchemy import Engine, insert, select

from greenlight.infrastructure.persistence.tables import stored_files


class DatabaseFileStorage:
    """Keeps uploads in Postgres. Fine for quotes at Orvane's volume; swap for a bucket later."""

    def __init__(self, engine: Engine):
        self._engine = engine

    def save(self, key: str, content: bytes, content_type: str) -> None:
        with self._engine.begin() as connection:
            exists = connection.execute(
                select(stored_files.c.key).where(stored_files.c.key == key)
            ).first()
            if not exists:
                connection.execute(
                    insert(stored_files).values(key=key, content=content, content_type=content_type)
                )

    def load(self, key: str) -> bytes:
        with self._engine.connect() as connection:
            content = connection.execute(
                select(stored_files.c.content).where(stored_files.c.key == key)
            ).scalar()
        if content is None:
            raise FileNotFoundError(key)
        return content
