import sqlite3
from contextlib import contextmanager
from typing import Generator, Optional
from src.config import settings


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Creates and configures a thread-safe SQLite connection."""
    target_path = db_path or settings.DATABASE_PATH
    conn = sqlite3.connect(target_path, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


@contextmanager
def get_db(db_path: Optional[str] = None) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for SQLite connection lifecycle and transactions.
    
    Automatically commits on normal exit and rolls back on unhandled exceptions.
    """
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
