import asyncio
import json
import sqlite3
import pytest

from src.db.migrations import init_db
from src.db.database import get_db
from src.schemas.ticket_schema import TicketCreate, TicketStatus, TriageError
from tests.conftest import FakeEmbedder, FakeLLM
from src.services.duplicate_service import DuplicateService
from src.services.embedding_service import EmbeddingService
from src.services.ticket_service import TicketService
from src.services.triage_service import TriageService
from src.workers.ticket_worker import TicketWorker


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_phase5.db")
    monkeypatch.setattr("src.config.settings.DATABASE_PATH", test_db)
    init_db(test_db)
    return test_db


@pytest.fixture
def ticket_service_inst(setup_test_db):
    return TicketService(default_db_path=setup_test_db)


@pytest.fixture
def embedding_service_inst():
    return EmbeddingService(provider=FakeEmbedder())


@pytest.fixture
def duplicate_service_inst():
    return DuplicateService(threshold=0.85)


@pytest.fixture
def triage_service_inst(
    setup_test_db, ticket_service_inst, embedding_service_inst, duplicate_service_inst
):
    mock_llm = FakeLLM(default_delay=0.0)
    return TriageService(
        llm_provider=mock_llm,
        embedding_service=embedding_service_inst,
        duplicate_service=duplicate_service_inst,
        ticket_service=ticket_service_inst,
        confidence_threshold=0.7,
        timeout_seconds=2.0,
    )


# --- 1. TicketService Tests ---


def test_create_ticket_and_idempotency(ticket_service_inst):
    create_req = TicketCreate(
        subject="Payment failed for invoice #123",
        body="I tried paying via credit card but received error code 402.",
        customerEmail="billing@customer.com",
    )

    # 1. First creation
    ticket, created = ticket_service_inst.create_ticket(
        tenant_id="acme", ticket_data=create_req, idempotency_key="idemp_key_1"
    )
    assert created is True
    assert ticket["id"] is not None
    assert ticket["status"] == "queued"
    assert ticket["tenant_id"] == "acme"
    assert ticket["idempotency_key"] == "idemp_key_1"

    # 2. Second creation with same idempotency_key returns existing ticket
    ticket_again, created_again = ticket_service_inst.create_ticket(
        tenant_id="acme", ticket_data=create_req, idempotency_key="idemp_key_1"
    )
    assert created_again is False
    assert ticket_again["id"] == ticket["id"]


def test_tenant_isolation(ticket_service_inst):
    create_req = TicketCreate(
        subject="Confidential account issue",
        body="Sensitive information regarding Acme corp only.",
        customerEmail="admin@acme.com",
    )
    ticket, _ = ticket_service_inst.create_ticket(
        tenant_id="acme", ticket_data=create_req
    )

    # Acquired by acme
    acme_ticket = ticket_service_inst.get_ticket(ticket["id"], tenant_id="acme")
    assert acme_ticket is not None

    # Blocked for beta
    beta_ticket = ticket_service_inst.get_ticket(ticket["id"], tenant_id="beta")
    assert beta_ticket is None


def test_mark_processing_and_stuck_recovery(ticket_service_inst, setup_test_db):
    create_req = TicketCreate(
        subject="Server error 500 on checkout",
        body="Checkout button throws a 500 internal server error.",
        customerEmail="buyer@example.com",
    )
    ticket, _ = ticket_service_inst.create_ticket(
        tenant_id="acme", ticket_data=create_req
    )

    # Mark processing
    marked = ticket_service_inst.mark_processing(ticket["id"])
    assert marked is True

    # Re-marking should fail since status is no longer queued
    assert ticket_service_inst.mark_processing(ticket["id"]) is False

    # Simulate ticket being stuck in processing 40 seconds ago
    with get_db(setup_test_db) as conn:
        conn.execute(
            """
            UPDATE tickets
            SET processing_started_at = datetime('now', '-40 seconds')
            WHERE id = ?
            """,
            (ticket["id"],),
        )

    # Recover stuck tickets (> 30 seconds)
    recovered = ticket_service_inst.reset_stuck_processing_tickets(timeout_seconds=30.0)
    assert recovered == 1

    refreshed = ticket_service_inst.get_ticket(ticket["id"])
    assert refreshed["status"] == "queued"
    assert refreshed["processing_started_at"] is None


# --- 2. EmbeddingService Tests ---


@pytest.mark.asyncio
async def test_embedding_service(embedding_service_inst):
    vec, model = await embedding_service_inst.generate_ticket_embedding(
        subject="Password reset link expired",
        body="Please send another link to reset my account password.",
    )
    assert len(vec) == 256
    assert model == "fake-embed-v1"

    query_vec = await embedding_service_inst.generate_query_embedding("password reset")
    assert len(query_vec) == 256


# --- 3. DuplicateService Tests ---


@pytest.mark.asyncio
async def test_duplicate_detection_and_chain_resolution(
    ticket_service_inst, duplicate_service_inst, embedding_service_inst, setup_test_db
):
    email = "repeat.customer@example.com"
    req_a = TicketCreate(
        subject="Login button broken",
        body="Clicking login does nothing on Chrome browser.",
        customerEmail=email,
    )
    # Ticket A (Root)
    ticket_a, _ = ticket_service_inst.create_ticket("acme", req_a)
    emb_a, model = await embedding_service_inst.generate_ticket_embedding(
        req_a.subject, req_a.body
    )
    ticket_service_inst.update_ticket(
        ticket_a["id"],
        {"embedding": json.dumps(emb_a), "status": TicketStatus.TRIAGED.value},
    )

    # Ticket B (Duplicate of A)
    req_b = TicketCreate(
        subject="Login button broken still",
        body="Clicking login does nothing on Chrome browser today.",
        customerEmail=email,
    )
    ticket_b, _ = ticket_service_inst.create_ticket("acme", req_b)
    emb_b, _ = await embedding_service_inst.generate_ticket_embedding(
        req_b.subject, req_b.body
    )

    dup_id_b, score_b = duplicate_service_inst.find_duplicate(
        tenant_id="acme",
        customer_email=email,
        ticket_id=ticket_b["id"],
        ticket_embedding=emb_b,
        db_path=setup_test_db,
    )
    assert dup_id_b == ticket_a["id"]
    assert score_b >= 0.85

    # Update Ticket B with duplicate_of = ticket_a["id"]
    ticket_service_inst.update_ticket(
        ticket_b["id"],
        {
            "embedding": json.dumps(emb_b),
            "duplicate_of": ticket_a["id"],
            "duplicate_score": score_b,
            "status": TicketStatus.TRIAGED.value,
        },
    )

    # Ticket C (Similar to B -> Must resolve chain to Root Ticket A!)
    req_c = TicketCreate(
        subject="Login button broken again",
        body="Clicking login does nothing on Chrome browser again.",
        customerEmail=email,
    )
    ticket_c, _ = ticket_service_inst.create_ticket("acme", req_c)
    emb_c, _ = await embedding_service_inst.generate_ticket_embedding(
        req_c.subject, req_c.body
    )

    dup_id_c, score_c = duplicate_service_inst.find_duplicate(
        tenant_id="acme",
        customer_email=email,
        ticket_id=ticket_c["id"],
        ticket_embedding=emb_c,
        db_path=setup_test_db,
    )
    # Chain resolution: must resolve to Ticket A, not Ticket B
    assert dup_id_c == ticket_a["id"]
    assert score_c >= 0.85


# --- 4. TriageService Tests ---


@pytest.mark.asyncio
async def test_triage_normal_ticket(ticket_service_inst, triage_service_inst):
    create_req = TicketCreate(
        subject="Cannot pay monthly subscription bill",
        body="My credit card payment failed when renewing subscription.",
        customerEmail="client@corp.com",
    )
    ticket, _ = ticket_service_inst.create_ticket("acme", create_req)

    result = await triage_service_inst.triage_ticket(ticket["id"])
    assert result["status"] == TicketStatus.TRIAGED.value
    assert result["category"] == "billing"
    assert result["priority"] == "low"
    assert result["confidence"] == 0.95
    assert result["triage_error"] is None
    assert result["llm_attempts"] == 1
    assert result["embedding"] is not None


@pytest.mark.asyncio
async def test_triage_malformed_repair_loop(ticket_service_inst, triage_service_inst):
    create_req = TicketCreate(
        subject="[[MALFORMED]] System crash on export",
        body="When exporting reports the application crashes.",
        customerEmail="client@corp.com",
    )
    ticket, _ = ticket_service_inst.create_ticket("acme", create_req)

    # First attempt returns malformed output, second attempt succeeds
    result = await triage_service_inst.triage_ticket(ticket["id"])
    assert result["status"] == TicketStatus.TRIAGED.value
    assert result["category"] == "technical"
    assert result["llm_attempts"] == 2
    assert result["triage_error"] is None


@pytest.mark.asyncio
async def test_triage_always_bad_failure(ticket_service_inst, triage_service_inst):
    create_req = TicketCreate(
        subject="[[ALWAYS_BAD]] Unknown glitch occurring",
        body="This will always return invalid schema.",
        customerEmail="client@corp.com",
    )
    ticket, _ = ticket_service_inst.create_ticket("acme", create_req)

    result = await triage_service_inst.triage_ticket(ticket["id"])
    assert result["status"] == TicketStatus.NEEDS_REVIEW.value
    assert result["triage_error"] == TriageError.SCHEMA_INVALID_AFTER_REPAIR.value
    assert result["llm_attempts"] == 2


@pytest.mark.asyncio
async def test_triage_timeout(ticket_service_inst, setup_test_db):
    fast_timeout_triage = TriageService(
        llm_provider=FakeLLM(default_delay=0.0),
        ticket_service=ticket_service_inst,
        timeout_seconds=0.1,  # short timeout for test
    )
    create_req = TicketCreate(
        subject="[[SLOW]] Gateway timeout on API request",
        body="Takes 15 seconds to reply in mock LLM.",
        customerEmail="client@corp.com",
    )
    ticket, _ = ticket_service_inst.create_ticket("acme", create_req)

    result = await fast_timeout_triage.triage_ticket(
        ticket["id"], db_path=setup_test_db
    )
    assert result["status"] == TicketStatus.NEEDS_REVIEW.value
    assert result["triage_error"] == TriageError.TIMEOUT.value


@pytest.mark.asyncio
async def test_triage_low_confidence(ticket_service_inst, triage_service_inst):
    create_req = TicketCreate(
        subject="[[LOW_CONF]] Ambiguous request",
        body="Not sure what is happening with this feature.",
        customerEmail="client@corp.com",
    )
    ticket, _ = ticket_service_inst.create_ticket("acme", create_req)

    result = await triage_service_inst.triage_ticket(ticket["id"])
    assert result["status"] == TicketStatus.NEEDS_REVIEW.value
    assert result["triage_error"] == TriageError.LOW_CONFIDENCE.value
    assert result["confidence"] == 0.3


# --- 5. TicketWorker Tests ---


@pytest.mark.asyncio
async def test_worker_processing_batch(
    ticket_service_inst, triage_service_inst, setup_test_db
):
    worker = TicketWorker(
        ticket_service=ticket_service_inst,
        triage_service=triage_service_inst,
        poll_interval=0.05,
        db_path=setup_test_db,
    )

    create_req1 = TicketCreate(
        subject="Crash on settings page",
        body="When clicking settings the entire tab crashes.",
        customerEmail="user1@example.com",
    )
    create_req2 = TicketCreate(
        subject="Pricing question on enterprise tier",
        body="How much does the annual enterprise tier cost with billing?",
        customerEmail="user2@example.com",
    )

    t1, _ = ticket_service_inst.create_ticket("acme", create_req1)
    t2, _ = ticket_service_inst.create_ticket("acme", create_req2)

    assert t1["status"] == "queued"
    assert t2["status"] == "queued"

    # Process batch
    processed = await worker.process_queued_batch(batch_size=5)
    assert processed == 2

    # Check updated tickets
    res1 = ticket_service_inst.get_ticket(t1["id"])
    res2 = ticket_service_inst.get_ticket(t2["id"])

    assert res1["status"] == TicketStatus.TRIAGED.value
    assert res1["category"] == "technical"
    assert res2["status"] == TicketStatus.TRIAGED.value
    assert res2["category"] == "billing"


@pytest.mark.asyncio
async def test_worker_lifecycle_start_stop(
    ticket_service_inst, triage_service_inst, setup_test_db
):
    worker = TicketWorker(
        ticket_service=ticket_service_inst,
        triage_service=triage_service_inst,
        poll_interval=0.01,
        db_path=setup_test_db,
    )
    task = worker.start()
    assert worker.is_running is True
    assert task is not None
    await asyncio.sleep(0.05)
    await worker.stop()
    assert worker.is_running is False


@pytest.mark.asyncio
async def test_search_tickets_by_embedding(
    ticket_service_inst, triage_service_inst, embedding_service_inst
):
    # Create tickets
    req1 = TicketCreate(
        subject="Database connection failure",
        body="Database pool timed out and dropped connections.",
        customerEmail="db@acme.com",
    )
    req2 = TicketCreate(
        subject="Invoice billing receipt needed",
        body="Please send the monthly invoice receipt for my company card.",
        customerEmail="finance@acme.com",
    )
    t1, _ = ticket_service_inst.create_ticket("acme", req1)
    t2, _ = ticket_service_inst.create_ticket("acme", req2)

    await triage_service_inst.triage_ticket(t1["id"])
    await triage_service_inst.triage_ticket(t2["id"])

    # Search for database errors
    q_emb = await embedding_service_inst.generate_query_embedding("database connection dropped")
    results = ticket_service_inst.search_tickets_by_embedding("acme", q_emb, limit=2)

    assert len(results) == 2
    assert results[0]["id"] == t1["id"]
    assert results[0]["similarity"] > results[1]["similarity"]

