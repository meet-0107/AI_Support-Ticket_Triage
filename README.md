# AI Support Ticket Triage Service

A robust, multi-tenant backend service for AI-powered support-ticket triage built with **FastAPI**, **SQLite**, **Pydantic**, **Celery**, and **Pytest**.

The service handles incoming customer support requests, performs background triage using an LLM pipeline with an automatic schema repair loop, detects duplicates across a 7-day sliding window with chain resolution, and supports vector-based semantic search.

---

## 🚀 Key Features

- **Multi-Tenant Security**: Enforces tenant isolation on all operations via `x-api-key` authentication headers (`key_acme`, `key_beta`).
- **Async Queue & Processing**: Accepts incoming tickets with HTTP 202 (`queued`), offloading processing to asynchronous background tasks and Celery workers.
- **Resilient AI Triage Pipeline**:
  - Predicts ticket `category` (`billing`, `technical`, `account`, `feature_request`, `other`), `priority` (`urgent`, `high`, `low`), and generates a concise `summary`.
  - Self-healing repair loop: automatically triggers 1 retry on malformed model outputs before escalating to `needs_review` (`schema_invalid_after_repair`).
  - Graceful handling of timeouts (`timeout`) and low confidence outputs (`low_confidence`).
- **Duplicate Detection & Chain Resolution**:
  - Automatically compares incoming tickets with recent tickets from the same customer over a 7-day window using cosine similarity.
  - Matches $\ge 0.9$ similarity resolve transitively to the canonical root ticket (`duplicate_of`).
- **Semantic Vector Search**: Generates 256-dimensional L2-normalized embeddings to rank and return relevant tickets for any query string.
- **Idempotency Protection**: Optional `Idempotency-Key` header prevents duplicate ticket creation on network retries.
- **Background Worker Engine**:
  - Includes both an in-process asyncio worker and Celery distributed tasks.
  - Built-in crash recovery: automatically resets tickets stuck in `processing` (> 30s) back to `queued`.

---

## 🏛️ System Architecture

A single, clean, end-to-end architecture representing the complete lifecycle: from authenticated client ingestion, asynchronous queuing, and crash recovery, to vector duplicate resolution, self-healing LLM classification, and semantic vector search.

```mermaid
flowchart TD
    %% Styling
    classDef client fill:#e0f2fe,stroke:#0284c7,stroke-width:2px,color:#0369a1;
    classDef api fill:#f0fdf4,stroke:#16a34a,stroke-width:2px,color:#15803d;
    classDef worker fill:#fef3c7,stroke:#d97706,stroke-width:2px,color:#b45309;
    classDef pipeline fill:#f5f3ff,stroke:#7c3aed,stroke-width:2px,color:#6d28d9;
    classDef storage fill:#f1f5f9,stroke:#475569,stroke-width:2px,color:#334155;
    classDef provider fill:#fdf2f8,stroke:#db2777,stroke-width:2px,color:#be185d;
    classDef state fill:#ffffff,stroke:#64748b,stroke-width:2px,stroke-dasharray: 4 4,color:#0f172a;

    subgraph ClientLayer["🌐 Client Layer"]
        Client["Client / CRM / Webhook<br/><code>POST /tickets</code> | <code>GET /tickets/{id}</code> | <code>GET /tickets/search</code>"]:::client
    end

    subgraph APILayer["⚡ FastAPI Gateway Layer"]
        Auth["1. Tenant Auth Guard<br/>Validate <code>x-api-key</code>"]:::api
        Validator["2. Pydantic Validator<br/>Field lengths, types, email"]:::api
        Idemp["3. Idempotency Gate<br/>Check <code>Idempotency-Key</code>"]:::api
    end

    subgraph DBLayer["💾 Persistence Layer (SQLite)"]
        DB[("SQLite Database: tickets.db<br/>Tenants, Tickets, JSON Embeddings")]:::storage
    end

    subgraph WorkerLayer["⚙️ Asynchronous Execution Engine"]
        Worker["BackgroundTasks / Celery Worker<br/>Polls <code>queued</code> &bull; Sets <code>processing</code><br/><i>Stuck ticket auto-recovery (> 30s)</i>"]:::worker
    end

    subgraph PipelineLayer["🧠 Core AI Triage Pipeline"]
        Embed["Stage 1: Vector Embedding<br/>256-dim L2-normalized float vector"]:::pipeline
        Dup["Stage 2: Duplicate Detection<br/>7-Day window &bull; Cosine &ge; 0.90<br/>Transitive chain resolution"]:::pipeline
        LLM["Stage 3: LLM Classification & Self-Healing Loop<br/>Classify category, priority & summary<br/>5s Timeout &bull; 1x Auto-Repair Retry on invalid schema"]:::pipeline
    end

    subgraph ModelLayer["🤖 AI Providers"]
        Models["LLM & Embedding Models<br/>Gemini / Ollama / Local / Mock"]:::provider
    end

    subgraph Outcomes["📊 Final Ticket States"]
        Triaged["Status: triaged<br/>Valid schema & Confidence &ge; 0.70"]:::state
        Review["Status: needs_review<br/>Timeout / Invalid schema / Low confidence"]:::state
    end

    %% Flows
    Client -->|"POST /tickets (x-api-key)"| Auth
    Auth --> Validator
    Validator --> Idemp
    Idemp -->|"Replay detected"| Client
    Idemp -->|"Insert status: queued"| DB
    Idemp -->|"HTTP 202 Accepted {id, status}"| Client

    DB -.->|"Picks queued ticket"| Worker
    Worker --> Embed
    Embed <-->|"Generate embedding"| Models
    Embed --> Dup
    Dup <-->|"Query 7-day customer history"| DB
    Dup --> LLM
    LLM <-->|"Classify with schema"| Models

    LLM -->|"Success"| Triaged
    LLM -->|"Fallback"| Review
    Triaged -->|"Persist metadata"| DB
    Review -->|"Persist metadata"| DB

    Client <-->|"GET /tickets/search (Cosine ranking)"| DB
```

---

## 📁 Directory Structure

```text
.
├── .env                         # Environment variable configurations
├── .env.example                 # Example template for environment variables
├── README.md                    # Project documentation, instructions & trade-offs writeup
├── requirements.txt             # Python dependencies
├── IMPLEMENTATION_PLAN.md       # Step-by-step architecture & execution plan
│
├── src/
│   ├── app.py                   # FastAPI application setup & middleware/router mounting
│   ├── server.py                # Server startup script, DB initialization & background worker trigger
│   ├── config.py                # Pydantic BaseSettings management & Upstash Redis configuration
│   │
│   ├── routes/
│   │   ├── __init__.py
│   │   └── ticket_routes.py     # FastAPI routes: POST /tickets, GET /tickets/{id}, GET /tickets/search
│   │
│   ├── controllers/
│   │   ├── __init__.py
│   │   └── ticket_controller.py # Endpoint logic, Pydantic validation & response formatting
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── ticket_service.py    # Database operations & CRUD for tickets
│   │   ├── triage_service.py    # Core triage execution pipeline & repair loop
│   │   ├── embedding_service.py # Wrapper for text embedding generation
│   │   └── duplicate_service.py # Duplicate candidate query, cosine scoring & chain resolution
│   │
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── llm_provider.py      # LLMProvider supporting configured providers (Gemini, Ollama)
│   │   └── embedding_provider.py# EmbeddingProvider supporting configured providers (Gemini, Ollama)
│   │
│   ├── workers/
│   │   ├── __init__.py
│   │   ├── celery_app.py        # Celery application initialization & broker configuration
│   │   ├── celery_worker.py     # Celery tasks: triage_ticket_task, recover_stuck_tickets_task
│   │   └── ticket_worker.py     # Async background worker loop & celery dispatch helper
│   │
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── ticket_schema.py     # Pydantic schemas for request validation (POST /tickets)
│   │   └── llm_output_schema.py # Pydantic schemas for runtime LLM output validation
│   │
│   ├── middleware/
│   │   ├── __init__.py
│   │   └── auth.py              # Tenant authentication dependency (x-api-key check)
│   │
│   ├── db/
│   │   ├── __init__.py
│   │   ├── database.py          # SQLite database connection manager
│   │   └── migrations.py        # SQLite schema initialization & tenant seeding
│   │
│   └── utils/
│       ├── __init__.py
│       ├── cosine.py            # Cosine similarity calculation helper
│       └── timeout.py           # Asyncio timeout wrapper helper (5s limit)
│
└── tests/
    ├── test_db.py               # Database migrations & connection tests
    ├── test_phase3.py           # Utilities, mock LLM, and embedder tests
    ├── test_phase4.py           # Schemas, validation rules, and auth tests
    ├── test_phase5.py           # Services, duplicate resolution & worker tests
    ├── test_phase6.py           # Controllers, routes & error formatting tests
    ├── test_celery.py           # Celery tasks & execution tests
    └── test_tickets.py          # Complete end-to-end integration test suite
```

---

## 🛠 Installation & Setup

### 1. Prerequisites
- Python 3.10+ (tested with Python 3.14)
- Pip / Virtual environment (recommended)

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Environment Variables
Create a `.env` file from `.env.example`:
```bash
cp .env.example .env
```
Default configuration:
```env
DATABASE_PATH=tickets.db
DUPLICATE_THRESHOLD=0.9
LLM_TIMEOUT_SECONDS=5.0
LLM_PROVIDER=mock
EMBEDDING_PROVIDER=mock
HOST=0.0.0.0
PORT=8000
CONFIDENCE_THRESHOLD=0.7
STUCK_TIMEOUT_SECONDS=30.0
```

---

## 🖥 Running the Application

### 1. Start the FastAPI Server
```bash
python -m src.server
```
The server will initialize the SQLite schema, seed tenants (`acme`, `beta`), and start listening on `http://localhost:8000`.

Interactive Swagger documentation is available at:
👉 **`http://localhost:8000/docs`**

### 2. Run the Celery Worker (Optional)
If using Celery for distributed background processing:
```bash
celery -A src.workers.celery_worker.celery_app worker --loglevel=info -P solo
```
*(Note: `-P solo` is used on Windows to run tasks safely.)*

---

## 🧪 Running Tests

Execute the full suite of 72 automated tests:

```bash
python -m pytest
```

Run tests with detailed output:
```bash
python -m pytest -v
```

---

## 📡 API Reference & `curl` Examples

### Seed Tenants
| Tenant ID | Name | API Key |
| :--- | :--- | :--- |
| `acme` | Acme Corp | `key_acme` |
| `beta` | Beta Inc | `key_beta` |

---

### 1. Submit a Support Ticket (`POST /tickets`)
Returns **HTTP 202 Accepted** and queues background triage.

```bash
curl -X POST "http://localhost:8000/tickets" \
  -H "Content-Type: application/json" \
  -H "x-api-key: key_acme" \
  -H "Idempotency-Key: idemp-req-101" \
  -d '{
    "subject": "Cannot complete credit card payment for invoice",
    "body": "When I enter my billing details, the page returns error 402 payment required.",
    "customerEmail": "finance@client.com",
    "channel": "web"
  }'
```

**Response (202 Accepted):**
```json
{
  "id": "e2a5f576-9051-4f10-9b48-1ff4bb121287",
  "status": "queued"
}
```

---

### 2. Retrieve Ticket Details (`GET /tickets/{id}`)
Fetches ticket status and triage outcome (raw embedding array is omitted).

```bash
curl -X GET "http://localhost:8000/tickets/e2a5f576-9051-4f10-9b48-1ff4bb121287" \
  -H "x-api-key: key_acme"
```

**Response (200 OK):**
```json
{
  "id": "e2a5f576-9051-4f10-9b48-1ff4bb121287",
  "tenant_id": "acme",
  "idempotency_key": "idemp-req-101",
  "subject": "Cannot complete credit card payment for invoice",
  "body": "When I enter my billing details, the page returns error 402 payment required.",
  "customer_email": "finance@client.com",
  "channel": "web",
  "status": "triaged",
  "category": "billing",
  "priority": "low",
  "summary": "Summary: Cannot complete credit card payment for invoice",
  "confidence": 0.95,
  "triage_error": null,
  "llm_attempts": 1,
  "duplicate_of": null,
  "duplicate_score": null,
  "created_at": "2026-10-07 13:00:00",
  "updated_at": "2026-10-07 13:00:01"
}
```

---

### 3. Semantic Search (`GET /tickets/search`)
Ranks triaged tickets by cosine similarity against the query.

```bash
curl -X GET "http://localhost:8000/tickets/search?q=payment+invoice+error&limit=5" \
  -H "x-api-key: key_acme"
```

**Response (200 OK):**
```json
[
  {
    "id": "e2a5f576-9051-4f10-9b48-1ff4bb121287",
    "subject": "Cannot complete credit card payment for invoice",
    "body": "When I enter my billing details, the page returns error 402 payment required.",
    "category": "billing",
    "priority": "low",
    "status": "triaged",
    "created_at": "2026-10-07 13:00:00",
    "similarity": 0.9421
  }
]
```

---

### 4. Validation Error Example (HTTP 400 with Per-Field Details)
Submitting invalid fields or unknown parameters:

```bash
curl -X POST "http://localhost:8000/tickets" \
  -H "Content-Type: application/json" \
  -H "x-api-key: key_acme" \
  -d '{
    "subject": "Hi",
    "body": "Short",
    "customerEmail": "invalid-email",
    "unknownField": "not_allowed"
  }'
```

**Response (400 Bad Request):**
```json
{
  "error": "validation_error",
  "message": "Invalid request payload or parameters",
  "detail": [
    {
      "field": "subject",
      "message": "String should have at least 3 characters",
      "type": "string_too_short"
    },
    {
      "field": "body",
      "message": "String should have at least 10 characters",
      "type": "string_too_short"
    },
    {
      "field": "customerEmail",
      "message": "value is not a valid email address: ...",
      "type": "value_error"
    },
    {
      "field": "unknownField",
      "message": "Extra inputs are not permitted",
      "type": "extra_forbidden"
    }
  ]
}
```

---

### 5. Multi-Tenant Isolation Enforcement (HTTP 404)
When tenant `beta` attempts to access a ticket owned by `acme`:

```bash
curl -X GET "http://localhost:8000/tickets/e2a5f576-9051-4f10-9b48-1ff4bb121287" \
  -H "x-api-key: key_beta"
```

**Response (404 Not Found):**
```json
{
  "detail": "Ticket not found"
}
```

---

### 6. Authentication Failure (HTTP 401)
Request without `x-api-key` header:

```bash
curl -X GET "http://localhost:8000/tickets/some-id"
```

**Response (401 Unauthorized):**
```json
{
  "detail": "Missing API key"
}
```

---


