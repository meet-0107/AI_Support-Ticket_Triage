import pytest

from src.db.database import get_db
from src.db.migrations import init_db
from src.schemas.ticket_schema import TicketCreate, TicketStatus
from src.services.ticket_service import ticket_service
from src.workers.celery_app import celery_app
from src.workers.celery_worker import (
    process_queued_batch_task,
    recover_stuck_tickets_task,
    triage_ticket_task,
)
from src.workers.ticket_worker import dispatch_to_celery


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_celery.db")
    monkeypatch.setattr("src.config.settings.DATABASE_PATH", test_db)
    monkeypatch.setattr("src.config.settings.CELERY_TASK_ALWAYS_EAGER", True)
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    init_db(test_db)
    return test_db


def test_celery_app_configuration():
    assert celery_app.main == "ticket_triage_worker"
    assert "triage_ticket_task" in celery_app.tasks
    assert "recover_stuck_tickets_task" in celery_app.tasks
    assert "process_queued_batch_task" in celery_app.tasks


def test_celery_triage_ticket_task(setup_test_db):
    req = TicketCreate(
        subject="Billing payment dispute",
        body="I was charged for an invoice twice on my card.",
        customerEmail="accounting@example.com",
    )
    ticket, created = ticket_service.create_ticket(
        tenant_id="acme", ticket_data=req, db_path=setup_test_db
    )
    assert created is True
    assert ticket["status"] == "queued"

    # Execute celery task in eager mode
    async_result = triage_ticket_task.delay(ticket["id"], setup_test_db)
    result = async_result.get()

    assert result is not None
    assert result["status"] == TicketStatus.TRIAGED.value
    assert result["category"] == "billing"
    assert result["priority"] == "high"
    assert result["confidence"] == 0.95

    # Verify directly from DB
    updated = ticket_service.get_ticket(ticket["id"], db_path=setup_test_db)
    assert updated["status"] == TicketStatus.TRIAGED.value
    assert updated["category"] == "billing"


def test_celery_recover_stuck_tickets_task(setup_test_db):
    req = TicketCreate(
        subject="Application crash on load",
        body="App crashed upon launch on Windows 11.",
        customerEmail="user@example.com",
    )
    ticket, _ = ticket_service.create_ticket(
        tenant_id="acme", ticket_data=req, db_path=setup_test_db
    )

    # Set status to processing with timestamp 60s ago
    with get_db(setup_test_db) as conn:
        conn.execute(
            """
            UPDATE tickets
            SET status = 'processing',
                processing_started_at = datetime('now', '-60 seconds')
            WHERE id = ?
            """,
            (ticket["id"],),
        )

    # Run recovery task
    recovered = recover_stuck_tickets_task(timeout_seconds=30.0, db_path=setup_test_db)
    assert recovered == 1

    refreshed = ticket_service.get_ticket(ticket["id"], db_path=setup_test_db)
    assert refreshed["status"] == "queued"
    assert refreshed["processing_started_at"] is None


def test_celery_process_queued_batch_task(setup_test_db):
    req1 = TicketCreate(
        subject="Technical server timeout error",
        body="Cannot connect to the server due to socket error timeout.",
        customerEmail="tech1@example.com",
    )
    req2 = TicketCreate(
        subject="Account password reset issue",
        body="Password reset link is not opening in browser.",
        customerEmail="tech2@example.com",
    )
    t1, _ = ticket_service.create_ticket("acme", req1, db_path=setup_test_db)
    t2, _ = ticket_service.create_ticket("acme", req2, db_path=setup_test_db)

    count = process_queued_batch_task(batch_size=10, db_path=setup_test_db)
    assert count == 2

    # Because tasks run eagerly in test, both should now be triaged
    res1 = ticket_service.get_ticket(t1["id"], db_path=setup_test_db)
    res2 = ticket_service.get_ticket(t2["id"], db_path=setup_test_db)
    assert res1["status"] == TicketStatus.TRIAGED.value
    assert res2["status"] == TicketStatus.TRIAGED.value


def test_dispatch_to_celery_helper(setup_test_db):
    req = TicketCreate(
        subject="Feature request dark theme",
        body="I would like to suggest adding dark mode to the dashboard.",
        customerEmail="user@theme.com",
    )
    ticket, _ = ticket_service.create_ticket("acme", req, db_path=setup_test_db)

    async_result = dispatch_to_celery(ticket["id"], setup_test_db)
    result = async_result.get()

    assert result["status"] == TicketStatus.TRIAGED.value
    assert result["category"] == "feature_request"
