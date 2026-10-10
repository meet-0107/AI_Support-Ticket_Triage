import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.db.migrations import init_db
from src.middleware.auth import get_current_tenant, get_tenant_by_api_key
from src.schemas import (
    LLMOutputSchema,
    TicketCategory,
    TicketChannel,
    TicketCreate,
    TicketPriority,
    TicketStatus,
)


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_tickets.db")
    monkeypatch.setattr("src.config.settings.DATABASE_PATH", test_db)
    init_db(test_db)
    return test_db


# --- TicketCreate Schema Tests ---


def test_ticket_create_valid_camel_case():
    data = {
        "subject": "Can't log into dashboard",
        "body": "When I enter my credentials, the screen remains blank.",
        "customerEmail": "  User.Name@Example.COM  ",
        "channel": "email",
    }
    ticket = TicketCreate(**data)
    assert ticket.subject == "Can't log into dashboard"
    assert ticket.body == "When I enter my credentials, the screen remains blank."
    assert ticket.customer_email == "user.name@example.com"
    assert ticket.customerEmail == "user.name@example.com"
    assert ticket.channel == TicketChannel.EMAIL


def test_ticket_create_default_channel():
    data = {
        "subject": "Need help with API key",
        "body": "How can I rotate my existing API key securely?",
        "customerEmail": "admin@tenant.com",
    }
    ticket = TicketCreate(**data)
    assert ticket.channel == TicketChannel.WEB


def test_ticket_create_extra_fields_forbidden():
    data = {
        "subject": "Test ticket",
        "body": "Valid body text here for testing.",
        "customerEmail": "valid@email.com",
        "unexpected_field": "intruder",
    }
    with pytest.raises(ValidationError) as exc_info:
        TicketCreate(**data)
    assert "extra_forbidden" in str(exc_info.value)


def test_ticket_create_invalid_email():
    data = {
        "subject": "Broken billing button",
        "body": "Valid body text here for testing.",
        "customerEmail": "not-an-email",
    }
    with pytest.raises(ValidationError) as exc_info:
        TicketCreate(**data)
    assert "customerEmail" in str(exc_info.value) or "customer_email" in str(exc_info.value)


def test_ticket_create_subject_length_limits():
    # Too short (< 3 chars)
    with pytest.raises(ValidationError):
        TicketCreate(
            subject="Hi",
            body="Valid body text here for testing.",
            customerEmail="test@test.com",
        )

    # Max length valid (150 chars)
    ticket_valid = TicketCreate(
        subject="a" * 150,
        body="Valid body text here for testing.",
        customerEmail="test@test.com",
    )
    assert len(ticket_valid.subject) == 150

    # Too long (> 150 chars)
    with pytest.raises(ValidationError):
        TicketCreate(
            subject="a" * 151,
            body="Valid body text here for testing.",
            customerEmail="test@test.com",
        )


def test_ticket_create_body_length_limits():
    # Too short (< 10 chars)
    with pytest.raises(ValidationError):
        TicketCreate(
            subject="Valid subject",
            body="Too short",
            customerEmail="test@test.com",
        )

    # Max length valid (5000 chars)
    ticket_valid = TicketCreate(
        subject="Valid subject",
        body="b" * 5000,
        customerEmail="test@test.com",
    )
    assert len(ticket_valid.body) == 5000

    # Too long (> 5000 chars)
    with pytest.raises(ValidationError):
        TicketCreate(
            subject="Valid subject",
            body="b" * 5001,
            customerEmail="test@test.com",
        )


def test_ticket_create_invalid_channel():
    with pytest.raises(ValidationError):
        TicketCreate(
            subject="Valid subject",
            body="Valid body text here for testing.",
            customerEmail="test@test.com",
            channel="carrier_pigeon",
        )


# --- LLMOutputSchema Tests ---


def test_llm_output_schema_valid():
    data = {
        "category": "technical",
        "priority": "urgent",
        "summary": "Database server unresponsive",
        "confidence": 0.98,
    }
    output = LLMOutputSchema(**data)
    assert output.category == TicketCategory.TECHNICAL
    assert output.priority == TicketPriority.URGENT
    assert output.summary == "Database server unresponsive"
    assert output.confidence == 0.98


def test_llm_output_schema_invalid_category():
    data = {
        "category": "not_a_category",
        "priority": "low",
        "summary": "Some summary",
        "confidence": 0.9,
    }
    with pytest.raises(ValidationError):
        LLMOutputSchema(**data)


def test_llm_output_schema_invalid_priority():
    data = {
        "category": "billing",
        "priority": "super_urgent",
        "summary": "Some summary",
        "confidence": 0.9,
    }
    with pytest.raises(ValidationError):
        LLMOutputSchema(**data)


def test_llm_output_schema_summary_length_bounds():
    # Empty summary (< 1 char)
    with pytest.raises(ValidationError):
        LLMOutputSchema(
            category="billing",
            priority="low",
            summary="",
            confidence=0.5,
        )

    # Exceeding 200 chars
    with pytest.raises(ValidationError):
        LLMOutputSchema(
            category="billing",
            priority="low",
            summary="x" * 201,
            confidence=0.5,
        )


def test_llm_output_schema_confidence_bounds():
    # Negative confidence
    with pytest.raises(ValidationError):
        LLMOutputSchema(
            category="billing",
            priority="low",
            summary="Valid summary",
            confidence=-0.1,
        )

    # Exceeding 1.0
    with pytest.raises(ValidationError):
        LLMOutputSchema(
            category="billing",
            priority="low",
            summary="Valid summary",
            confidence=1.05,
        )


# --- Auth Middleware Tests ---


def test_get_tenant_by_api_key_success(setup_test_db):
    tenant_acme = get_tenant_by_api_key("key_acme", setup_test_db)
    assert tenant_acme == "acme"

    tenant_beta = get_tenant_by_api_key("key_beta", setup_test_db)
    assert tenant_beta == "beta"


def test_get_tenant_by_api_key_missing(setup_test_db):
    with pytest.raises(HTTPException) as exc_info:
        get_tenant_by_api_key(None, setup_test_db)
    assert exc_info.value.status_code == 401
    assert "Missing API key" in exc_info.value.detail


def test_get_tenant_by_api_key_invalid(setup_test_db):
    with pytest.raises(HTTPException) as exc_info:
        get_tenant_by_api_key("non_existent_key", setup_test_db)
    assert exc_info.value.status_code == 401
    assert "Invalid API key" in exc_info.value.detail


def test_fastapi_auth_dependency(setup_test_db):
    app = FastAPI()

    @app.get("/test-protected")
    def protected_route(tenant_id: str = Depends(get_current_tenant)):
        return {"tenant_id": tenant_id}

    client = TestClient(app)

    # Without header -> 401
    resp_no_header = client.get("/test-protected")
    assert resp_no_header.status_code == 401

    # With invalid header -> 401
    resp_bad_key = client.get(
        "/test-protected", headers={"x-api-key": "invalid_key_xyz"}
    )
    assert resp_bad_key.status_code == 401

    # With valid header for acme -> 200
    resp_acme = client.get(
        "/test-protected", headers={"x-api-key": "key_acme"}
    )
    assert resp_acme.status_code == 200
    assert resp_acme.json() == {"tenant_id": "acme"}

    # Case-insensitive header check: X-API-KEY
    resp_beta = client.get(
        "/test-protected", headers={"X-API-KEY": "key_beta"}
    )
    assert resp_beta.status_code == 200
    assert resp_beta.json() == {"tenant_id": "beta"}
