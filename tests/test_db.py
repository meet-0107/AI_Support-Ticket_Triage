import pytest
import sqlite3
from src.db.database import get_db, get_connection
from src.db.migrations import init_db


@pytest.fixture
def temp_db(tmp_path):
    db_file = str(tmp_path / "test_tickets.db")
    init_db(db_file)
    return db_file


def test_init_db_creates_tables_and_seeds(temp_db):
    with get_db(temp_db) as conn:
        tenants = conn.execute("SELECT * FROM tenants ORDER BY id ASC").fetchall()
        assert len(tenants) == 2
        assert tenants[0]["id"] == "acme"
        assert tenants[0]["api_key"] == "key_acme"
        assert tenants[1]["id"] == "beta"
        assert tenants[1]["api_key"] == "key_beta"

        # Check tickets table structure
        cursor = conn.execute("PRAGMA table_info(tickets)")
        columns = {row["name"]: row["type"] for row in cursor.fetchall()}
        expected_cols = [
            "id", "tenant_id", "idempotency_key", "subject", "body",
            "customer_email", "channel", "status", "category", "priority",
            "summary", "confidence", "triage_error", "llm_attempts",
            "embedding", "embedding_model", "duplicate_of", "duplicate_score",
            "processing_started_at", "created_at", "updated_at"
        ]
        for col in expected_cols:
            assert col in columns, f"Column {col} missing in tickets table"


def test_db_transaction_rollback(temp_db):
    # Test that exception inside get_db rolls back changes
    with pytest.raises(RuntimeError):
        with get_db(temp_db) as conn:
            conn.execute(
                "INSERT INTO tenants (id, name, api_key) VALUES (?, ?, ?)",
                ("gamma", "Gamma LLC", "key_gamma")
            )
            raise RuntimeError("Forced error to test rollback")

    with get_db(temp_db) as conn:
        tenant = conn.execute("SELECT * FROM tenants WHERE id = 'gamma'").fetchone()
        assert tenant is None
