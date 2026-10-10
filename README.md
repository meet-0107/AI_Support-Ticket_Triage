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

## 🏛️ End-to-End System Architecture

Comprehensive architecture diagrams illustrating the complete, end-to-end working flow of the **AI Support Ticket Triage Service**: from authenticated client ingestion, asynchronous queueing, and crash recovery, through vector duplicate resolution, self-healing LLM triage, and semantic search retrieval.

<div align="center">
  <img src="assets/architecture_diagram.svg" alt="AI Support Ticket Triage - End-to-End System Architecture" width="100%"/>
</div>

---

### 1. Visual Architecture Blueprint

```text
+-------------------------------------------------------------------------------------------------------------------+
|                                 AI SUPPORT TICKET TRIAGE - SYSTEM ARCHITECTURE                                    |
+-------------------------------------------------------------------------------------------------------------------+
|                                                                                                                   |
|  [ CLIENT LAYER ]                                                                                                 |
|   Support Portal / CRM / Webhook Integration                                                                      |
|         │                                                                                                         |
|         │ POST /tickets (x-api-key: key_acme, Idempotency-Key: uuid, JSON payload)                                |
|         ▼                                                                                                         |
|  [ FASTAPI APPLICATION LAYER ]                                                                                    |
|  ┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────┐  |
|  │  1. Multi-Tenant Auth Guard: Validate 'x-api-key' against 'tenants' table (key_acme, key_beta)                │  |
|  │  2. Payload Validation: Pydantic TicketCreate schema (subject >= 3, body >= 10, valid customerEmail)          │  |
|  │  3. Idempotency Gate: If Idempotency-Key exists, return cached ticket record immediately                   │  |
|  │  4. Enqueue Ticket: INSERT INTO tickets (status = 'queued') ────────────────────────┐                      │  |
|  │  5. Immediate Client Response: HTTP 202 Accepted { "id": "uuid", "status": "queued" }                      │  |
|  └───────────────────────────────────────────────────┬─────────────────────────────────│──────────────────────┘  |
|                                                      │ (async dispatch)                │ (INSERT status: queued)  |
|                                                      ▼                                 ▼                          |
|  [ ASYNC WORKER ENGINE ]                                                    [ PERSISTENCE LAYER ]                 |
|  ┌──────────────────────────────────────────────────────────┐               ┌──────────────────────────────────┐  |
|  │ • In-Process Asyncio Worker (ticket_worker.py polling)   │               │ SQLite Database (tickets.db)     │  |
|  │ • Distributed Celery Worker (celery_worker.py via Redis) │ ◄────────────►│  • tenants table (isolation)     │  |
|  │ • Stuck Ticket Crash Recovery (> 30s processing -> reset)│               │  • tickets table                 │  |
|  └──────────────────────────┬───────────────────────────────┘               │    - status, category, priority  │  |
|                             │ Marks status = 'processing'                   │    - 256-dim embedding (JSON)    │  |
|                             ▼                                               │    - duplicate_of, score         │  |
|  [ CORE TRIAGE PIPELINE (TriageService) ]                                   └────────────────┬─────────────────┘  |
|  ┌───────────────────────────────────────────────────────────────────────────────────────────│─────────────────┐  |
|  │                                                                                           │                 │  |
|  │  STAGE 1: VECTOR EMBEDDING GENERATION                                                     │                 │  |
|  │  Concatenate subject + body  ──►  Generate 256-dimensional L2-normalized float vector     │                 │  |
|  │                                                                                           │                 │  |
|  │  STAGE 2: DUPLICATE DETECTION & TRANSITIVE CHAIN RESOLUTION                              │                 │  |
|  │  1. Query customer tickets within 7-day sliding window  ──────────────────────────────────┘                 │  |
|  │  2. Calculate Cosine Similarity against recent candidate embeddings                                         │  |
|  │  3. If similarity >= 0.90: Traverse duplicate_of links to canonical root ticket                             │  |
|  │     (Example: C -> B and B -> A  ==>  C resolves directly to canonical root ticket A)                      │  |
|  │                                                                                                             │  |
|  │  STAGE 3: LLM CLASSIFICATION & SELF-HEALING REPAIR LOOP                                                     │  |
|  │  • Attempt 1: Call LLM with strict JSON schema (5.0s timeout limit)                                         │  |
|  │  • Timeout or Provider error? ──► Escalate to 'needs_review' (triage_error: 'timeout')                      │  |
|  │  • Schema ValidationError?    ──► Attempt 2: Auto-inject repair prompt with schema instructions            │  |
|  │  • Still invalid schema?      ──► Escalate to 'needs_review' (triage_error: 'schema_invalid_after_repair') │  |
|  │  • Confidence score < 0.70?   ──► Escalate to 'needs_review' (triage_error: 'low_confidence')              │  |
|  │  • Valid & Confidence >= 0.70 ──► Mark status = 'triaged' (assign category, priority, summary)              │  |
|  │                                                                                                             │  |
|  │  STAGE 4: PERSISTENCE & UPDATES                                                                             │  |
|  │  UPDATE tickets SET status, category, priority, summary, confidence, embedding, duplicate_of...             │  |
|  └─────────────────────────────────────────────────────────────────────────────────────────────────────────────┘  |
|                                                                                                                   |
|  [ READ & SEMANTIC SEARCH ]                                                                                       |
|   • GET /tickets/{id}         ──► Returns ticket status & triage metadata (raw vector omitted)                    |
|   • GET /tickets/search?q=... ──► Generates 256-dim query embedding & ranks tickets by Cosine Similarity          |
+-------------------------------------------------------------------------------------------------------------------+
```

---

### 2. High-Level Component Flowchart (Interactive)

```mermaid
flowchart TB
    subgraph Clients["🌐 Clients & Integrations"]
        Client["Client Application / Support Portal / Webhook"]
    end

    subgraph APIGateway["⚡ FastAPI Application Layer"]
        AuthMiddleware["Tenant Auth Dependency<br/><code>x-api-key: key_acme | key_beta</code>"]
        IdempMiddleware["Idempotency Gate<br/><code>Idempotency-Key Header</code>"]
        TicketRouter["Ticket Routes<br/><code>POST /tickets</code><br/><code>GET /tickets/{id}</code><br/><code>GET /tickets/search</code>"]
        Controller["Ticket Controller<br/>Pydantic Validation & Dispatch"]
    end

    subgraph DataStorage["💾 Persistence Layer (SQLite)"]
        SQLiteDB[("SQLite Database<br/><code>tickets.db</code>")]
        TenantsTable[("tenants table<br/><code>tenant_id</code>, <code>api_key</code>, <code>name</code>")]
        TicketsTable[("tickets table<br/>tickets, JSON embeddings,<br/>triage status & metadata")]
    end

    subgraph AsyncExecution["⚙️ Asynchronous Worker Engine"]
        InProcessWorker["Asyncio Background Worker<br/><code>TicketWorker (polling loop)</code>"]
        CeleryWorker["Celery Worker Engine<br/>Distributed Queue (Redis Broker)"]
        CrashRecovery["Stuck Ticket Recovery<br/>Reset <code>processing > 30s</code> ➔ <code>queued</code>"]
    end

    subgraph Pipeline["🧠 Core Triage Engine (TriageService)"]
        EmbedStep["Stage 1: Vector Embedding<br/>Generate 256-dim L2-normalized vector"]
        DuplicateStep["Stage 2: Duplicate Detection<br/>7-day sliding window & Cosine &ge; 0.9<br/>Transitive chain resolution"]
        LLMStep["Stage 3: LLM Triage & Repair Loop<br/>Timeout 5s &bull; Schema validation<br/>1x Repair retry &bull; Confidence check &ge; 0.7"]
    end

    subgraph ExternalAI["🤖 AI & Embedding Providers"]
        AIProviders["Google Gemini / Ollama / Local Models<br/>LLM Classification & Vector Embeddings"]
    end

    %% Connections
    Client -->|"HTTP POST / GET<br/>x-api-key, Idempotency-Key"| AuthMiddleware
    AuthMiddleware -->|"Verify Tenant Key"| TenantsTable
    AuthMiddleware -->|"Authorized Context"| IdempMiddleware
    IdempMiddleware --> TicketRouter
    TicketRouter --> Controller

    Controller -->|"Check Key & Insert (status: queued)"| TicketsTable
    Controller -->|"HTTP 202 Accepted {id, status}"| Client

    Controller -.->|"BackgroundTasks.add_task()"| InProcessWorker
    Controller -.->|"Celery .delay() (Optional)"| CeleryWorker

    InProcessWorker -->|"Poll queued tickets"| TicketsTable
    CeleryWorker -->|"Fetch queued tickets"| TicketsTable
    CrashRecovery -.->|"Auto-recover stuck tickets"| TicketsTable

    InProcessWorker -->|"Mark status: processing"| Pipeline
    CeleryWorker -->|"Mark status: processing"| Pipeline

    Pipeline --> EmbedStep
    EmbedStep <-->|"Generate Embeddings"| AIProviders
    EmbedStep --> DuplicateStep
    DuplicateStep <-->|"Query customer 7-day history"| TicketsTable
    DuplicateStep --> LLMStep
    LLMStep <-->|"Classify, Prioritize, Summarize"| AIProviders
    LLMStep -->|"Persist status, category, priority, embedding"| TicketsTable

    Client -->|"GET /tickets/search?q=query"| Controller
    Controller <-->|"Vector cosine ranking"| TicketsTable
```

---

### 3. End-to-End Ticket Lifecycle & Decision Flowchart

```mermaid
flowchart TD
    Start(["📥 Client Submits Ticket<br/>POST /tickets"]) --> Auth{"🔑 Valid x-api-key?"}

    Auth -- "No" --> ErrAuth["Return HTTP 401 Unauthorized<br/>{ detail: 'Missing or invalid API key' }"]
    Auth -- "Yes" --> ValPayload{"📋 Valid Payload?<br/>(Pydantic Schema)"}

    ValPayload -- "No" --> ErrPayload["Return HTTP 400 Bad Request<br/>Field-level error list (subject, body, email)"]
    ValPayload -- "Yes" --> CheckIdemp{"🔁 Idempotency Key<br/>Supplied & Cached?"}

    CheckIdemp -- "Yes (Replay)" --> ReturnExisting["Return Existing Ticket (HTTP 200/202)<br/>{ id, status }"]
    CheckIdemp -- "No (New)" --> InsertQueued["Insert Ticket into DB<br/>Status: 'queued'"]
    InsertQueued --> Resp202["Return HTTP 202 Accepted<br/>{ id: uuid, status: 'queued' }"]

    Resp202 --> WorkerPick["⚙️ Background Worker / Celery Picks Up Ticket<br/>Atomic DB Update: status ➔ 'processing'"]

    subgraph TriagePipeline["🧠 Resilient Triage Pipeline Execution"]
        WorkerPick --> GenEmbedding["Stage 1: Generate Vector Embedding<br/>(256-dimensional L2-normalized vector)"]
        GenEmbedding --> FetchHistory["Query customer tickets within<br/>7-day sliding window for tenant"]
        FetchHistory --> CalcCosine["Compute Cosine Similarity against candidates"]
        CalcCosine --> DupCheck{"Highest Similarity<br/>&ge; 0.90?"}

        DupCheck -- "Yes" --> ResolveChain["Resolve Transitive Duplicate Chain<br/>(e.g., C &rarr; B &rarr; A &rArr; canonical root A)<br/>duplicate_of = root_id, duplicate_score = sim"]
        DupCheck -- "No" --> NoDup["duplicate_of = null<br/>duplicate_score = null"]

        ResolveChain --> CallLLM1["Stage 3: LLM Triage (Attempt 1)<br/>Timeout limit: 5.0 seconds"]
        NoDup --> CallLLM1

        CallLLM1 --> CheckTimeout1{"Timeout or<br/>Network Failure?"}
        CheckTimeout1 -- "Timeout" --> MarkReviewTimeout["status = 'needs_review'<br/>triage_error = 'timeout'"]
        CheckTimeout1 -- "Completed" --> ValidateSchema1{"Valid Pydantic Schema?<br/>(category, priority, summary)"}

        ValidateSchema1 -- "Valid JSON" --> CheckConfidence{"Confidence Score<br/>&ge; 0.70?"}
        ValidateSchema1 -- "Invalid Schema" --> CallLLM2["Self-Healing Repair (Attempt 2)<br/>Inject corrective schema prompt"]

        CallLLM2 --> CheckTimeout2{"Timeout or<br/>Provider Error?"}
        CheckTimeout2 -- "Timeout" --> MarkReviewTimeout
        CheckTimeout2 -- "Still Invalid" --> MarkReviewSchema["status = 'needs_review'<br/>triage_error = 'schema_invalid_after_repair'"]
        CheckTimeout2 -- "Repaired Valid" --> CheckConfidence

        CheckConfidence -- "&ge; 0.70" --> MarkTriaged["status = 'triaged'<br/>Assign category, priority, summary"]
        CheckConfidence -- "< 0.70" --> MarkReviewConf["status = 'needs_review'<br/>triage_error = 'low_confidence'"]
    end

    MarkTriaged --> SaveDB["💾 Persist final updates to DB<br/>(status, category, priority, summary, confidence,<br/>duplicate_of, embedding vector JSON, llm_attempts)"]
    MarkReviewTimeout --> SaveDB
    MarkReviewSchema --> SaveDB
    MarkReviewConf --> SaveDB

    SaveDB --> End(["✅ Triage Complete & Available for Query / Search"])
```

---

### 4. End-to-End Sequence Diagram

```mermaid
sequenceDiagram
    autonumber
    actor Customer as 👤 Client / Agent
    participant API as ⚡ FastAPI App
    participant Auth as 🛡️ Auth Middleware
    participant DB as 💾 SQLite DB
    participant Worker as ⚙️ Background Worker
    participant Triage as 🧠 Triage Service
    participant Embed as 📐 Embedding Provider
    participant LLM as 🤖 LLM Provider

    %% 1. Ingestion Phase
    Note over Customer, DB: 1. Ingestion & Asynchronous Enqueueing
    Customer->>API: POST /tickets (x-api-key, Idempotency-Key, JSON payload)
    API->>Auth: Validate tenant API key (key_acme / key_beta)
    Auth-->>API: Tenant verified: 'acme'
    API->>DB: Check Idempotency-Key existence
    API->>DB: INSERT INTO tickets (status: 'queued', tenant_id: 'acme')
    API-->>Customer: HTTP 202 Accepted {"id": "uuid", "status": "queued"}

    %% 2. Background Processing
    Note over Worker, LLM: 2. Background Worker Pickup & Pipeline Execution
    Worker->>DB: Poll queued tickets / Atomic lock
    Worker->>DB: UPDATE tickets SET status = 'processing'
    Worker->>Triage: triage_ticket(ticket_id)

    %% 3. Embedding Generation
    Note over Triage, Embed: Stage 1: Vector Embedding
    Triage->>Embed: generate_ticket_embedding(subject, body)
    Embed-->>Triage: Return 256-dim L2-normalized float vector

    %% 4. Duplicate Detection
    Note over Triage, DB: Stage 2: 7-Day Window Duplicate Detection
    Triage->>DB: SELECT recent tickets for customer (last 7 days, tenant_id)
    DB-->>Triage: Candidate tickets with embeddings
    Triage->>Triage: Compute Cosine Similarity (threshold &ge; 0.90)
    opt Similarity &ge; 0.90
        Triage->>DB: Traverse duplicate_of chain to canonical root ticket
    end

    %% 5. LLM Triage & Self-Healing Loop
    Note over Triage, LLM: Stage 3: LLM Classification & Repair Loop
    Triage->>LLM: triage(subject, body, is_retry=False) [5s timeout]
    alt Normal Path: Valid JSON & Confidence &ge; 0.70
        LLM-->>Triage: Return {category, priority, summary, confidence}
        Triage->>Triage: Set status = 'triaged'
    else Malformed Output (Schema ValidationError)
        LLM-->>Triage: Invalid JSON / Schema mismatch
        Triage->>LLM: triage(subject, body, is_retry=True) [Repair Retry]
        alt Repaired Output Valid
            LLM-->>Triage: Return repaired JSON
            Triage->>Triage: Set status = 'triaged'
        else Still Invalid
            Triage->>Triage: Set status = 'needs_review', error = 'schema_invalid_after_repair'
        end
    else LLM Timeout (> 5.0s)
        Triage->>Triage: Catch TimeoutError &rarr; status = 'needs_review', error = 'timeout'
    else Low Confidence (< 0.70)
        Triage->>Triage: Set status = 'needs_review', error = 'low_confidence'
    end

    %% 6. Persistence
    Triage->>DB: UPDATE tickets SET status, category, priority, summary, embedding, duplicate_of...
    Worker-->>DB: Finish ticket processing

    %% 7. Retrieval & Semantic Search
    Note over Customer, Embed: 3. Retrieval & Semantic Vector Search
    Customer->>API: GET /tickets/{id} (x-api-key: key_acme)
    API->>DB: SELECT * FROM tickets WHERE id = ? AND tenant_id = 'acme'
    DB-->>API: Ticket record
    API-->>Customer: HTTP 200 OK (Cleaned response, raw vector omitted)

    Customer->>API: GET /tickets/search?q="payment failure"&limit=5
    API->>Embed: generate_query_embedding("payment failure")
    Embed-->>API: 256-dim query vector
    API->>DB: Fetch triaged tickets for tenant & compute cosine rank
    API-->>Customer: HTTP 200 OK (Ranked tickets sorted by similarity desc)
```

---

### 5. Detailed Stage-by-Stage Processing Breakdown

| Stage | Responsibility | Failure & Fallback Handling |
| :--- | :--- | :--- |
| **Ingestion** | Enforces `x-api-key` tenant isolation, Pydantic field validation, and optional idempotency deduplication. Immediately returns HTTP 202 (`queued`). | HTTP 401 on missing/invalid key; HTTP 400 with granular field errors; cached ticket on duplicate `Idempotency-Key`. |
| **Worker Engine** | Pulls `queued` tickets, transitions status to `processing`. Includes periodic recovery resetting tickets stuck in `processing` > 30s back to `queued`. | Prevents lost tasks during worker restarts, crashes, or unhandled exceptions. |
| **Stage 1: Embedding** | Converts ticket `subject` + `body` into a 256-dimensional L2-normalized vector using configured provider (Gemini, Ollama, or Mock). | Fallback embedding providers configurable via `.env`. |
| **Stage 2: Duplicate Detection** | Scans tickets for the same `(tenant_id, customer_email)` within a 7-day sliding window. Calculates cosine similarity; if $\ge 0.90$, recursively resolves parent links to the canonical root ticket. | If no match $\ge 0.90$, ticket remains standalone (`duplicate_of: null`). Handles circular links with depth bounds. |
| **Stage 3: LLM Triage** | Invokes LLM with strict JSON schema instructions to classify `category`, assign `priority`, draft `summary`, and output `confidence`. | <ul><li>**Timeout**: 5s limit triggers fallback to `needs_review` (`timeout`).</li><li>**Invalid Schema**: 1x automatic repair retry with corrective prompt before fallback to `needs_review` (`schema_invalid_after_repair`).</li><li>**Low Confidence**: Confidence $< 0.70$ routes to `needs_review` (`low_confidence`).</li></ul> |
| **Stage 4: Persistence** | Atomically updates ticket record with triage results, raw embedding vector JSON, duplicate resolution, and attempt count. | SQLite row update within isolated tenant scope. |
| **Semantic Search** | Embeds incoming query `q`, computes cosine similarity against all triaged tenant tickets, and returns top-$K$ matches. | Input validation (2–200 chars, limit 1–20); tenant boundary strictly preserved. |

---

## 🏗 Directory & Architecture Overview

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

## 🏗 Key Assumptions & Architectural Trade-offs at 1M Ticket Scale

### 1. Multi-Tenancy & Data Isolation
- **Current Approach**: Row-level tenant partitioning (`tenant_id` column with composite index on `(tenant_id, customer_email, created_at)`).
- **Scale Trade-off (1M+ Tickets)**:
  - Row-level multitenancy in a single SQLite file reaches locking limits (SQLite single-writer bottleneck).
  - *Recommendation at 1M Scale*: Migrate to **PostgreSQL with Schema-per-Tenant** or **Database-per-Tenant** for enterprise tiers, with Row-Level Security (RLS) enabled.

### 2. Semantic Search & Vector Embeddings
- **Current Approach**: 256-dimensional embeddings stored as JSON strings in SQLite, scored with Python in-memory brute-force cosine similarity.
- **Scale Trade-off (1M+ Tickets)**:
  - Scanning $10^6$ rows in Python consumes significant memory ($O(N)$ comparisons) and degrades response times ($>500\text{ms}$).
  - *Recommendation at 1M Scale*: Adopt a dedicated vector index such as **pgvector (HNSW / IVFFlat)**, **Qdrant**, or **Pinecone**. Approximate Nearest Neighbor (ANN) index reduces search latency to $<10\text{ms}$ with logarithmic $O(\log N)$ scaling.

### 3. Queueing & Background Workers
- **Current Approach**: Dual engine support—in-process `BackgroundTasks` alongside Celery with Redis broker and periodic stuck-processing recovery.
- **Scale Trade-off (1M+ Tickets)**:
  - In-process tasks are tied to the web worker process lifecycle and risk task loss during process restarts or crashes.
  - *Recommendation at 1M Scale*: Decouple fully with distributed message brokers like **Redis Streams**, **RabbitMQ**, or **Apache Kafka** with dead-letter queues (DLQ), consumer autoscaling (KEDA), and rate-limiting per tenant.

### 4. LLM Resilience & Rate Limiting
- **Current Approach**: Single-shot repair retry loop with 5-second timeout and fallback to `needs_review`.
- **Scale Trade-off (1M+ Tickets)**:
  - At high volume, external LLM providers enforce tight RPM/TPM rate limits and experience transient network latency.
  - *Recommendation at 1M Scale*: Implement an exponential backoff circuit breaker (e.g. `tenacity`), token-bucket rate limiter per tenant, and fallback to quantized local open-source models (e.g. Mistral/Llama via vLLM) for low-complexity tier classification.

### 5. Duplicate Detection & Windowing
- **Current Approach**: 7-day sliding window query on `(tenant_id, customer_email, created_at)`.
- **Scale Trade-off (1M+ Tickets)**:
  - High-frequency support customers generate large candidate lists over 7 days.
  - *Recommendation at 1M Scale*: Pre-filter candidate tickets by customer ID with a partition index and prune closed/resolved tickets older than 48 hours to minimize vector distance calculations.
