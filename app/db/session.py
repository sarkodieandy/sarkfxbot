"""Engine/session lifecycle and explicit transaction-oriented unit of work."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config.settings import get_settings
from app.db.models import Base
from app.db.repositories import (
    AuditRepository,
    ConfigRepository,
    ExecutionAttemptRepository,
    HeartbeatRepository,
    OrderRepository,
    OutboxRepository,
    PositionRepository,
    SignalRepository,
    SystemEventRepository,
    TradeEventRepository,
    TradeRepository,
)


class Database:
    """Own an engine and session factory for one database URL."""

    def __init__(self, database_url: str, *, echo: bool = False) -> None:
        url = make_url(database_url)
        options: dict[str, Any] = {"echo": echo, "pool_pre_ping": True}
        if url.get_backend_name() == "sqlite":
            options["connect_args"] = {"check_same_thread": False}
            if url.database in {None, "", ":memory:"}:
                options["poolclass"] = StaticPool
        self.engine: Engine = create_engine(url, **options)
        if url.get_backend_name() == "sqlite":
            event.listen(self.engine, "connect", self._enable_sqlite_foreign_keys)
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )

    @staticmethod
    def _enable_sqlite_foreign_keys(dbapi_connection: Any, connection_record: Any) -> None:
        del connection_record
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    def create_schema(self) -> None:
        """Create metadata for isolated tests; production uses Alembic migrations."""

        Base.metadata.create_all(self.engine)

    def drop_schema(self) -> None:
        """Drop metadata for isolated tests only."""

        Base.metadata.drop_all(self.engine)

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def unit_of_work(self) -> UnitOfWork:
        return UnitOfWork(self.session_factory)

    def dispose(self) -> None:
        self.engine.dispose()


class UnitOfWork:
    """Group repositories under a single explicit commit/rollback boundary."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self.session: Session | None = None
        self._committed = False

    def __enter__(self) -> UnitOfWork:
        if self.session is not None:
            raise RuntimeError("unit of work is already active")
        self.session = self._session_factory()
        self.signals = SignalRepository(self.session)
        self.orders = OrderRepository(self.session)
        self.execution_attempts = ExecutionAttemptRepository(self.session)
        self.positions = PositionRepository(self.session)
        self.trades = TradeRepository(self.session)
        self.trade_events = TradeEventRepository(self.session)
        self.events = self.trade_events
        self.audit = AuditRepository(self.session)
        self.config = ConfigRepository(self.session)
        self.system_events = SystemEventRepository(self.session)
        self.system = self.system_events
        self.heartbeats = HeartbeatRepository(self.session)
        self.heartbeat = self.heartbeats
        self.outbox = OutboxRepository(self.session)
        return self

    def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("unit of work is not active")
        self.session.commit()
        self._committed = True

    def rollback(self) -> None:
        if self.session is None:
            raise RuntimeError("unit of work is not active")
        self.session.rollback()
        self._committed = False

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.session is None:
            return
        try:
            if exc_type is not None or not self._committed:
                self.session.rollback()
        finally:
            self.session.close()
            self.session = None
            self._committed = False


@lru_cache(maxsize=1)
def get_database() -> Database:
    return Database(get_settings().database_url)


def get_session() -> Generator[Session, None, None]:
    """FastAPI-compatible session dependency with safe transaction handling."""

    session = get_database().session_factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
