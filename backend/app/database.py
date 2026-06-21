from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import DB_PATH


SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"


engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    future=True,
    connect_args={
        "timeout": 30.0,
        "check_same_thread": False,
    },
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    future=True,
)

@event.listens_for(Engine, "connect")
def configure_sqlite(dbapi_connection: sqlite3.Connection, _: object) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def initialize_database() -> None:
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.commit()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    raw_connection = engine.raw_connection()
    try:
        dbapi_connection = getattr(raw_connection, "driver_connection", None)
        if dbapi_connection is None:
            dbapi_connection = getattr(raw_connection, "connection", raw_connection)
        if isinstance(dbapi_connection, sqlite3.Connection):
            dbapi_connection.row_factory = sqlite3.Row
        yield dbapi_connection
        raw_connection.commit()
    except Exception:
        raw_connection.rollback()
        raise
    finally:
        raw_connection.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

