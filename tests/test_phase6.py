import pytest
from fastapi.testclient import TestClient

from src.app import create_app
from src.db.migrations import init_db
from src.schemas.ticket_schema import TicketStatus
from src.services.ticket_service import ticket_service
from src.services.triage_service import triage_service


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_phase6.db")
    monkeypatch.setattr("src.config.settings.DATABASE_PATH", test_db)
    monkeypatch.setattr("src.config.settings.CELERY_TASK_ALWAYS_EAGER", True)
    init_db(test_db)
    return test_db


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


# --- 1. Authentication & Tenant Isolation ---


def test_auth_missing_header(client):
    res = client.post("/tickets", json={"subject": "Valid subject", "body": "Valid body text here.", "customerEmail": "user@test.com"})
    assert res.status_code == 401
    assert "Missing API key" in res.json().get("detail", "")


def test_auth_invalid_header(client):
    res = client.post(
        "/tickets",
        headers={"x-api-key": "invalid_key_xyz"},
        json={"subject": "Valid subject", "body": "Valid body text here.", "customerEmail": "user@test.com"},
    )
    assert res.status_code == 401
    assert "Invalid API key" in res.json().get("detail", "")


# --- 2. Request Validation & Custom 400 Per-Field Errors ---


def test_validation_errors_return_400_with_per_field_detail(client):
    # Invalid email, subject too short (< 3 chars), body too short (< 10 chars)
    payload = {
        "subject": "Hi",
        "body": "Short",
        "customerEmail": "not-an-email",
    }
    res = client.post("/tickets", headers={"x-api-key": "key_acme"}, json=payload)
    assert res.status_code == 400
    data = res.json()
    assert data["error"] == "validation_error"
    assert "detail" in data
    fields = [d["field"] for d in data["detail"]]
    assert "subject" in fields
    assert "body" in fields
    assert "customerEmail" in fields or "customer_email" in fields


def test_extra_fields_rejected_with_400(client):
    payload = {
        "subject": "Valid ticket subject",
        "body": "Valid ticket body with sufficient length.",
        "customerEmail": "valid@email.com",
        "hacker_field": "injected",
    }
    res = client.post("/tickets", headers={"x-api-key": "key_acme"}, json=payload)
    assert res.status_code == 400
    data = res.json()
    assert data["error"] == "validation_error"
    fields = [d["field"] for d in data["detail"]]
    assert "hacker_field" in fields


# --- 3. POST /tickets ---


def test_create_ticket_async_submission(client):
    payload = {
        "subject": "Cannot complete payment transaction",
        "body": "When clicking submit payment, error code 402 is returned.",
        "customerEmail": "buyer@acme-client.com",
        "channel": "web",
    }
    res = client.post("/tickets", headers={"x-api-key": "key_acme"}, json=payload)
    assert res.status_code == 202
    data = res.json()
    assert "id" in data
    assert data["status"] in ["queued", "triaged"]  # background task runs via TestClient


def test_idempotency_key_deduplication(client):
    payload = {
        "subject": "Duplicate request test ticket",
        "body": "Testing idempotency key deduplication logic in POST /tickets.",
        "customerEmail": "idemp@test.com",
    }
    headers = {"x-api-key": "key_acme", "Idempotency-Key": "unique-idemp-1234"}

    # First request
    res1 = client.post("/tickets", headers=headers, json=payload)
    assert res1.status_code == 202
    ticket_id_1 = res1.json()["id"]

    # Second request with same idempotency key
    res2 = client.post("/tickets", headers=headers, json=payload)
    assert res2.status_code == 202
    ticket_id_2 = res2.json()["id"]

    # Must match the same ticket
    assert ticket_id_1 == ticket_id_2


# --- 4. GET /tickets/{id} & Tenant Isolation ---


def test_get_ticket_by_id_and_tenant_isolation(client):
    # Acme submits ticket
    payload = {
        "subject": "Internal acme secret issue",
        "body": "This ticket belongs exclusively to Acme Corp.",
        "customerEmail": "internal@acme.com",
    }
    create_res = client.post(
        "/tickets", headers={"x-api-key": "key_acme"}, json=payload
    )
    ticket_id = create_res.json()["id"]

    # Acme retrieves ticket -> 200
    acme_res = client.get(
        f"/tickets/{ticket_id}", headers={"x-api-key": "key_acme"}
    )
    assert acme_res.status_code == 200
    ticket_data = acme_res.json()
    assert ticket_data["id"] == ticket_id
    assert ticket_data["tenant_id"] == "acme"
    # Ensure raw embedding array is excluded from response
    assert "embedding" not in ticket_data

    # Beta attempts to retrieve Acme's ticket -> 404 (Tenant Isolation)
    beta_res = client.get(
        f"/tickets/{ticket_id}", headers={"x-api-key": "key_beta"}
    )
    assert beta_res.status_code == 404
    assert beta_res.json()["detail"] == "Ticket not found"


def test_get_ticket_not_found(client):
    res = client.get("/tickets/non-existent-uuid", headers={"x-api-key": "key_acme"})
    assert res.status_code == 404
    assert res.json()["detail"] == "Ticket not found"


# --- 5. GET /tickets/search ---


@pytest.mark.asyncio
async def test_search_tickets_flow(client, setup_test_db):
    # Pre-populate and triage tickets for Acme
    t1, _ = ticket_service.create_ticket(
        tenant_id="acme",
        ticket_data={
            "subject": "Database pool timeout connection error",
            "body": "Postgres connection pool reached maximum capacity and crashed.",
            "customer_email": "dba@acme.com",
            "channel": "web",
        },
        db_path=setup_test_db,
    )
    t2, _ = ticket_service.create_ticket(
        tenant_id="acme",
        ticket_data={
            "subject": "Annual subscription billing invoice request",
            "body": "We need the VAT invoice for our yearly subscription renewal.",
            "customer_email": "accounting@acme.com",
            "channel": "email",
        },
        db_path=setup_test_db,
    )

    await triage_service.triage_ticket(t1["id"], db_path=setup_test_db)
    await triage_service.triage_ticket(t2["id"], db_path=setup_test_db)

    # Search for database errors
    search_res = client.get(
        "/tickets/search?q=database connection crashed&limit=5",
        headers={"x-api-key": "key_acme"},
    )
    assert search_res.status_code == 200
    results = search_res.json()
    assert len(results) >= 2
    # First ranked result must be the database ticket
    assert results[0]["id"] == t1["id"]
    assert results[0]["similarity"] > results[1]["similarity"]
    assert "embedding" not in results[0]


def test_search_validation_errors(client):
    # Query too short (< 2 chars)
    res_short = client.get("/tickets/search?q=a", headers={"x-api-key": "key_acme"})
    assert res_short.status_code == 400

    # Limit too large (> 20)
    res_limit = client.get("/tickets/search?q=database&limit=25", headers={"x-api-key": "key_acme"})
    assert res_limit.status_code == 400


# --- 6. GET /health ---


def test_health_check(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
