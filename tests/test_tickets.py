import asyncio
import json
import pytest
from fastapi.testclient import TestClient

from src.app import create_app
from src.db.database import get_db
from src.db.migrations import init_db
from tests.conftest import FakeLLM
from src.schemas.ticket_schema import TicketStatus, TriageError
from src.services.ticket_service import ticket_service
from src.services.triage_service import TriageService, triage_service
from src.workers.ticket_worker import TicketWorker


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_tickets_suite.db")
    monkeypatch.setattr("src.config.settings.DATABASE_PATH", test_db)
    monkeypatch.setattr("src.config.settings.CELERY_TASK_ALWAYS_EAGER", True)
    init_db(test_db)
    return test_db


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


# --- 1. Tenant Isolation & x-api-key Auth ---


def test_tenant_auth_and_isolation(client):
    # No auth header -> 401
    res_no_auth = client.post(
        "/tickets",
        json={
            "subject": "Missing auth header",
            "body": "Checking that unauthenticated requests fail.",
            "customerEmail": "user@example.com",
        },
    )
    assert res_no_auth.status_code == 401

    # Invalid auth header -> 401
    res_bad_auth = client.post(
        "/tickets",
        headers={"x-api-key": "fake_invalid_key"},
        json={
            "subject": "Bad auth header",
            "body": "Checking that bad key requests fail.",
            "customerEmail": "user@example.com",
        },
    )
    assert res_bad_auth.status_code == 401

    # Acme submits ticket -> 202
    create_res = client.post(
        "/tickets",
        headers={"x-api-key": "key_acme"},
        json={
            "subject": "Acme proprietary issue",
            "body": "Confidential Acme system configuration bug.",
            "customerEmail": "admin@acme.com",
        },
    )
    assert create_res.status_code == 202
    ticket_id = create_res.json()["id"]

    # Acme can read its own ticket -> 200
    acme_get = client.get(f"/tickets/{ticket_id}", headers={"x-api-key": "key_acme"})
    assert acme_get.status_code == 200
    assert acme_get.json()["tenant_id"] == "acme"
    assert "embedding" not in acme_get.json()

    # Beta attempts to read Acme's ticket -> 404 (Isolation)
    beta_get = client.get(f"/tickets/{ticket_id}", headers={"x-api-key": "key_beta"})
    assert beta_get.status_code == 404
    assert beta_get.json()["detail"] == "Ticket not found"


# --- 2. Request Validation (HTTP 400 with Per-Field Errors) ---


def test_request_validation_per_field_errors(client):
    # Bad email, subject too short (<3), body too short (<10)
    bad_payload = {
        "subject": "Ab",
        "body": "Short",
        "customerEmail": "not-an-email-address",
    }
    res = client.post("/tickets", headers={"x-api-key": "key_acme"}, json=bad_payload)
    assert res.status_code == 400
    data = res.json()
    assert data["error"] == "validation_error"
    error_fields = {e["field"] for e in data["detail"]}
    assert "subject" in error_fields
    assert "body" in error_fields
    assert "customerEmail" in error_fields or "customer_email" in error_fields


def test_extra_unknown_fields_rejected_400(client):
    payload_with_extra = {
        "subject": "Valid subject title",
        "body": "Valid body text exceeding minimum required characters.",
        "customerEmail": "user@domain.com",
        "forbidden_extra_param": "malicious_payload",
    }
    res = client.post(
        "/tickets", headers={"x-api-key": "key_acme"}, json=payload_with_extra
    )
    assert res.status_code == 400
    data = res.json()
    assert data["error"] == "validation_error"
    error_fields = {e["field"] for e in data["detail"]}
    assert "forbidden_extra_param" in error_fields


# --- 3. Async Ticket Submission (HTTP 202) ---


def test_async_ticket_submission_lifecycle(client):
    payload = {
        "subject": "Database connection error on query",
        "body": "Database timeout while executing analytical SQL query.",
        "customerEmail": "developer@client.com",
        "channel": "web",
    }
    res = client.post("/tickets", headers={"x-api-key": "key_acme"}, json=payload)
    assert res.status_code == 202
    data = res.json()
    assert "id" in data
    assert data["status"] in ["queued", "triaged"]

    # Since TestClient executes background tasks, the ticket is triaged immediately
    ticket = client.get(f"/tickets/{data['id']}", headers={"x-api-key": "key_acme"}).json()
    assert ticket["status"] == TicketStatus.TRIAGED.value
    assert ticket["category"] == "technical"
    assert ticket["priority"] in ["high", "urgent", "low"]


# --- 4. LLM Repair Path ([[MALFORMED]]) ---


@pytest.mark.asyncio
async def test_llm_repair_path_malformed(client, setup_test_db):
    payload = {
        "subject": "[[MALFORMED]] Billing invoice payment not credited",
        "body": "I paid invoice #456 but credit card payment was charged twice.",
        "customerEmail": "billing@customer.com",
    }
    res = client.post("/tickets", headers={"x-api-key": "key_acme"}, json=payload)
    assert res.status_code == 202
    ticket_id = res.json()["id"]

    # Execute triage with repair
    triaged = await triage_service.triage_ticket(ticket_id, db_path=setup_test_db)
    assert triaged["status"] == TicketStatus.TRIAGED.value
    assert triaged["category"] == "billing"
    assert triaged["llm_attempts"] == 2
    assert triaged["triage_error"] is None


# --- 5. Permanent Failure ([[ALWAYS_BAD]]) ---


@pytest.mark.asyncio
async def test_llm_permanent_failure_always_bad(client, setup_test_db):
    payload = {
        "subject": "[[ALWAYS_BAD]] Internal server failure occurred",
        "body": "This ticket always generates malformed LLM responses.",
        "customerEmail": "ops@customer.com",
    }
    res = client.post("/tickets", headers={"x-api-key": "key_acme"}, json=payload)
    assert res.status_code == 202
    ticket_id = res.json()["id"]

    triaged = await triage_service.triage_ticket(ticket_id, db_path=setup_test_db)
    assert triaged["status"] == TicketStatus.NEEDS_REVIEW.value
    assert triaged["triage_error"] == TriageError.SCHEMA_INVALID_AFTER_REPAIR.value
    assert triaged["llm_attempts"] == 2


# --- 6. Timeout Handling ([[SLOW]]) ---


@pytest.mark.asyncio
async def test_llm_timeout_handling(client, setup_test_db):
    fast_triage = TriageService(
        llm_provider=FakeLLM(default_delay=0.0),
        timeout_seconds=0.1,  # Short timeout triggers TimeoutError on [[SLOW]]
    )
    payload = {
        "subject": "[[SLOW]] System performance bottleneck",
        "body": "Mock LLM simulates a 15-second response delay.",
        "customerEmail": "perf@customer.com",
    }
    res = client.post("/tickets", headers={"x-api-key": "key_acme"}, json=payload)
    ticket_id = res.json()["id"]

    triaged = await fast_triage.triage_ticket(ticket_id, db_path=setup_test_db)
    assert triaged["status"] == TicketStatus.NEEDS_REVIEW.value
    assert triaged["triage_error"] == TriageError.TIMEOUT.value


# --- 7. Low Confidence Handling ([[LOW_CONF]]) ---


@pytest.mark.asyncio
async def test_low_confidence_handling(client, setup_test_db):
    payload = {
        "subject": "[[LOW_CONF]] Unclear ticket description",
        "body": "User submitted vague notes regarding unexpected system state.",
        "customerEmail": "vague@customer.com",
    }
    res = client.post("/tickets", headers={"x-api-key": "key_acme"}, json=payload)
    ticket_id = res.json()["id"]

    triaged = await triage_service.triage_ticket(ticket_id, db_path=setup_test_db)
    assert triaged["status"] == TicketStatus.NEEDS_REVIEW.value
    assert triaged["triage_error"] == TriageError.LOW_CONFIDENCE.value
    assert triaged["confidence"] == 0.3


# --- 8. Duplicate Detection & Chain Resolution ---


@pytest.mark.asyncio
async def test_duplicate_detection_and_chain_resolution(client, setup_test_db):
    email = "same.user@domain.com"
    base_body = "Receiving 403 Forbidden error on credentials submit when accessing company dashboard with chrome browser."

    # Ticket 1: Root Ticket
    res1 = client.post(
        "/tickets",
        headers={"x-api-key": "key_acme"},
        json={
            "subject": "Cannot log in to web portal",
            "body": base_body,
            "customerEmail": email,
        },
    )
    t1_id = res1.json()["id"]

    # Ticket 2: Duplicate of Ticket 1 (> 0.9 similarity)
    res2 = client.post(
        "/tickets",
        headers={"x-api-key": "key_acme"},
        json={
            "subject": "Cannot log in to web portal",
            "body": base_body + " Please assist.",
            "customerEmail": email,
        },
    )
    t2_id = res2.json()["id"]
    t2_data = client.get(f"/tickets/{t2_id}", headers={"x-api-key": "key_acme"}).json()
    assert t2_data["duplicate_of"] == t1_id
    assert t2_data["duplicate_score"] >= 0.90

    # Ticket 3: Duplicate of Ticket 2 -> Chain Resolution must point to Root (Ticket 1)
    res3 = client.post(
        "/tickets",
        headers={"x-api-key": "key_acme"},
        json={
            "subject": "Cannot log in to web portal",
            "body": base_body + " Please assist again.",
            "customerEmail": email,
        },
    )
    t3_id = res3.json()["id"]
    t3_data = client.get(f"/tickets/{t3_id}", headers={"x-api-key": "key_acme"}).json()
    assert t3_data["duplicate_of"] == t1_id  # Resolved to root Ticket 1
    assert t3_data["duplicate_score"] >= 0.90


# --- 9. Idempotency Key Deduplication ---


def test_idempotency_key_deduplication_e2e(client):
    payload = {
        "subject": "Idempotent payment notification",
        "body": "Customer confirmed payment received via webhook.",
        "customerEmail": "payments@client.com",
    }
    idem_headers = {
        "x-api-key": "key_acme",
        "Idempotency-Key": "idemp-key-xyz-789",
    }

    # Request 1
    resp1 = client.post("/tickets", headers=idem_headers, json=payload)
    assert resp1.status_code == 202
    ticket_id_1 = resp1.json()["id"]

    # Request 2 (Duplicate with same idempotency key)
    resp2 = client.post("/tickets", headers=idem_headers, json=payload)
    assert resp2.status_code == 202
    ticket_id_2 = resp2.json()["id"]

    # Must deduplicate to same ticket ID
    assert ticket_id_1 == ticket_id_2


# --- 10. Semantic Search Query Ranking ---


@pytest.mark.asyncio
async def test_semantic_search_ranking(client, setup_test_db):
    # Submit technical database crash ticket
    r_tech = client.post(
        "/tickets",
        headers={"x-api-key": "key_acme"},
        json={
            "subject": "PostgreSQL database memory crash",
            "body": "Out of memory error caused Postgres daemon to crash and terminate.",
            "customerEmail": "tech@corp.com",
        },
    )
    tech_id = r_tech.json()["id"]
    await triage_service.triage_ticket(tech_id, db_path=setup_test_db)

    # Submit billing invoice refund ticket
    r_bill = client.post(
        "/tickets",
        headers={"x-api-key": "key_acme"},
        json={
            "subject": "Monthly subscription refund request",
            "body": "Customer requested full refund for duplicate credit card charge.",
            "customerEmail": "finance@corp.com",
        },
    )
    bill_id = r_bill.json()["id"]
    await triage_service.triage_ticket(bill_id, db_path=setup_test_db)

    # Search for technical database crash
    tech_search = client.get(
        "/tickets/search?q=database memory crash&limit=5",
        headers={"x-api-key": "key_acme"},
    )
    assert tech_search.status_code == 200
    results = tech_search.json()
    assert len(results) >= 2
    assert results[0]["id"] == tech_id
    assert results[0]["similarity"] > results[1]["similarity"]

    # Search for billing refund
    bill_search = client.get(
        "/tickets/search?q=credit card refund subscription&limit=5",
        headers={"x-api-key": "key_acme"},
    )
    assert bill_search.status_code == 200
    bill_results = bill_search.json()
    assert len(bill_results) >= 2
    assert bill_results[0]["id"] == bill_id
    assert bill_results[0]["similarity"] > bill_results[1]["similarity"]
