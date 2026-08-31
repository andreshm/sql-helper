from __future__ import annotations
import os
from pathlib import Path
from sqlalchemy import create_engine, text, Engine
from sqlalchemy.engine import URL


def build_engine(
    db_type: str,
    host: str = "",
    port: int = 0,
    user: str = "",
    password: str = "",
    database: str = "",
    sqlite_path: str = "",
) -> Engine:
    """Return a SQLAlchemy engine for the given connection parameters."""
    db_type = db_type.lower()

    if db_type == "sqlite":
        path_str = (sqlite_path or database or host or ":memory:").strip()
        if path_str == ":memory:":
            db_url = "sqlite:///:memory:"
        else:
            # Normalize path
            resolved = Path(path_str).resolve()
            # Ensure directory exists if it's a file path
            if not resolved.parent.exists():
                resolved.parent.mkdir(parents=True, exist_ok=True)
            db_url = f"sqlite:///{resolved.as_posix()}"
        engine = create_engine(db_url, connect_args={"check_same_thread": False})

    elif db_type in ("mysql", "mariadb"):
        url = URL.create(
            drivername="mysql+mysqlconnector",
            username=user,
            password=password,
            host=host or "localhost",
            port=int(port) if port else 3306,
            database=database or None,
            query={"connection_timeout": "10"},
        )
        engine = create_engine(url, pool_pre_ping=True, pool_size=3, max_overflow=2)

    elif db_type == "postgresql":
        url = URL.create(
            drivername="postgresql+psycopg2",
            username=user,
            password=password,
            host=host or "localhost",
            port=int(port) if port else 5432,
            database=database or "postgres",
        )
        engine = create_engine(url, pool_pre_ping=True, pool_size=3, max_overflow=2)

    else:
        raise ValueError(f"Unsupported database type: {db_type}")

    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return engine


def test_connection(engine: Engine) -> tuple[bool, str]:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "OK"
    except Exception as exc:
        return False, str(exc)


def get_dialect(engine: Engine) -> str:
    """Return 'sqlite', 'mysql', or 'postgresql'."""
    return engine.dialect.name
