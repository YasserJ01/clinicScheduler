# Software Requirements Specification
## Medical Clinic Appointment Scheduler

| Field | Value |
|---|---|
| Document Version | 1.0.0 |
| Status | Draft — For Review |
| Prepared By | Engineering Team |
| Date | 2025 |
| Classification | Internal / Technical |

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [System Overview](#2-system-overview)
3. [Stakeholders and User Roles](#3-stakeholders-and-user-roles)
4. [Functional Requirements](#4-functional-requirements)
5. [Non-Functional Requirements](#5-non-functional-requirements)
6. [System Architecture](#6-system-architecture)
7. [Data Management and Storage](#7-data-management-and-storage)
8. [API Specification](#8-api-specification)
9. [Security Design](#9-security-design)
10. [Design Patterns and Principles](#10-design-patterns-and-principles)
11. [Assumptions and Dependencies](#11-assumptions-and-dependencies)
12. [Constraints](#12-constraints)
13. [Glossary](#13-glossary)

---

## 1. Introduction

### 1.1 Purpose

This Software Requirements Specification (SRS) defines the complete functional and non-functional requirements for the **Medical Clinic Appointment Scheduler**, a distributed, containerised booking engine. It serves as the authoritative reference for developers, QA engineers, architects, and project managers throughout the full development lifecycle, from initial implementation through future iterations and maintenance.

### 1.2 Scope

The system enables patients to reserve specific calendar time slots with particular doctors at a medical clinic. It exposes a REST API consumed by any client (web, mobile, or third-party integration). The backend is built on FastAPI, persists data in PostgreSQL, uses Redis for auxiliary caching and session state, and is horizontally scaled across three worker nodes behind an NGINX reverse proxy.

The scope covers:

- Patient and doctor management
- Authentication and authorisation
- Conflict-free appointment booking with double-booking prevention
- Fault simulation ("chaos" mode) for resilience testing
- Health monitoring, observability, and load testing

Out of scope for the current version: payment processing, video/telemedicine features, insurance billing, and patient medical-records management.

### 1.3 Definitions and Acronyms

See [Section 13 — Glossary](#13-glossary) for a complete list of terms.

### 1.4 References

- `AGENTS.md` — internal developer quick-start and architecture guide
- FastAPI 0.115 documentation
- SQLAlchemy 2.0 async documentation
- PostgreSQL 16 documentation
- NGINX 1.25 documentation
- OWASP API Security Top 10 (2023)
- GDPR (General Data Protection Regulation) — Regulation (EU) 2016/679

### 1.5 Document Conventions

- **SHALL** — mandatory requirement
- **SHOULD** — recommended, may be deferred
- **MAY** — optional
- `FR-N` — Functional Requirement identifier
- `NFR-N` — Non-Functional Requirement identifier

---

## 2. System Overview

### 2.1 Product Description

The Clinic Scheduler is a stateless, horizontally scalable REST API service deployable via Docker Compose. It provides a booking interface through which authenticated users can discover available doctors, query existing appointments, and reserve future time slots. The system enforces single-occupancy constraints per doctor per time slot across all concurrent workers using the shared PostgreSQL database as the serialisation point.

### 2.2 Product Context

```
┌────────────────────────────────────────────────────────────────┐
│                         Client Layer                          │
│          (Web Browser / Mobile App / Third-Party API)         │
└───────────────────────────┬────────────────────────────────────┘
                            │ HTTP/HTTPS :80
┌───────────────────────────▼────────────────────────────────────┐
│                     NGINX Reverse Proxy                        │
│        Consistent-hashing LB · Rate limiting 500 r/s          │
│        Retry on 502/503 · TLS termination (future)            │
└────────┬───────────────────┬───────────────────┬───────────────┘
         │                   │                   │
  ┌──────▼──────┐    ┌───────▼─────┐    ┌────────▼─────┐
  │  Worker 1   │    │  Worker 2   │    │  Worker 3    │
  │  FastAPI    │    │  FastAPI    │    │  FastAPI     │
  │  Uvicorn    │    │  Uvicorn    │    │  Uvicorn     │
  └──────┬──────┘    └───────┬─────┘    └────────┬─────┘
         └──────────────┬────┴────────────────────┘
                        │
           ┌────────────┴────────────┐
           │                         │
    ┌──────▼──────┐           ┌──────▼──────┐
    │ PostgreSQL  │           │    Redis     │
    │    :5432    │           │    :6379     │
    └─────────────┘           └─────────────┘
```

### 2.3 Operating Environment

| Component | Technology | Version |
|---|---|---|
| Language | Python | 3.12 |
| Web Framework | FastAPI | 0.115.6 |
| ASGI Server | Uvicorn | 0.34.0 |
| ORM | SQLAlchemy (async) | 2.0.36 |
| Primary DB | PostgreSQL | 16 (Alpine) |
| Cache / Pub-Sub | Redis | 7 (Alpine) |
| Reverse Proxy / LB | NGINX | 1.25 (Alpine) |
| Container Runtime | Docker + Compose | 3.9 |
| Auth | JWT (HS256) via python-jose | 3.3.0 |
| Password Hashing | passlib + bcrypt | 1.7.4 / 4.0.1 |
| Serialisation | JSON (default) + MessagePack | — |

---

## 3. Stakeholders and User Roles

### 3.1 Stakeholder Summary

| Stakeholder | Interest |
|---|---|
| Clinic administrators | Manage doctors, view all bookings, control system access |
| Doctors | View their own schedule and patient list |
| Patients | Book, view, and cancel their own appointments |
| DevOps / Platform team | Deploy, monitor, and scale the service |
| QA engineers | Validate correctness and performance under load |
| Compliance officer | Ensure data privacy and regulatory adherence |

### 3.2 User Roles and Permissions

| Role | Register | Login | View Doctors | View All Appointments | Book Appointment | Cancel Appointment | Create Doctor |
|---|---|---|---|---|---|---|---|
| `admin` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `doctor` | ✓ | ✓ | ✓ | Own schedule only | — | — | — |
| `patient` | ✓ | ✓ | ✓ | Own appointments only | ✓ | Own only | — |
| Unauthenticated | ✓ | ✓ | — | — | — | — | — |

Role is embedded in the JWT payload at registration time and is not modifiable by the user.

---

## 4. Functional Requirements

### 4.1 Authentication and Authorisation (AUTH)

**FR-AUTH-1 — User Registration**
The system SHALL allow any visitor to register with a unique username, a plaintext password (hashed server-side with bcrypt), and an optional role (`patient` by default). On success, the system SHALL return an `access_token` (JWT) and a `token_type`.

**FR-AUTH-2 — User Login**
The system SHALL authenticate registered users via username and password. On success, the system SHALL return a fresh JWT. On failure, the system SHALL return HTTP 401 with a human-readable error message.

**FR-AUTH-3 — Token Expiry**
JWTs SHALL expire after a configurable interval (default: 30 minutes). Expired tokens SHALL be rejected with HTTP 401.

**FR-AUTH-4 — Role-Based Access Control**
Every protected endpoint SHALL validate the `Authorization: Bearer <token>` header. The decoded JWT payload SHALL contain the `sub` (username) and `role` claims. Endpoints that require elevated roles SHALL return HTTP 403 if the caller's role is insufficient.

**FR-AUTH-5 — Password Storage**
Passwords SHALL never be stored in plaintext. The system SHALL use bcrypt (cost factor ≥ 12) via passlib. The `bcrypt` package SHALL be pinned to 4.0.1 for compatibility with passlib 1.7.4.

### 4.2 Doctor Management (DOC)

**FR-DOC-1 — List Active Doctors**
Any authenticated user SHALL be able to retrieve a list of all active doctors (where `is_active = 'true'`), including each doctor's `id`, `name`, and `specialty`.

**FR-DOC-2 — Create Doctor**
Only users with the `admin` role SHALL be able to create a new doctor record. The creation request SHALL include `name` and `specialty`. A successful creation SHALL return HTTP 201 and the created doctor object.

**FR-DOC-3 — Doctor Activation / Deactivation**
The system SHOULD support toggling a doctor's `is_active` flag via an admin-only endpoint. Inactive doctors SHALL be excluded from the `GET /doctors` listing and SHALL NOT be bookable.

**FR-DOC-4 — Doctor Profile**
The system SHOULD expose a `GET /doctors/{id}` endpoint returning full doctor details including schedule, for use by admin and doctor roles.

### 4.3 Patient Management (PAT)

**FR-PAT-1 — List Patients**
Any authenticated user SHALL be able to retrieve a list of all registered patients.

**FR-PAT-2 — Patient Self-Profile**
Any authenticated user SHALL be able to call `GET /patients/me` to retrieve a minimal self-profile derived from the JWT.

**FR-PAT-3 — Patient Registration via Booking**
If a patient ID provided during booking does not exist in the `patients` table, the system SHALL return HTTP 404 rather than silently creating a ghost record. Patient records SHALL be created explicitly (either at user registration or via an admin-provisioned endpoint).

**FR-PAT-4 — Patient CRUD (Admin)**
The system SHOULD expose admin-only endpoints for creating, updating, and deactivating patient records, including name, email, and phone number.

### 4.4 Appointment Booking (APT)

**FR-APT-1 — Book Appointment**
Any authenticated user SHALL be able to POST to `/api/v1/appointments` with a `doctor_id` (integer), `patient_id` (integer or string), and `time_slot` (ISO 8601 string). On success, the system SHALL return HTTP 201 and the booking response object.

The booking response object SHALL contain:
- `success` (boolean)
- `node_id` (the container hostname, for distributed tracing)
- `error` (null on success, human-readable string on failure)
- `appointment` (the created appointment object, or the conflicting appointment on HTTP 409)

**FR-APT-2 — Conflict Detection**
Before inserting a new appointment, the system SHALL query the database for an existing non-cancelled appointment for the same `doctor_id` and `appointment_time`. If a conflict exists, the system SHALL return HTTP 409 with `success: false` and an error message identifying the patient who holds the slot (e.g., `"Slot already occupied by patient Jane Doe"`).

**FR-APT-3 — Doctor Validation**
The system SHALL validate that the provided `doctor_id` refers to an existing, active doctor record before inserting. If the doctor does not exist, the system SHALL return HTTP 400 with `success: false` and `error: "Doctor not found"`.

**FR-APT-4 — Patient Validation**
The system SHALL validate that the provided `patient_id` refers to an existing patient record. If the patient does not exist, the system SHALL return HTTP 404 with `success: false` and an appropriate error message.

**FR-APT-5 — Time Slot Validation**
The system SHALL validate that `time_slot` is a syntactically valid ISO 8601 datetime string. Invalid strings SHALL be rejected at the Pydantic layer before any database interaction, returning HTTP 422.

**FR-APT-6 — Timezone Normalisation**
The system SHALL strip timezone information from parsed datetimes before persisting them to PostgreSQL's `TIMESTAMP WITHOUT TIME ZONE` columns. All stored times are interpreted as UTC.

**FR-APT-7 — List Appointments**
Any authenticated user SHALL be able to retrieve all appointments ordered by `appointment_time` ascending. Each item SHALL include `id`, `doctor_id`, `patient_id`, `patient_name`, `time_slot`, and `status`.

**FR-APT-8 — Get Appointment by ID**
Any authenticated user SHALL be able to retrieve a single appointment by its `id`. A non-existent ID SHALL return HTTP 404.

**FR-APT-9 — Appointment Status Lifecycle**
Appointments SHALL transition through the following states: `scheduled → confirmed → completed → cancelled`. Only `admin` or `doctor` roles SHALL be permitted to advance or revert status beyond `scheduled`.

**FR-APT-10 — Appointment Cancellation**
A `patient` role user SHALL be able to cancel their own appointments (set status to `cancelled`). An `admin` role user SHALL be able to cancel any appointment. Cancelled appointments SHALL be excluded from conflict checks (`FR-APT-2`).

### 4.5 Chaos Engineering Backdoor (CHAOS)

**FR-CHAOS-1 — Poison Pill Trigger**
When `patient_id` equals `999` (integer or the string `"999"`), the system SHALL immediately return HTTP 503 with `{"detail": "CHAOS: Simulated node failure"}` without performing any database operations. This facilitates automated resilience and retry testing.

**FR-CHAOS-2 — Logging of Chaos Events**
All chaos events SHALL be logged at `ERROR` level with the triggering `patient_id` and the responding `node_id`.

### 4.6 Health Monitoring (HEALTH)

**FR-HEALTH-1 — Health Check Endpoint**
The system SHALL expose `GET /api/v1/health` without authentication. The response SHALL include the aggregate status (`ok` or `degraded`), the database connectivity status (`healthy` or `unhealthy`), and the Redis connectivity status (`healthy` or `unhealthy`).

**FR-HEALTH-2 — HTTP Status Code on Degradation**
The health endpoint SHALL return HTTP 200 when all dependencies are healthy and HTTP 503 when the database is unhealthy. Redis degradation alone SHALL return HTTP 200 but include `redis: "unhealthy"` in the payload.

**FR-HEALTH-3 — Container Health Check**
Each worker container's Dockerfile HEALTHCHECK SHALL poll `GET /api/v1/health` every 10 seconds, with a 3-second timeout and 3 retries before marking the container as unhealthy.

### 4.7 Serialisation and Middleware (SERIAL)

**FR-SERIAL-1 — JSON Default**
All API responses SHALL default to `application/json` unless the client specifies otherwise.

**FR-SERIAL-2 — MessagePack Support**
If the client sends `Accept: application/x-msgpack`, the response body SHALL be re-serialised as MessagePack and the `Content-Type` response header set to `application/x-msgpack`.

**FR-SERIAL-3 — Response Time Header**
Every response SHALL include an `X-Response-Time` header reporting the elapsed time in milliseconds (e.g., `X-Response-Time: 12.43ms`).

---

## 5. Non-Functional Requirements

### 5.1 Performance (NFR-PERF)

**NFR-PERF-1 — Throughput**
The system SHALL sustain at least 200 concurrent virtual users with a p95 request latency below 500 ms under steady-state load, as validated by the k6 load test (`loadtest/scheduler.js`).

**NFR-PERF-2 — Error Rate Under Load**
The HTTP error rate (non-2xx and non-4xx client errors) SHALL remain below 5% during load tests. The application-level error rate SHALL remain below 10%.

**NFR-PERF-3 — Database Connection Pooling**
Each worker SHALL maintain an async SQLAlchemy connection pool with `pool_size=20`, `max_overflow=10`, and `pool_timeout=10s`. Connection recycle SHALL occur every 1,800 seconds to prevent stale connections.

**NFR-PERF-4 — NGINX Rate Limiting**
NGINX SHALL enforce a rate limit of 500 requests/second per client IP for the `/api/` location block, with a burst allowance of 50 and `nodelay` to prevent queuing at the proxy layer. The rate SHOULD be reduced to approximately 30 r/s for production deployments.

**NFR-PERF-5 — Proxy Timeouts**
NGINX SHALL enforce a connect timeout of 3 seconds, a send timeout of 5 seconds, and a read timeout of 10 seconds. Requests failing with HTTP 502 or 503 SHALL be retried on the next available upstream node (max 2 attempts).

### 5.2 Reliability and Availability (NFR-REL)

**NFR-REL-1 — Target Uptime**
The service SHALL target 99.9% monthly uptime (approximately 43 minutes of downtime/month) when deployed in production with health-check-driven restart policies.

**NFR-REL-2 — Graceful Restart**
Workers SHALL be configured with `restart: unless-stopped` and Docker health-check restart policies. NGINX SHALL only route to workers that have passed health checks.

**NFR-REL-3 — Circuit Breaker**
The system SHALL implement a circuit breaker for database interactions (threshold: 5 consecutive failures, recovery: 15 seconds) and for Redis interactions (threshold: 3, recovery: 10 seconds). When a circuit is open, affected requests SHALL receive HTTP 503 immediately rather than blocking.

**NFR-REL-4 — Idempotent Startup**
The `seed_data()` function SHALL be idempotent: it SHALL only insert seed records when the `doctors` table is empty, preventing duplicate inserts on worker restarts.

**NFR-REL-5 — Atomic Conflict Prevention**
Conflict detection and appointment creation SHALL occur within a single database session that is committed atomically. In high-concurrency scenarios, the system SHOULD use a database-level advisory lock or `SELECT … FOR UPDATE` to prevent race conditions between concurrent bookings.

### 5.3 Security (NFR-SEC)

**NFR-SEC-1 — Authentication Required**
All endpoints except `/api/v1/auth/register`, `/api/v1/auth/login`, and `/api/v1/health` SHALL require a valid JWT.

**NFR-SEC-2 — Secret Key Management**
The `SECRET_KEY` SHALL be injected via environment variable. It SHALL NOT be hard-coded in source code. The default value `change-me-in-production` SHALL be replaced before any production deployment.

**NFR-SEC-3 — Token Signing Algorithm**
JWTs SHALL be signed using HS256. The system SHALL reject tokens signed with `alg: none` or any algorithm not in the configured allowlist.

**NFR-SEC-4 — Password Policy**
Passwords SHALL be hashed with bcrypt at a cost factor of at least 12. The system SHALL NOT accept passwords longer than 72 bytes (bcrypt limitation) and SHALL return a validation error, not a 500.

**NFR-SEC-5 — Input Validation**
All request bodies SHALL be validated by Pydantic v2 before reaching business logic. Invalid payloads SHALL return HTTP 422 with structured error details.

**NFR-SEC-6 — SQL Injection Prevention**
All database queries SHALL use SQLAlchemy's parameterised ORM query methods. Raw SQL execution SHALL be limited to schema migration scripts using the `text()` construct with no user-supplied interpolation.

**NFR-SEC-7 — CORS Policy**
For internal deployments, the CORS middleware MAY allow all origins (`*`). For production deployments, the allowlist SHALL be restricted to the known front-end origin(s).

**NFR-SEC-8 — TLS**
All traffic in production SHALL be encrypted in transit via TLS 1.2 or higher, terminated at NGINX. Plaintext HTTP SHALL redirect to HTTPS.

**NFR-SEC-9 — Secrets Not in Logs**
The logging configuration SHALL never output JWT tokens, passwords, or database connection strings.

**NFR-SEC-10 — Principle of Least Privilege**
The application process inside the container SHALL run as the non-root `appuser` account. The Docker image SHALL not grant elevated filesystem permissions beyond `/app`.

### 5.4 Scalability (NFR-SCALE)

**NFR-SCALE-1 — Horizontal Scaling**
The number of worker replicas SHALL be configurable via Docker Compose `deploy.replicas` without code changes. NGINX consistent hashing SHALL distribute traffic across all healthy replicas.

**NFR-SCALE-2 — Stateless Workers**
Workers SHALL hold no in-memory application state. All shared state (appointments, users, patients, doctors) SHALL be persisted in PostgreSQL. Workers MAY cache read-heavy data in Redis but SHALL NOT rely on Redis for write correctness.

**NFR-SCALE-3 — Database Scalability**
The PostgreSQL schema SHALL include indexes on `appointment_time`, `doctor_id`, and `patient_id` to ensure query plans remain efficient as the appointments table grows beyond millions of rows.

**NFR-SCALE-4 — Redis Eviction Policy**
Redis SHALL be configured with `allkeys-lru` eviction and a maximum memory limit of 128 MB to prevent unbounded memory growth.

### 5.5 Maintainability (NFR-MAINT)

**NFR-MAINT-1 — Code Organisation**
Business logic SHALL be separated from HTTP handling. Routers SHALL delegate to repository classes; repository classes SHALL not contain HTTP-layer concerns.

**NFR-MAINT-2 — Dependency Management**
All dependencies SHALL be pinned to exact versions in `requirements.txt`. Major version upgrades SHALL be gated behind a test suite run.

**NFR-MAINT-3 — Structured Logging**
All log messages SHALL be emitted via the standard `logging` module at appropriate levels (`DEBUG`, `INFO`, `WARNING`, `ERROR`). Log format SHALL include timestamp, logger name, level, and message.

**NFR-MAINT-4 — Exception Handling**
`SQLAlchemyError` and `CircuitBreakerError` exceptions SHALL be caught by global FastAPI exception handlers and translated into structured JSON responses. Unhandled exceptions SHALL return HTTP 500 and log the full traceback.

**NFR-MAINT-5 — OpenAPI Documentation**
The system SHALL auto-generate OpenAPI 3.x documentation at `/docs` (Swagger UI) and `/redoc` (ReDoc). All request/response models SHALL be documented via Pydantic schemas.

**NFR-MAINT-6 — Database Migrations**
Schema changes SHALL be managed via Alembic migration scripts. No schema change SHALL be applied by directly modifying `Base.metadata.create_all` in production.

### 5.6 Usability (NFR-USE)

**NFR-USE-1 — API Discoverability**
The Swagger UI at `/docs` SHALL provide interactive try-it-out capability for all endpoints, enabling frontend developers and QA engineers to explore the API without additional tooling.

**NFR-USE-2 — Consistent Error Format**
All error responses SHALL follow a consistent JSON shape. Booking errors SHALL always return the `BookingResponse` schema (`success`, `node_id`, `error`, `appointment`). Infrastructure errors SHALL return `{"error": "<category>", "detail": "<message>"}`.

**NFR-USE-3 — Human-Readable Conflict Messages**
Conflict responses (HTTP 409) SHALL name the existing patient who holds the slot, enabling the client application to surface a useful message to the end user.

### 5.7 Compliance and Privacy (NFR-PRIV)

**NFR-PRIV-1 — Data Minimisation**
The system SHALL only collect patient data necessary for appointment scheduling: name, email, and optional phone number. Medical history and clinical notes SHALL NOT be stored in this service.

**NFR-PRIV-2 — Data Retention**
Completed and cancelled appointment records SHALL be retained for a minimum of 7 years to satisfy medical record-keeping obligations, or per the applicable jurisdiction's regulations.

**NFR-PRIV-3 — Right to Access and Deletion**
The system SHOULD provide admin-accessible endpoints to export or delete a specific patient's personal data in compliance with GDPR Article 17 (Right to Erasure).

**NFR-PRIV-4 — Audit Trail**
All appointment create, update, and cancel operations SHALL be logged with the acting user's identity, timestamp, and outcome for audit purposes.

### 5.8 Observability (NFR-OBS)

**NFR-OBS-1 — Request Tracing**
Each booking response SHALL include `node_id` (container hostname) to enable distributed tracing across replicas.

**NFR-OBS-2 — Metrics (Future)**
The system SHOULD expose a `/api/v1/metrics` endpoint compatible with Prometheus scraping, reporting request counts, latency histograms, and circuit breaker state.

**NFR-OBS-3 — Centralised Logging (Future)**
In production, container stdout logs SHOULD be collected by a log aggregator (e.g., Loki, CloudWatch, or Elasticsearch) and made queryable by node, endpoint, and error level.

---

## 6. System Architecture

### 6.1 Deployment Topology

The system is packaged as a Docker Compose multi-service application consisting of:

- **nginx** — ingress, TLS termination (future), consistent-hash load balancing, rate limiting
- **worker** (×3 replicas) — stateless FastAPI application processes
- **db** — single PostgreSQL 16 instance (HA failover is a future concern)
- **redis** — single Redis 7 instance for caching and future pub-sub notifications

All services communicate over the `clinic-net` Docker bridge network using Docker DNS service names (`db`, `redis`, `worker`).

### 6.2 Request Lifecycle

```
Client → NGINX (/api/) → consistent-hash → Worker N
  → FastAPI middleware (MessagePack, timing header)
  → JWT validation (dependencies.py)
  → Router handler
  → Repository (SQLAlchemy async ORM)
  → PostgreSQL (atomic read-write)
  ← Pydantic response model serialisation
  ← Middleware (X-Response-Time header)
  ← NGINX (retry on 502/503)
← Client
```

### 6.3 Layer Responsibilities

| Layer | Component | Responsibility |
|---|---|---|
| Infrastructure | Docker, NGINX | Process isolation, routing, rate limiting |
| Transport | Uvicorn + FastAPI | HTTP lifecycle, middleware, OpenAPI |
| API | Routers | Request validation, response shaping, HTTP status codes |
| Business Logic | Routers + Repository | Conflict resolution, role checks, booking rules |
| Data Access | Repository classes | ORM queries, session management |
| Persistence | PostgreSQL | Durable storage, ACID guarantees |
| Cache | Redis | Optional caching, future pub-sub |
| Security | JWT, bcrypt, CORS | AuthN/AuthZ, encryption |

### 6.4 NGINX Load Balancing Strategy

NGINX uses `hash $request_uri consistent` (consistent hashing). This means requests for the same URI are sticky to the same upstream, improving cache locality. Because appointment booking uses `POST /api/v1/appointments` for all booking requests, the hash key distributes based on the full URI, which is identical for all booking requests — effectively load-balancing bookings across all three workers evenly.

### 6.5 Circuit Breaker States

```
CLOSED ──(failures ≥ threshold)──► OPEN ──(recovery_timeout elapsed)──► HALF_OPEN
  ▲                                                                           │
  └──────────────────(success)────────────────────────────────────────────────┘
                    (failure in HALF_OPEN returns to OPEN)
```

---

## 7. Data Management and Storage

### 7.1 Entity-Relationship Overview

```
users ──────── (no FK, username in JWT)
doctors ◄──── appointments ────► patients
```

### 7.2 Table Definitions

#### `users`

| Column | Type | Constraints |
|---|---|---|
| `id` | INTEGER | PK, auto-increment |
| `username` | VARCHAR(100) | UNIQUE, NOT NULL, INDEX |
| `hashed_password` | VARCHAR(255) | NOT NULL |
| `role` | ENUM(userrole) | NOT NULL, DEFAULT 'patient' |
| `created_at` | TIMESTAMP | NOT NULL, DEFAULT now() |

#### `doctors`

| Column | Type | Constraints |
|---|---|---|
| `id` | INTEGER | PK, auto-increment |
| `name` | VARCHAR(200) | NOT NULL |
| `specialty` | VARCHAR(100) | NOT NULL |
| `is_active` | VARCHAR(10) | NOT NULL, DEFAULT 'true' |
| `created_at` | TIMESTAMP | NOT NULL, DEFAULT now() |

#### `patients`

| Column | Type | Constraints |
|---|---|---|
| `id` | INTEGER | PK, auto-increment |
| `name` | VARCHAR(200) | NOT NULL |
| `email` | VARCHAR(255) | UNIQUE, NOT NULL, INDEX |
| `phone` | VARCHAR(20) | NULLABLE |
| `created_at` | TIMESTAMP | NOT NULL, DEFAULT now() |

#### `appointments`

| Column | Type | Constraints |
|---|---|---|
| `id` | INTEGER | PK, auto-increment |
| `doctor_id` | INTEGER | FK → doctors.id, NOT NULL, INDEX |
| `patient_id` | INTEGER | FK → patients.id, NOT NULL, INDEX |
| `appointment_time` | TIMESTAMP WITHOUT TZ | NOT NULL, INDEX |
| `status` | ENUM(appointmentstatus) | NOT NULL, DEFAULT 'scheduled' |
| `notes` | TEXT | NULLABLE |
| `created_at` | TIMESTAMP | NOT NULL, DEFAULT now() |

**Composite unique constraint** (recommended): `UNIQUE(doctor_id, appointment_time)` WHERE `status != 'cancelled'` — enforced as a partial unique index to complement the application-level conflict check.

### 7.3 ENUM Types

The database defines two ENUM types managed via idempotent `DO $$ BEGIN … EXCEPTION WHEN duplicate_object` blocks in `init_db()`:

- `userrole`: `patient`, `doctor`, `admin`
- `appointmentstatus`: `scheduled`, `confirmed`, `completed`, `cancelled`

SQLAlchemy model definitions MUST use `values_callable=lambda x: [e.value for e in x]` to ensure the lowercase string value is sent to PostgreSQL rather than the Python enum member name.

### 7.4 Timezone Handling

All datetimes are stored as `TIMESTAMP WITHOUT TIME ZONE` (equivalent to UTC). Pydantic parses ISO 8601 strings with `Z` or `+HH:MM` offsets as timezone-aware `datetime` objects. The `_parse_time_slot()` helper and repository methods MUST call `.replace(tzinfo=None)` before any ORM comparison or insert to prevent asyncpg type mismatch errors.

### 7.5 Schema Migration

Schema evolution SHALL use Alembic with autogenerate enabled. Each migration script SHALL be reviewed before application to production. The `init_db()` function using `create_all` is suitable for development but SHALL be replaced by Alembic `upgrade head` in production CI/CD pipelines.

---

## 8. API Specification

### 8.1 Base URL

All API endpoints are prefixed with `/api/v1`.

### 8.2 Authentication

All protected endpoints require:
```
Authorization: Bearer <jwt_access_token>
```

### 8.3 Endpoints Summary

| Method | Path | Auth | Role | Description |
|---|---|---|---|---|
| POST | `/auth/register` | None | — | Register new user |
| POST | `/auth/login` | None | — | Obtain JWT |
| GET | `/doctors` | Required | Any | List active doctors |
| POST | `/doctors` | Required | admin | Create doctor |
| GET | `/patients` | Required | Any | List patients |
| GET | `/patients/me` | Required | Any | Current user profile |
| GET | `/appointments` | Required | Any | List all appointments |
| POST | `/appointments` | Required | Any | Book appointment |
| GET | `/appointments/{id}` | Required | Any | Get appointment by ID |
| GET | `/health` | None | — | Health check |

### 8.4 Key Request / Response Schemas

#### POST `/auth/register` — Request
```json
{
  "username": "jane_doe",
  "password": "securepass123",
  "role": "patient"
}
```

#### POST `/auth/register` — Response 200
```json
{
  "access_token": "<jwt>",
  "token_type": "bearer"
}
```

#### POST `/appointments` — Request
```json
{
  "doctor_id": 1,
  "patient_id": 42,
  "time_slot": "2025-06-15T09:00:00Z"
}
```

#### POST `/appointments` — Response 201 (success)
```json
{
  "success": true,
  "node_id": "3f4a2b1c8e92",
  "error": null,
  "appointment": {
    "id": 101,
    "doctor_id": 1,
    "patient_id": 42,
    "patient_name": "Jane Doe",
    "time_slot": "2025-06-15T09:00:00",
    "status": "scheduled"
  }
}
```

#### POST `/appointments` — Response 409 (conflict)
```json
{
  "success": false,
  "node_id": "3f4a2b1c8e92",
  "error": "Slot already occupied by patient John Smith",
  "appointment": {
    "id": 87,
    "doctor_id": 1,
    "patient_id": 7,
    "patient_name": "John Smith",
    "time_slot": "2025-06-15T09:00:00",
    "status": "scheduled"
  }
}
```

#### GET `/health` — Response 200
```json
{
  "status": "ok",
  "database": "healthy",
  "redis": "healthy"
}
```

### 8.5 Standard Error Response Format

```json
{
  "error": "<category>",
  "detail": "<human-readable message>"
}
```

HTTP 422 (Pydantic validation failure) uses FastAPI's default schema:
```json
{
  "detail": [
    {
      "loc": ["body", "time_slot"],
      "msg": "time_slot must be a valid ISO 8601 datetime string",
      "type": "value_error"
    }
  ]
}
```

---

## 9. Security Design

### 9.1 Threat Model Summary

| Threat | Mitigation |
|---|---|
| Credential stuffing / brute force | Rate limiting at NGINX (500 r/s per IP) |
| JWT forgery | HS256 with secret-key injection via env var |
| SQL injection | Parameterised ORM queries only |
| Privilege escalation | Role claim validated server-side on every request |
| Denial of service | NGINX rate limiting; circuit breakers |
| Container escape | Non-root `appuser`; read-only mounts |
| Secret leakage in logs | Logging policy prohibits token/password output |
| Data exfiltration | CORS restricted in production |

### 9.2 JWT Lifecycle

1. User registers or logs in → server issues JWT (HS256, `exp = now + 30 min`, `sub = username`, `role = <role>`)
2. Client stores token (recommended: in-memory or HttpOnly cookie — NOT localStorage)
3. Client sends `Authorization: Bearer <token>` with every protected request
4. Server decodes and validates signature, checks `exp`, extracts `sub` and `role`
5. Token expiry → client calls `/auth/login` again

### 9.3 Recommended Production Hardening

- Rotate `SECRET_KEY` on a schedule; implement token revocation via a Redis deny-list for high-security deployments
- Enable TLS at NGINX with HSTS headers
- Restrict CORS to the specific front-end origin
- Reduce NGINX rate limit to ~30 r/s per IP for production
- Add IP allow-listing for admin endpoints if the admin UI is internal-only
- Enable PostgreSQL SSL mode (`sslmode=require`) in the `DATABASE_URL`

---

## 10. Design Patterns and Principles

### 10.1 Repository Pattern

All database access is encapsulated in repository classes (`UserRepository`, `DoctorRepository`, `PatientRepository`, `AppointmentRepository`). Routers never execute ORM queries directly. This decouples the HTTP layer from persistence, making it straightforward to swap or mock the data layer in tests.

### 10.2 Dependency Injection (DI)

FastAPI's `Depends()` mechanism injects the database session (`get_db`) and the current user (`get_current_user`) into route handlers. This allows transactional session management (commit on success, rollback on exception) to be handled declaratively in `session.py` rather than scattered through business logic.

### 10.3 Circuit Breaker Pattern

`CircuitBreaker` in `app/core/circuit_breaker.py` wraps calls to external dependencies (PostgreSQL, Redis). The breaker transitions through `CLOSED → OPEN → HALF_OPEN` states, fast-failing requests when a dependency is known to be unavailable rather than exhausting connection pool threads.

### 10.4 Middleware Chain (Decorator / Chain of Responsibility)

FastAPI middleware is applied as a decorator chain. `MessagePackMiddleware` intercepts requests and responses to negotiate content serialisation, and injects the `X-Response-Time` header. Exception handlers are registered globally and apply to all routes uniformly.

### 10.5 Single Source of Truth for Shared State

Workers share no in-memory state. PostgreSQL is the authoritative source for all appointment data. This satisfies the Shared-Nothing architecture principle for stateless horizontal scaling.

### 10.6 Factory Pattern

`create_app()` in `main.py` assembles the FastAPI application, wiring middleware, exception handlers, and routers. This factory pattern simplifies testing (create a test-specific app instance) and separates configuration from construction.

### 10.7 Lifespan Context Manager

The `@asynccontextmanager lifespan` function handles startup (`init_db`, `seed_data`) and shutdown logic in a single, readable location, replacing the deprecated `startup`/`shutdown` event hooks.

---

## 11. Assumptions and Dependencies

### 11.1 Assumptions

- Each appointment occupies exactly one discrete time slot (no duration modelling in the current version).
- Doctors are pre-loaded by the clinic administrator; patients self-register or are provisioned by an admin.
- All clients are assumed to be time-zone-aware and will send `time_slot` values in UTC (or with a UTC offset).
- The Docker Compose deployment runs on a single host; cross-host orchestration (Kubernetes, Swarm) is a future concern.
- Network latency between services is negligible (all on `clinic-net` bridge).

### 11.2 External Dependencies

| Dependency | Version | Purpose | Risk if unavailable |
|---|---|---|---|
| PostgreSQL | 16 | Primary persistence | Total service outage |
| Redis | 7 | Caching / future pub-sub | Degraded (health returns warning) |
| Docker Engine | 24+ | Container runtime | Cannot deploy |
| NGINX | 1.25 | Ingress / LB | Direct worker access only |
| PyPI (build time) | — | Python packages | Build failure |

---

## 12. Constraints

| Constraint | Description |
|---|---|
| **Language** | Python 3.12 only; no other runtime languages |
| **Framework** | FastAPI; migration to another framework requires full re-architecture |
| **ORM** | SQLAlchemy 2.0 async; raw psycopg2 or sync queries are not permitted |
| **bcrypt version** | Pinned to 4.0.1; upgrading requires verifying passlib compatibility |
| **NGINX routing** | New top-level URL paths require an explicit `location` block in `nginx.conf` |
| **Deployment** | Currently Docker Compose only; Kubernetes support is future scope |
| **ENUM types** | PostgreSQL ENUMs cannot be altered without migration; add new values via `ALTER TYPE` |
| **Single DB** | No read replica; heavy read traffic will hit the primary |
| **No Alembic in dev** | `create_all` is used during development; Alembic required for production migrations |

---

## 13. Glossary

| Term | Definition |
|---|---|
| **Appointment** | A reservation of a specific doctor's time slot by a specific patient |
| **Circuit Breaker** | A design pattern that prevents calls to a failing service, allowing it to recover |
| **Consistent Hashing** | An NGINX load-balancing algorithm where requests with the same key are routed to the same upstream |
| **Chaos Engineering** | Deliberate fault injection to test system resilience |
| **ENUM** | A PostgreSQL column type restricted to a predefined set of string values |
| **FR** | Functional Requirement |
| **JWT** | JSON Web Token — a compact, signed representation of claims used for stateless authentication |
| **MessagePack** | A compact binary serialisation format, more efficient than JSON for machine-to-machine communication |
| **NFR** | Non-Functional Requirement |
| **Node ID** | The Docker container's hostname, used to identify which worker processed a given request |
| **Poison Pill** | A deliberately invalid input (`patient_id = 999`) that triggers simulated failure |
| **Repository Pattern** | A data-access abstraction that decouples business logic from ORM/database details |
| **Time Slot** | An ISO 8601 datetime string representing the start of a bookable appointment window |
| **Timezone-naive datetime** | A Python `datetime` object with no `tzinfo`; stored in PostgreSQL as `TIMESTAMP WITHOUT TIME ZONE` |
| **Worker** | A single FastAPI/Uvicorn process instance running in a Docker container |
| **ASGI** | Asynchronous Server Gateway Interface — the Python standard for async web servers |
| **DI** | Dependency Injection — FastAPI's mechanism for providing shared resources to route handlers |
