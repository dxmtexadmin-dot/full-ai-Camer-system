"""Database engine, session factory and schema bootstrap.

The models use only portable SQLAlchemy types so the SQLite database can be
moved to PostgreSQL later by changing ``DATABASE_URL``.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import PROJECT_ROOT, Settings, get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker[Session]] = None


def _sqlite_path_from_url(url: str) -> Optional[Path]:
    if not url.startswith("sqlite"):
        return None
    raw = url.split("///", 1)[-1]
    if raw in {"", ":memory:"}:
        return None
    path = Path(raw)
    return path if path.is_absolute() else (PROJECT_ROOT / path)


def build_engine(database_url: str, *, echo: bool = False) -> Engine:
    kwargs: dict = {"echo": echo, "future": True}
    sqlite_path = _sqlite_path_from_url(database_url)
    if database_url.startswith("sqlite"):
        sqlite_path.parent.mkdir(parents=True, exist_ok=True) if sqlite_path else None
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(database_url, **kwargs)

    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _record):  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    return engine


def configure(database_url: Optional[str] = None, *, echo: bool = False) -> Engine:
    """(Re)create the process-wide engine and session factory."""
    global _engine, _session_factory
    url = database_url or get_settings().database_url
    if _engine is not None:
        _engine.dispose()
    _engine = build_engine(url, echo=echo)
    _session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False, future=True)
    logger.info("Database configured: %s", url)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        configure()
    assert _engine is not None
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    if _session_factory is None:
        configure()
    assert _session_factory is not None
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session for background workers and scripts."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def init_db(settings: Optional[Settings] = None, *, seed: bool = True) -> Engine:
    """Create directories, tables and initial reference data."""
    settings = settings or get_settings()
    settings.ensure_directories()
    engine = configure(settings.database_url)

    # Importing the models registers them on Base.metadata.
    from app import models  # noqa: F401  (side effect: register mappers)

    Base.metadata.create_all(bind=engine)
    logger.info("Database schema ready (%d tables)", len(Base.metadata.tables))

    if seed:
        from app.services.bootstrap import bootstrap_database

        bootstrap_database(settings)
    return engine


def reset_for_tests(database_url: str) -> None:
    """Create a fresh schema in an isolated database (used by the test-suite)."""
    global _engine, _session_factory
    _engine = None
    _session_factory = None
    configure(database_url)
    from app import models  # noqa: F401

    Base.metadata.drop_all(bind=get_engine())
    Base.metadata.create_all(bind=get_engine())
