"""Thread-safe process-wide SQLAlchemy engines keyed by database URL."""

from __future__ import annotations

import atexit
import os
import threading

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

_engines: dict[str, Engine] = {}
_lock = threading.Lock()


def get_engine(database_url: str) -> Engine:
    """Return one bounded engine/pool for each database URL in the process."""
    engine = _engines.get(database_url)
    if engine is not None:
        return engine
    with _lock:
        engine = _engines.get(database_url)
        if engine is None:
            kwargs: dict = {"future": True, "pool_pre_ping": True}
            if database_url.startswith("sqlite"):
                kwargs["connect_args"] = {"check_same_thread": False}
            else:
                kwargs.update(
                    pool_size=int(os.getenv("DB_POOL_SIZE", "3")),
                    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "2")),
                    pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "10")),
                    pool_recycle=int(os.getenv("DB_POOL_RECYCLE", "1800")),
                    connect_args={
                        "application_name": os.getenv(
                            "DB_APPLICATION_NAME", "fastfund"
                        )
                    },
                )
            engine = create_engine(database_url, **kwargs)
            _engines[database_url] = engine
    return engine


def dispose_database_pools() -> None:
    """Dispose and forget every engine during shutdown or test reset."""
    with _lock:
        engines = list(_engines.values())
        _engines.clear()
    for engine in engines:
        engine.dispose()


atexit.register(dispose_database_pools)
