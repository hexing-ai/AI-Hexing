from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.app.core.config import get_settings


def make_engine(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(engine, "connect")
    def pragmas(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")

    return engine


def factory(settings=None):
    return sessionmaker(make_engine(settings or get_settings()), expire_on_commit=False)


@contextmanager
def transaction(session_factory):
    with session_factory() as db:
        # SQLite serializes write decisions, including authorization and idempotency.
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
