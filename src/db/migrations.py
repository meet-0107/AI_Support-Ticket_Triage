from typing import Optional
from src.db.database import get_db

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    api_key TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    idempotency_key TEXT,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    customer_email TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'web',
    status TEXT NOT NULL,
    category TEXT,
    priority TEXT,
    summary TEXT,
    confidence REAL,
    triage_error TEXT,
    llm_attempts INTEGER NOT NULL DEFAULT 0,
    embedding TEXT,
    embedding_model TEXT,
    duplicate_of TEXT,
    duplicate_score REAL,
    processing_started_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);

CREATE INDEX IF NOT EXISTS idx_tickets_tenant_idempotency 
ON tickets (tenant_id, idempotency_key);

CREATE INDEX IF NOT EXISTS idx_tickets_tenant_email_created 
ON tickets (tenant_id, customer_email, created_at);

CREATE INDEX IF NOT EXISTS idx_tickets_status 
ON tickets (status);
"""

SEED_TENANTS = [
    ("acme", "Acme Corp", "key_acme"),
    ("beta", "Beta Inc", "key_beta"),
]


def init_db(db_path: Optional[str] = None) -> None:
    """Initializes tables, indices, and default seed tenants."""
    with get_db(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        for tenant_id, name, api_key in SEED_TENANTS:
            conn.execute(
                """
                INSERT OR IGNORE INTO tenants (id, name, api_key)
                VALUES (?, ?, ?)
                """,
                (tenant_id, name, api_key),
            )


if __name__ == "__main__":
    init_db()
    print("Database schema and seed tenants initialized successfully.")
