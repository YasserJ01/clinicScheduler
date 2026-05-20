# Phased Implementation Plan
## Medical Clinic Appointment Scheduler

| Field | Value |
|---|---|
| Document Version | 1.0.0 |
| Status | Draft — For Review |
| Date | 2025 |
| Classification | Internal / Technical |

---

## Table of Contents

1. [Plan Overview](#1-plan-overview)
2. [Phase 0 — Foundation and Infrastructure](#2-phase-0--foundation-and-infrastructure)
3. [Phase 1 — Core Authentication and Data Models](#3-phase-1--core-authentication-and-data-models)
4. [Phase 2 — Appointment Booking Engine](#4-phase-2--appointment-booking-engine)
5. [Phase 3 — Resilience, Observability, and Chaos Engineering](#5-phase-3--resilience-observability-and-chaos-engineering)
6. [Phase 4 — Performance, Scaling, and Load Testing](#6-phase-4--performance-scaling-and-load-testing)
7. [Phase 5 — Security Hardening and Compliance](#7-phase-5--security-hardening-and-compliance)
8. [Phase 6 — Production Readiness and Future Extensions](#8-phase-6--production-readiness-and-future-extensions)
9. [Cross-Phase Quality Gates](#9-cross-phase-quality-gates)
10. [Risk Register](#10-risk-register)
11. [Milestone Summary](#11-milestone-summary)

---

## 1. Plan Overview

### 1.1 Objectives

This document breaks the Clinic Scheduler project into six progressive phases. Each phase produces a tested, deployable increment of the system. Phases are designed so that each one:

- Delivers meaningful, demonstrable functionality
- Does not introduce regressions in previously delivered features
- Is independently releasable to a staging environment
- Has clear, measurable acceptance criteria before progression

### 1.2 Phasing Philosophy

| Principle | Application |
|---|---|
| **Thin vertical slices** | Each phase delivers end-to-end functionality, not just isolated components |
| **Test at every layer** | Unit, integration, and (from Phase 4 onwards) performance tests accompany every phase |
| **Security from day one** | Authentication and authorisation are built in Phase 1, not retrofitted later |
| **Resilience built in** | Circuit breakers and health checks are Phase 3 deliverables, before performance work |
| **Documentation as code** | OpenAPI docs auto-generate; AGENTS.md and this document are updated with every phase |

### 1.3 Team Roles Assumed

| Role | Responsibilities |
|---|---|
| Backend Engineer | FastAPI routes, repository layer, Pydantic schemas |
| DevOps / Platform Engineer | Docker, NGINX, CI/CD pipelines |
| QA Engineer | Test case authoring, load testing, chaos validation |
| Security Reviewer | Code review of auth/data flows, threat modelling |
| Product Owner | Acceptance of phase deliverables against requirements |

---

## 2. Phase 0 — Foundation and Infrastructure

### 2.1 Objectives

Stand up the full infrastructure skeleton before writing any business logic. Every subsequent phase builds on this foundation. At the end of Phase 0, a minimal "hello" API is reachable through the full stack (client → NGINX → Worker → DB → Redis).

### 2.2 Scope

- Docker Compose multi-service definition (`nginx`, `worker`, `db`, `redis`)
- NGINX configuration: upstream pool, rate limiting block, location routing
- Dockerfile: multi-stage build, non-root user, `HEALTHCHECK`
- PostgreSQL service with health check (`pg_isready`)
- Redis service with health check (`redis-cli ping`)
- FastAPI skeleton app (`create_app()`, lifespan hook)
- `GET /api/v1/health` endpoint with DB and Redis connectivity checks
- `init_db()` with idempotent ENUM type creation and `create_all()`
- `seed_data()` for initial doctor records
- Logging configuration (`basicConfig`, structured logger names)
- `.env` / environment variable injection for `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`
- `.dockerignore` and `.gitignore` configured

### 2.3 Deliverables

| Deliverable | Acceptance Criteria |
|---|---|
| `docker compose up -d --build` succeeds | All 5 services start healthy within 60 seconds |
| `GET /api/v1/health` returns `{"status": "ok", ...}` | HTTP 200, both DB and Redis report `healthy` |
| NGINX routes traffic to any of the 3 workers | Verified by checking `node_id` in responses |
| Worker container restarts on crash | Kill a worker; Docker restarts it; health recovers |
| NGINX `X-Response-Time` not yet required | Deferred to Phase 3 middleware work |

### 2.4 Tasks

1. Write `docker-compose.yml` with services, networks, volumes, and health checks
2. Write multi-stage `Dockerfile` (builder → runtime) with `appuser`
3. Write `nginx/nginx.conf` with consistent hashing upstream and rate-limit zone
4. Implement `app/config.py` with `pydantic-settings` and `.env` support
5. Implement `app/db/session.py` — async engine, session factory, `init_db()`
6. Implement `app/main.py` — `create_app()`, lifespan, seed data, CORS
7. Implement `app/api/v1/routers/health.py`
8. Wire NGINX location blocks for `/api/v1/health`, `/docs`, `/redoc`, `/openapi.json`, `/api/`
9. Add `requirements.txt` with all pinned versions
10. Validate with manual smoke tests and document curl examples in `AGENTS.md`

### 2.5 Estimated Effort

| Task Category | Estimate |
|---|---|
| Docker / NGINX configuration | 2 days |
| Database initialisation | 1 day |
| FastAPI skeleton + health | 1 day |
| Testing and documentation | 1 day |
| **Phase 0 Total** | **~5 days** |

---

## 3. Phase 1 — Core Authentication and Data Models

### 3.1 Objectives

Deliver the complete authentication flow and all database models. By the end of Phase 1, a user can register, log in, obtain a JWT, and use it to list doctors and patients. The full data schema is in place, ready for booking logic.

### 3.2 Scope

- SQLAlchemy models: `User`, `Doctor`, `Patient`, `Appointment` (with ENUM columns)
- `UserRepository`, `DoctorRepository`, `PatientRepository`
- `POST /auth/register` and `POST /auth/login` endpoints
- JWT creation (`create_access_token`) and verification (`get_current_user` dependency)
- bcrypt password hashing (`get_password_hash`, `verify_password`)
- `GET /doctors` and `POST /doctors` (admin-only) endpoints
- `GET /patients` and `GET /patients/me` endpoints
- Pydantic request/response schemas for all above endpoints
- Global exception handlers for `SQLAlchemyError`, `CircuitBreakerError`, and unhandled exceptions

### 3.3 Deliverables

| Deliverable | Acceptance Criteria |
|---|---|
| Register with unique username | HTTP 200, returns JWT |
| Duplicate registration rejected | HTTP 400, `"Username already exists"` |
| Login with correct credentials | HTTP 200, returns JWT |
| Login with wrong password | HTTP 401, `"Invalid credentials"` |
| Expired / invalid JWT rejected | HTTP 401 on protected endpoint |
| `GET /doctors` returns seeded doctors | HTTP 200, list of 2 doctors |
| `POST /doctors` rejected for non-admin | HTTP 403 |
| `POST /doctors` succeeds for admin role | HTTP 201, doctor created |
| `GET /patients` returns empty list initially | HTTP 200, `[]` |
| DB error returns structured JSON, not traceback | HTTP 500 with `{"error": "Database error", ...}` |

### 3.4 Tasks

1. Implement `app/models/__init__.py` — all four models with correct ENUM configurations
2. Implement `app/core/security.py` — JWT and bcrypt functions
3. Implement `app/api/v1/dependencies.py` — `get_current_user`
4. Implement `app/db/repository.py` — `UserRepository`, `DoctorRepository`, `PatientRepository`
5. Implement `app/api/v1/routers/auth.py`
6. Implement `app/api/v1/routers/doctors.py`
7. Implement `app/api/v1/routers/patients.py`
8. Register exception handlers in `app/core/exceptions.py`
9. Include all routers in `create_app()`
10. Write unit tests for security functions (hash, verify, token creation)
11. Write integration tests for auth endpoints against a test database
12. Verify all endpoints appear correctly in Swagger UI at `/docs`

### 3.5 Key Technical Notes

- ENUM columns MUST use `values_callable=lambda x: [e.value for e in x]` to prevent SQLAlchemy inserting enum member names instead of values.
- `create_type=False` MUST be set on all ENUM columns to avoid `CREATE TYPE` conflicts (types are created explicitly in `init_db()`).
- JWT decode MUST use an explicit `algorithms=[settings.ALGORITHM]` allowlist to prevent `alg: none` attacks.
- `bcrypt` MUST remain pinned to `4.0.1` in `requirements.txt`.

### 3.6 Estimated Effort

| Task Category | Estimate |
|---|---|
| Models and repositories | 2 days |
| Auth endpoints + security | 2 days |
| Doctor/Patient endpoints | 1 day |
| Exception handlers | 0.5 day |
| Tests and verification | 1.5 days |
| **Phase 1 Total** | **~7 days** |

---

## 4. Phase 2 — Appointment Booking Engine

### 4.1 Objectives

Deliver the full appointment booking flow, including conflict detection, all validation rules, chaos engineering backdoor, and the complete `BookingResponse` schema. This is the core business value of the system.

### 4.2 Scope

- `AppointmentRepository` — `list_all`, `get_by_id`, `create`, `check_conflict`
- `POST /appointments` — full booking flow with doctor/patient validation and conflict detection
- `GET /appointments` — list all appointments with patient name enrichment
- `GET /appointments/{id}` — get single appointment
- `patient_id == 999` chaos trigger (FR-CHAOS-1)
- Time slot parsing, timezone stripping, and ISO 8601 validation
- `BookingResponse` and `AppointmentDetail` Pydantic schemas
- `node_id` embedded in all booking responses (from `socket.gethostname()`)
- HTTP 201 on success, HTTP 409 on conflict, HTTP 400/404 on validation failures, HTTP 503 on chaos trigger

### 4.3 Deliverables

| Deliverable | Acceptance Criteria |
|---|---|
| Book appointment — success | HTTP 201, `success: true`, `appointment` populated |
| Book same slot again | HTTP 409, `success: false`, error names the existing patient |
| Book with invalid doctor ID | HTTP 400, `"Doctor not found"` |
| Book with invalid patient ID | HTTP 404, `"Patient with id X not found"` |
| Book with malformed `time_slot` | HTTP 422, Pydantic validation error |
| Book with `patient_id: 999` | HTTP 503, `"CHAOS: Simulated node failure"` |
| Concurrent bookings (same slot, 2 workers) | One succeeds (201), one conflicts (409); no duplicate inserts |
| List appointments returns all bookings | HTTP 200, ordered by `appointment_time` |
| Get appointment by valid ID | HTTP 200, full `AppointmentDetail` |
| Get appointment by invalid ID | HTTP 404 |
| `node_id` reflects actual container hostname | Verified by checking Docker container name |

### 4.4 Tasks

1. Implement `AppointmentRepository` in `app/db/repository.py`
2. Implement `app/api/v1/routers/appointments.py` — all three handlers
3. Implement `_parse_time_slot()` helper with timezone stripping
4. Add `AppointmentCreate`, `AppointmentDetail`, `BookingResponse` Pydantic models
5. Wire chaos trigger check at the top of `create_appointment` before any DB work
6. Wire `appointments` router in `create_app()`
7. Write integration tests for each acceptance criterion above
8. Write a concurrent test simulating two simultaneous requests for the same slot
9. Validate timezone handling: test with `Z` suffix, `+00:00`, and naive strings
10. Update Swagger UI and verify response schemas are correctly documented
11. Update `AGENTS.md` "Gotchas" section with any new findings

### 4.5 Concurrency Concern: Race Condition on Double Booking

The application-level `check_conflict()` followed by `create()` is a two-step operation that is not atomic at the SQL level. Under concurrent load, two requests can both pass `check_conflict()` and both attempt to `create()`.

**Recommended mitigations (implement in this phase):**

- Add a **partial unique index** to PostgreSQL: `CREATE UNIQUE INDEX uix_appointment_slot ON appointments (doctor_id, appointment_time) WHERE status != 'cancelled';`
- SQLAlchemy will raise an `IntegrityError` on the second insert; catch it in the router and return HTTP 409 with the conflict response
- Alternatively, use `SELECT … FOR UPDATE SKIP LOCKED` in `check_conflict()` to serialise concurrent bookings for the same slot

### 4.6 Estimated Effort

| Task Category | Estimate |
|---|---|
| Repository implementation | 1 day |
| Booking router + schemas | 2 days |
| Concurrency fix (unique index + IntegrityError handling) | 1 day |
| Chaos trigger | 0.5 day |
| Tests (unit + integration + concurrency) | 2 days |
| **Phase 2 Total** | **~6.5 days** |

---

## 5. Phase 3 — Resilience, Observability, and Chaos Engineering

### 5.1 Objectives

Harden the system against partial failures, make runtime behaviour observable, and validate that chaos engineering features work correctly under automated test conditions.

### 5.2 Scope

- `MessagePackMiddleware` — content negotiation and `X-Response-Time` header injection
- `CircuitBreaker` — full state machine (CLOSED, OPEN, HALF_OPEN), DB and Redis breakers
- Container `HEALTHCHECK` — verified to trigger Docker restart
- Chaos backdoor validation — automated test for HTTP 503 + correct error body
- Circuit breaker integration test — simulate DB failure, verify OPEN state, verify HALF_OPEN recovery
- Structured logging — consistent log format across all modules
- NGINX retry configuration — `proxy_next_upstream error timeout http_502 http_503`

### 5.3 Deliverables

| Deliverable | Acceptance Criteria |
|---|---|
| `X-Response-Time` header present | All responses include `X-Response-Time: <N>ms` |
| MessagePack negotiation works | `Accept: application/x-msgpack` returns binary response with correct content-type |
| Circuit breaker opens on DB failure | After 5 DB failures, requests return 503 immediately (no DB calls) |
| Circuit breaker recovers | After 15 seconds, one test call is allowed through (HALF_OPEN) |
| Redis failure degrades gracefully | Redis down → health returns `redis: "unhealthy"` but service continues |
| NGINX retries on 502 | Kill one worker mid-request; NGINX routes to another (max 2 retries) |
| Chaos trigger logged at ERROR | `CHAOS:` appears in worker logs with patient_id and node_id |

### 5.4 Tasks

1. Implement `app/core/middleware.py` — `MessagePackMiddleware` with timing header
2. Implement `app/core/circuit_breaker.py` — `CircuitBreaker` state machine, `db_breaker`, `redis_breaker`
3. Add circuit breaker to health check endpoint for DB and Redis probes
4. Write tests for circuit breaker: CLOSED → OPEN transition, OPEN → HALF_OPEN timeout, HALF_OPEN → CLOSED on success
5. Write tests for MessagePack middleware — send `Accept: application/x-msgpack`, verify binary response
6. Write chaos backdoor tests — verify 503 response body and log output
7. Validate `HEALTHCHECK` in Dockerfile by simulating a hung worker
8. Review and finalise NGINX retry configuration (`proxy_next_upstream_tries 2`)
9. Document chaos testing procedures in `AGENTS.md`

### 5.5 Estimated Effort

| Task Category | Estimate |
|---|---|
| Middleware implementation | 1 day |
| Circuit breaker implementation | 1.5 days |
| Chaos + resilience tests | 1.5 days |
| NGINX retry validation | 0.5 day |
| Logging review and structured format | 0.5 day |
| **Phase 3 Total** | **~5 days** |

---

## 6. Phase 4 — Performance, Scaling, and Load Testing

### 6.1 Objectives

Validate that the system meets NFR-PERF targets under load and tune any bottlenecks discovered. Establish the k6 load test as a repeatable benchmark in the CI/CD pipeline.

### 6.2 Scope

- k6 load test script (`loadtest/scheduler.js`) — 30s ramp → 200 VUs → 30s ramp-down
- Load test thresholds: p95 latency < 500 ms, HTTP error rate < 5%, app error rate < 10%
- NGINX rate limit configured at 500 r/s for load testing (document production reduction to ~30 r/s)
- Connection pool tuning validation (`pool_size=20`, `max_overflow=10`, `pool_timeout=10s`)
- PostgreSQL index review — confirm query plans use indexes for `doctor_id`, `appointment_time`, `patient_id`
- Redis eviction policy validation (`allkeys-lru`, 128 MB cap)
- Worker replica scaling test — confirm 3 replicas outperform 1 under load

### 6.3 Deliverables

| Deliverable | Acceptance Criteria |
|---|---|
| Load test passes all thresholds | p95 < 500ms, error rate < 5%, app errors < 10% |
| `GET /doctors` is the primary load path | 200 VUs served successfully for 1 minute |
| No OOM errors | Redis stays below 128 MB throughout |
| No connection pool exhaustion | No `pool_timeout` errors in worker logs |
| Consistent hashing distributes load | All 3 workers show roughly equal request counts in logs |

### 6.4 Tasks

1. Implement `loadtest/scheduler.js` with options, setup (register/login), and default function
2. Run baseline with 1 worker; run again with 3 workers; document the difference
3. Profile slow queries with `EXPLAIN ANALYSE` — add indexes where missing
4. Tune `pool_size` / `max_overflow` if pool exhaustion is observed
5. Add `EXPLAIN ANALYSE` for `check_conflict()` query to confirm index usage on `(doctor_id, appointment_time)`
6. Document load test run command and k6 binary path in `AGENTS.md`
7. Add load test to CI pipeline as a post-deploy smoke test (reduced VU count for CI speed)
8. Produce a written load test report with actual vs. target latency percentiles

### 6.5 Notes on k6 Setup

- k6 binary path on Windows: `C:\Program Files\k6\k6.exe` (not on PATH by default)
- Run command: `& "C:\Program Files\k6\k6.exe" run loadtest/scheduler.js`
- On Linux/macOS: install via package manager; `k6 run loadtest/scheduler.js`
- Set `BASE_URL` env var to point at a non-localhost target for realistic testing

### 6.6 Estimated Effort

| Task Category | Estimate |
|---|---|
| k6 script implementation | 1 day |
| Baseline and scaling load test runs | 1 day |
| DB index and query plan review | 1 day |
| Connection pool / Redis tuning | 0.5 day |
| Report and CI integration | 1 day |
| **Phase 4 Total** | **~4.5 days** |

---

## 7. Phase 5 — Security Hardening and Compliance

### 7.1 Objectives

Ensure the system meets NFR-SEC and NFR-PRIV requirements. Perform a structured security review, address any findings, and produce documentation suitable for a compliance audit.

### 7.2 Scope

- TLS termination at NGINX (self-signed for staging; production CA certificate)
- CORS lockdown — restrict `allow_origins` to known front-end origin(s)
- `SECRET_KEY` rotation procedure documented
- Token `alg: none` attack test — verify system rejects it
- SQL injection attempt tests — verify parameterised queries block injection
- OWASP API Security Top 10 review
- GDPR compliance review — data minimisation, retention policy, right-to-erasure endpoint
- Audit logging — append structured audit entries for appointment create/update/cancel
- Rate limit reduction recommendation documented for production
- Password policy enforcement — max 72-byte check added to registration endpoint
- Penetration test (manual or automated) of auth endpoints

### 7.3 Deliverables

| Deliverable | Acceptance Criteria |
|---|---|
| TLS active in staging | `https://` requests succeed; `http://` redirects |
| `alg: none` JWT rejected | HTTP 401 returned |
| CORS restricted | Cross-origin requests from unknown origins receive 403 |
| SQL injection attempts return 422 or 400 | No raw SQL in error responses |
| Audit log entries present | Every booking/cancel creates a log entry with actor, timestamp, outcome |
| GDPR data export endpoint | `GET /admin/patients/{id}/export` returns NDJSON of patient data |
| GDPR erasure endpoint | `DELETE /admin/patients/{id}` anonymises patient record |
| Security review report | Written document listing findings and resolutions |

### 7.4 Tasks

1. Obtain or generate TLS certificate; configure NGINX `ssl_certificate` and `ssl_certificate_key`
2. Add HTTP → HTTPS redirect in NGINX server block
3. Update CORS middleware `allow_origins` from `["*"]` to `[settings.FRONTEND_URL]`
4. Add `FRONTEND_URL` to `app/config.py` and `docker-compose.yml` environment
5. Write a token-rejection test with `alg: none` payload
6. Add password length validation (≤ 72 bytes) to `RegisterRequest` with a Pydantic validator
7. Implement audit logging: decorator or explicit log statement in each appointment mutation handler
8. Implement `GET /api/v1/admin/patients/{id}/export` (admin-only)
9. Implement `DELETE /api/v1/admin/patients/{id}` that anonymises name/email/phone (admin-only)
10. Conduct OWASP API Top 10 review checklist and remediate findings
11. Update `AGENTS.md` with security configuration notes

### 7.5 Estimated Effort

| Task Category | Estimate |
|---|---|
| TLS + CORS configuration | 1 day |
| Auth security tests | 1 day |
| Audit logging | 1 day |
| GDPR endpoints | 1 day |
| Security review + report | 2 days |
| **Phase 5 Total** | **~6 days** |

---

## 8. Phase 6 — Production Readiness and Future Extensions

### 8.1 Objectives

Prepare the system for production deployment and implement the highest-priority enhancement features. Establish the Alembic migration workflow to replace `create_all` in production.

### 8.2 Scope

#### 8.2.1 Production Readiness

- Alembic migration setup — replace `create_all` for production; add partial unique index migration
- CI/CD pipeline — automated build, test, lint, and optional load-test stages
- Environment-specific Docker Compose overrides (`docker-compose.prod.yml`)
- Prometheus metrics endpoint (`GET /api/v1/metrics`) — request counts, latency histograms, circuit breaker state
- Centralised log aggregation setup (Loki or CloudWatch recommended)
- Graceful shutdown — SIGTERM handler to drain in-flight requests
- Kubernetes Helm chart or Compose-to-Kubernetes translation (optional)

#### 8.2.2 Feature Extensions

| Feature | Priority | Notes |
|---|---|---|
| Appointment duration modelling | High | Add `duration_minutes` column; update conflict check to range overlap |
| Doctor availability windows | High | `DoctorSchedule` table defining bookable hours per weekday |
| Email notifications on booking | Medium | Send confirmation email via SMTP or SendGrid on create/cancel |
| Patient self-registration portal | Medium | A minimal HTML/JS front end consumed via the existing REST API |
| Appointment reminders | Medium | Redis-based scheduled job 24 hours before appointment |
| Admin dashboard (read-only) | Medium | Simple metrics view: total bookings, cancellation rate, busiest doctor |
| Recurring appointments | Low | Weekly/monthly recurring booking with conflict detection across occurrences |
| Multi-tenant (multi-clinic) support | Low | Tenant ID column on all entities; NGINX routing by subdomain |
| Payment integration | Low | Deferred — out of original scope |
| Telemedicine / video link | Low | Deferred — out of original scope |

### 8.3 Deliverables

| Deliverable | Acceptance Criteria |
|---|---|
| Alembic baseline migration | `alembic upgrade head` creates schema; `create_all` removed from production flow |
| CI/CD pipeline | PR → build → test → lint → (optional) load test → merge |
| Prometheus metrics endpoint | Scraped successfully by a Prometheus instance |
| Production compose override | `docker compose -f docker-compose.yml -f docker-compose.prod.yml up` works |
| At least one Phase 6 feature extension | Agreed with Product Owner before sprint start |

### 8.4 Alembic Setup Steps

1. `pip install alembic` (already in `requirements.txt`)
2. `alembic init alembic` — creates `alembic/` directory and `alembic.ini`
3. Configure `sqlalchemy.url` in `alembic.ini` or use `env.py` to read from `settings.DATABASE_URL`
4. Generate baseline: `alembic revision --autogenerate -m "initial_schema"`
5. Review generated migration; add partial unique index manually:
   ```python
   op.create_index(
       'uix_appointment_slot',
       'appointments',
       ['doctor_id', 'appointment_time'],
       unique=True,
       postgresql_where=sa.text("status != 'cancelled'")
   )
   ```
6. Apply: `alembic upgrade head`
7. For each subsequent schema change: `alembic revision --autogenerate -m "<description>"`, review, apply

### 8.5 Estimated Effort

| Task Category | Estimate |
|---|---|
| Alembic setup and migration | 2 days |
| CI/CD pipeline | 2 days |
| Prometheus metrics | 1.5 days |
| Compose prod override | 0.5 day |
| First feature extension (duration modelling) | 3 days |
| **Phase 6 Total** | **~9 days** |

---

## 9. Cross-Phase Quality Gates

Each phase MUST pass the following gates before the team declares it complete and begins the next phase.

### 9.1 Universal Gates (All Phases)

| Gate | Method |
|---|---|
| All new code reviewed by a second engineer | Pull request with at least 1 approver |
| No new `CRITICAL` or `HIGH` linting errors | `ruff` or `flake8` with project configuration |
| Docker Compose build succeeds from clean checkout | `docker compose down -v && docker compose up -d --build` |
| `GET /api/v1/health` returns 200 | Automated smoke test in CI |
| All new endpoints documented in Swagger UI | Manual verification at `/docs` |
| `AGENTS.md` updated with any new gotchas | Checked in PR description |

### 9.2 Phase-Specific Gates

| Phase | Additional Gate |
|---|---|
| Phase 1 | Auth integration tests pass (register, login, JWT validation) |
| Phase 2 | Booking integration tests pass including conflict and chaos cases |
| Phase 3 | Circuit breaker unit tests pass; middleware tests pass |
| Phase 4 | k6 load test passes all three thresholds (p95, error rate, app error rate) |
| Phase 5 | Security review report signed off; no OWASP Top 10 critical findings outstanding |
| Phase 6 | `alembic upgrade head` completes with no errors on a fresh DB |

### 9.3 Recommended Testing Stack

| Test Type | Tool | When Run |
|---|---|---|
| Unit tests | `pytest` + `pytest-asyncio` | Every commit |
| Integration tests | `pytest` + `httpx.AsyncClient` + test DB | Every PR |
| Load tests | k6 | Post-deploy to staging |
| Linting | `ruff` | Every commit (pre-commit hook) |
| Type checking | `mypy` (optional, incremental) | Every PR |
| Security scan | `bandit` | Every PR |

---

## 10. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Double-booking race condition under load | Medium | High | Partial unique index + `IntegrityError` handling (Phase 2) |
| bcrypt version upgrade breaks passlib | Low | High | Pin `bcrypt==4.0.1`; test before any upgrade |
| PostgreSQL ENUM type conflicts on re-deploy | Medium | Medium | Idempotent `DO $$ BEGIN … EXCEPTION` pattern; Alembic from Phase 6 |
| JWT secret exposed in environment | Low | Critical | Use Docker secrets or Vault in production; rotate on suspicion |
| Single PostgreSQL instance is a SPOF | High | High | Accept in Phase 0–5; plan read replica or managed DB in Phase 6 |
| Redis data loss on restart | Medium | Low | Redis is auxiliary; no write-critical data stored there in current scope |
| NGINX rate limit too aggressive for legitimate users | Low | Medium | Tune burst and rate per environment; separate zones for health vs. API |
| k6 load test not representative of production traffic | Medium | Medium | Expand test to cover booking endpoint, not just `GET /doctors` |
| GDPR erasure breaks referential integrity | Low | High | Anonymise (replace with placeholder) rather than hard-delete; review FK constraints |

---

## 11. Milestone Summary

| Milestone | Phase | Key Outcome | Target Completion |
|---|---|---|---|
| M0: Infrastructure Live | Phase 0 | Full stack running; health endpoint green | Week 1 |
| M1: Auth Complete | Phase 1 | Register, login, JWT, role-based access | Week 2–3 |
| M2: Booking Engine Live | Phase 2 | Appointments bookable; conflicts detected | Week 4–5 |
| M3: Resilience Validated | Phase 3 | Circuit breakers, middleware, chaos tests | Week 6 |
| M4: Performance Certified | Phase 4 | Load test passes; indexes tuned | Week 7–8 |
| M5: Security Hardened | Phase 5 | TLS, CORS, GDPR, audit log | Week 9–10 |
| M6: Production Ready | Phase 6 | Alembic, CI/CD, metrics, first extension | Week 12–13 |

> Timelines above assume a small team (2–3 backend engineers + 1 DevOps) working in parallel. Actual timelines should be adjusted per team velocity and discovery findings.
