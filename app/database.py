"""Database engine, session factory and SQLite pragmas.

Every connection enables foreign keys, WAL journaling and a busy timeout so
concurrent reads (dashboard auto-refresh) never collide with a cashier's write
(spec §10.2). The engine is created lazily via :func:`init_engine` so tests can
point at a throwaway database.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


# Populated by init_engine().
engine: Engine | None = None
SessionLocal: sessionmaker[Session] | None = None


def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.execute("PRAGMA synchronous = NORMAL")
    cursor.execute("PRAGMA busy_timeout = 5000")
    cursor.close()


def init_engine(db_url: str, *, echo: bool = False) -> Engine:
    """Create (or replace) the global engine and session factory."""
    global engine, SessionLocal
    new_engine = create_engine(
        db_url,
        echo=echo,
        connect_args={"check_same_thread": False},
        future=True,
    )
    event.listen(new_engine, "connect", _configure_sqlite)
    engine = new_engine
    SessionLocal = sessionmaker(bind=new_engine, autoflush=False, expire_on_commit=False, future=True)
    return new_engine


# Columns added after the initial 0.1.0 schema. Kept here so existing databases
# upgrade in place (idempotent ALTER TABLE) without a full migration framework.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "placements": {
        "payouts_generated_at": "DATETIME",
        "disposition": "TEXT",
        "disposition_note": "TEXT",
        "disposition_by": "VARCHAR(120)",
        "disposition_at": "DATETIME",
    },
}


def ensure_schema() -> None:
    """Add any columns introduced after 0.1.0 to an existing database."""
    if engine is None:
        raise RuntimeError("init_engine() must be called before ensure_schema()")
    from sqlalchemy import text

    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            for name, ddl in columns.items():
                if name not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def create_all() -> None:
    """Create tables directly (used for tests and first-run bootstrap)."""
    if engine is None:
        raise RuntimeError("init_engine() must be called before create_all()")
    # Import models so they register on Base.metadata.
    import app.models  # noqa: F401

    Base.metadata.create_all(engine)
    ensure_schema()


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a session with commit/rollback handling."""
    if SessionLocal is None:
        raise RuntimeError("init_engine() must be called before get_session()")
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ensure_database(db_path: Path) -> None:
    """Make sure the parent directory exists before opening the file."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
