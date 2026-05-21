# Clinic Scheduler — Agent Instructions

## Quick Start

```bash
docker compose up -d --build          # Start all services (nginx + 3 workers + postgres + redis)
docker compose down -v                # Tear down everything including DB volume
```

**Ports**: NGINX on `:80`, Postgres on `:5433`, Redis on `:6380` (host-mapped to avoid conflicts).

## Architecture

- **NGINX** (port 80) → consistent hashing LB → **3 FastAPI workers** (port 8000 each) → **Postgres** + **Redis**
- All containers share `clinic-net` bridge network. Service discovery via Docker DNS (`db`, `redis`, `worker`).
- Health check endpoint: `GET /api/v1/health` (checks DB + Redis connectivity).
- Swagger UI: `http://localhost:80/docs`, ReDoc: `http://localhost:80/redoc`.

## Key File Map

| Path | Purpose |
|---|---|
| `app/main.py` | FastAPI entrypoint. `lifespan` runs `init_db()` then `seed_data()` on startup. |
| `app/db/session.py` | Async engine + session factory. `init_db()` creates ENUM types + tables + partial unique index. |
| `app/db/repository.py` | Data access layer. All DB queries go through repository classes. |
| `app/models/__init__.py` | SQLAlchemy models: `User`, `Doctor`, `Patient`, `Appointment`, `AuditLog`. |
| `app/api/v1/routers/` | Route handlers: `auth`, `doctors`, `patients`, `appointments`, `health`, `admin`, `metrics`. |
| `app/core/middleware.py` | MessagePack serialization + `X-Response-Time` header. |
| `app/core/circuit_breaker.py` | Circuit breaker for DB/Redis partial failure isolation. |
| `app/core/security.py` | JWT creation (with role claim), bcrypt password hashing. |
| `app/core/metrics.py` | Async Redis-backed Prometheus metrics collector. |
| `app/core/metrics_middleware.py` | HTTP request tracking middleware (async). |
| `app/core/request_id_middleware.py` | X-Request-ID correlation header middleware. |
| `app/core/audit.py` | Audit logging helper (DB + stdout). |
| `nginx/nginx.conf` | NGINX config: consistent hashing, rate limiting (500r/s), retry on 502/503. |
| `loadtest/scheduler.js` | k6 load test: 30s ramp to 50 VUs, 1m at 200 VUs, 30s ramp down. |
| `tests/unit/test_security.py` | 15 unit tests: password hashing, JWT creation/validation, `alg: none` attack. |
| `tests/integration/test_auth.py` | 10 integration tests: register, login, JWT validation. |
| `tests/integration/test_doctors.py` | 6 integration tests: list doctors, create doctor (admin). |
| `tests/integration/test_patients.py` | 6 integration tests: list patients, profile, real patient ID lookup. |
| `tests/integration/test_appointments.py` | 14 integration tests: booking success/conflict/validation, list, get by ID. |
| `tests/integration/test_concurrent_booking.py` | 1 integration test: concurrent same-slot booking (201 + 409). |
| `tests/integration/test_timezone.py` | 5 integration tests: Z suffix, UTC offset, naive datetime, invalid strings. |
| `tests/unit/test_circuit_breaker.py` | 8 unit tests: CLOSED→OPEN→HALF_OPEN→CLOSED state machine transitions. |
| `tests/integration/test_circuit_breaker.py` | 5 integration tests: health check with circuit breakers, breaker state validation. |
| `tests/integration/test_middleware.py` | 6 integration tests: MessagePack content negotiation, X-Response-Time header. |
| `tests/integration/test_chaos.py` | 2 integration tests: chaos backdoor (patient_id 999) returns 503. |
| `tests/integration/test_security_phase5.py` | 10 integration tests: SQL injection, password policy, alg: none attack. |
| `tests/integration/test_admin.py` | 7 integration tests: GDPR export (NDJSON), patient anonymisation, RBAC. |
| `loadtest/scheduler.js` | k6 load test: read/write scenarios, 50-200 VUs, p95<500ms threshold. |
| `docker-compose.baseline.yml` | Override file for 1-worker baseline load testing. |
| `tests/unit/test_metrics_async.py` | 5 unit tests: async Redis calls are awaited. |
| `tests/unit/test_conflict_query.py` | 1 unit test: SQL includes lower-bound filter. |
| `tests/unit/test_patient_repository.py` | 3 unit tests: email-based patient lookup. |
| `tests/conftest.py` | Pytest fixtures: HTTP client, admin/user tokens, auth headers, patient_id, future_time_slot. |

## Gotchas

### Appointment booking API (FR-1)
- Request body uses `patient_id` (int or string) and `time_slot` (ISO 8601 string), **not** `patient_name` and `appointment_time`.
- Success returns HTTP 201 with `{"success": true, "node_id": "<container_id>", "error": null, "appointment": {...}}`.
- Conflict returns HTTP 409 with `{"success": false, "error": "Slot already occupied by patient <name>", "appointment": {...}}`.
- `check_conflict()` in `app/db/repository.py` returns `Appointment | None` (not bool) so the error message includes who holds the slot.
- Node ID comes from `socket.gethostname()` — inside Docker this is the container ID.

### Chaos backdoor (FR-2)
- `patient_id == 999` (int or string) triggers an HTTP 503 with `{"detail": "CHAOS: Simulated node failure"}` **only when `CHAOS_ENABLED=true`**.
- Default is `CHAOS_ENABLED=false` (production-safe). Development `docker-compose.yml` sets `CHAOS_ENABLED=true`.
- The check runs before any DB work in `app/api/v1/routers/appointments.py:create_appointment`.

### PostgreSQL ENUM types
- DB uses `TIMESTAMP WITHOUT TIME ZONE`. Pydantic parses ISO timestamps with `Z` suffix as timezone-aware datetimes. **Always strip tzinfo before DB operations** — see `app/db/repository.py:96` and `app/db/repository.py:110`.
- ENUM columns must use `values_callable=lambda x: [e.value for e in x]` in model definitions, or SQLAlchemy inserts the enum member name (`PATIENT`) instead of the value (`patient`).
- `CREATE TYPE` has no `IF NOT EXISTS` in Postgres. Use the `DO $$ BEGIN ... EXCEPTION WHEN duplicate_object` pattern in `app/db/session.py`.

### Multi-worker state
- **Never use in-memory lists/dicts for shared state.** Three workers run independently; each request may hit a different worker. All persistent data must go through PostgreSQL.
- `seed_data()` in `app/main.py` only inserts doctors if the table is empty (idempotent).

### NGINX routing
- Only `/api/`, `/docs`, `/redoc`, and `/openapi.json` are proxied. Any new top-level route must be added to `nginx/nginx.conf`.
- Health check is at `/api/v1/health` (not `/health`). Dockerfile HEALTHCHECK uses this path.

### k6 load test
- k6 binary location: `C:\Program Files\k6\k6.exe` (not on PATH).
- Run: `& "C:\Program Files\k6\k6.exe" run loadtest/scheduler.js`
- Rate limit in NGINX is set to 500r/s for load testing. For production, reduce to ~30r/s.

### bcrypt / passlib compatibility
- `requirements.txt` pins `bcrypt==4.0.1`. Newer bcrypt versions break passlib 1.7.4 with a `ValueError: password cannot be longer than 72 bytes` error.

### JWT role claims
- Both `POST /auth/register` and `POST /auth/login` embed the user's `role` in the JWT payload as a `role` claim.
- `get_current_user` in `app/api/v1/dependencies.py` reads `payload.get("role")` for RBAC checks.
- `create_access_token()` accepts optional `extra_claims` dict for adding custom claims.

### Concurrency: Double-booking prevention
- A partial unique index `uix_appointment_slot` is created in `init_db()` on `(doctor_id, appointment_time) WHERE status != 'cancelled'`.
- `create_appointment` in `appointments.py` catches `IntegrityError` from concurrent inserts and returns HTTP 409.
- The two-step `check_conflict()` → `create()` is NOT atomic; the unique index is the final guard.

### Patient creation
- `POST /api/v1/patients` creates or retrieves a patient by email (idempotent via `get_or_create_by_email`).
- Required before booking appointments — the booking endpoint validates patient existence.
- `GET /api/v1/patients/me` looks up the patient by email convention (`{username}@clinic.com`) and returns the real `id` if found.

### Circuit Breaker (Phase 3)
- `app/core/circuit_breaker.py` implements a full state machine: CLOSED → OPEN → HALF_OPEN → CLOSED.
- `db_breaker`: 5 failure threshold, 15-second recovery timeout.
- `redis_breaker`: 3 failure threshold, 10-second recovery timeout.
- Health check endpoint wraps DB/Redis probes with circuit breaker calls.
- When OPEN, circuit breaker raises `CircuitBreakerError` immediately without calling the underlying function.
- State is in-memory per worker; each of the 3 workers maintains its own breaker state.

### MessagePack Middleware (Phase 3)
- `app/core/middleware.py` adds `X-Response-Time: <N>ms` header to all responses.
- Send `Accept: application/x-msgpack` to receive binary MessagePack-encoded responses.
- POST/PUT/PATCH with `Content-Type: application/x-msgpack` decodes request body automatically.
- Middleware is wired in `app/main.py` via `app.add_middleware(MessagePackMiddleware)`.

### Chaos Testing Procedures (Phase 3)
- **Trigger**: Send any booking request with `patient_id: 999` or `patient_id: "999"`.
- **Expected**: HTTP 503 with `{"detail": "CHAOS: Simulated node failure"}`.
- **Log output**: `CHAOS: Poison pill detected — patient_id=999 on node <container_id>` at ERROR level.
- **Test command**: `python -m pytest tests/integration/test_chaos.py -v`
- **Verify in logs**: `docker logs clinic-scheduler-worker-1 | grep CHAOS`
- **NGINX retry**: With `proxy_next_upstream` configured, NGINX may retry the request on another worker if the first returns 503.

### Structured Logging
- All modules use named loggers: `clinic.appointments`, `clinic.exceptions`, `clinic.health`.
- CHAOS errors include `patient_id` and `node_id` for traceability.
- Booking success logs include `appt_id` and `node_id`.
- Health check failures log the specific probe (DB or Redis) and error message.

### Load Testing (Phase 4)
- k6 binary location: `C:\Program Files\k6\k6.exe` (not on PATH).
- **Read-heavy test** (default): `& "C:\Program Files\k6\k6.exe" run loadtest/scheduler.js`
- **Write-heavy test**: `& "C:\Program Files\k6\k6.exe" run --env SCENARIO=write loadtest/scheduler.js`
- **Baseline test (1 worker)**: `docker compose -f docker-compose.yml -f docker-compose.baseline.yml up -d`
- **Scaling test (3 workers)**: `docker compose up -d`
- **Thresholds**: p95 < 500ms, HTTP error < 5%, app error < 10%
- **Results**: 3 workers show 20% higher throughput and 55% lower p95 latency vs 1 worker
- **Production rate limit**: Reduce from 500 r/s to ~30 r/s per IP in production
- **Note**: k6 may OOM on Windows with 200 VUs; use 50 VUs for local testing

### Database Indexes
- `uix_appointment_slot`: Partial unique index on `(doctor_id, appointment_time) WHERE status != 'cancelled'`
- `ix_appointments_appointment_time`: B-tree index on `appointment_time`
- `ix_appointments_doctor_id`: B-tree index on `doctor_id`
- `ix_appointments_patient_id`: B-tree index on `patient_id`
- All critical queries use indexes; sub-millisecond execution even with 26k+ rows

### CORS Configuration (Phase 5)
- `FRONTEND_URL` env var controls allowed origins. Default: `*` (development).
- Set to a specific origin for production: `FRONTEND_URL=https://app.clinic.example.com`
- When `FRONTEND_URL != "*"`, CORS middleware allows only that single origin.

### Password Policy (Phase 5)
- Passwords > 72 bytes are rejected at registration with HTTP 422.
- Byte count uses UTF-8 encoding (`len(password.encode("utf-8"))`), not character count.
- Unicode characters may use multiple bytes (e.g., `é` = 2 bytes).

### Audit Logging (Phase 5)
- `app/core/audit.py` provides `audit_log()` helper that writes to both `audit_log` DB table and stdout.
- Every `POST /appointments` creates an audit entry with actor, action, entity details.
- Every `DELETE /admin/patients/{id}` creates an audit entry with original/anonymised names.
- Audit entries are append-only; no update/delete operations on `audit_log` table.
- Never logs sensitive data (passwords, tokens, connection strings).

### GDPR Endpoints (Phase 5)
- `GET /api/v1/admin/patients/{id}/export` — returns NDJSON stream of patient + appointments data.
- `DELETE /api/v1/admin/patients/{id}` — anonymises patient (name → `ANONYMIZED-{id}`, email → `anonymized-{id}@redacted.local`, phone → NULL).
- Both endpoints require `admin` role. Non-admin receives HTTP 403.
- Anonymisation preserves FK integrity (appointments are NOT deleted).
- Export content-type: `application/x-ndjson`.

### TLS (Phase 5 — Documented Only)
- TLS is NOT implemented in the current stack. Procedure documented in `Phase5_Security_Review_Report.md`.
- For staging: generate self-signed cert with `openssl req -x509 ...` and mount in NGINX.
- For production: use Let's Encrypt (certbot) with automated renewal.

### SECRET_KEY Rotation (Phase 5)
- Generate new key: `python -c "import secrets; print(secrets.token_hex(32))"`
- Changing key invalidates all existing JWTs (users must re-authenticate).
- For zero-downtime rotation: implement dual-key validation with fallback period.

### Alembic Migrations (Phase 6, updated Phase 7)
- Development: `init_db()` uses `create_all` (default).
- Production: set `ALEMBIC_ENABLED=true` to use Alembic.
- Generate migration: `alembic revision --autogenerate -m "description"`
- Apply migration: `alembic upgrade head`
- Migrations: `001_initial_schema`, `002_add_duration_minutes`, `003_fix_doctor_is_active_boolean`, `004_audit_log_indexes`

### Prometheus Metrics (Phase 6, updated Phase 7)
- `GET /api/v1/metrics` — returns Prometheus exposition format.
- Metrics stored in Redis (persistent across worker restarts).
- Uses **async Redis** (`redis.asyncio`) — no event loop blocking.
- Tracks: `http_requests_total`, `http_request_duration_seconds`, `appointment_bookings_total`, `circuit_breaker_state`.
- No auth required (for Prometheus scraping).
- NGINX location block added (no rate limiting).

### Appointment Duration (Phase 6)
- `duration_minutes` field on appointments (default: 30, range: 5-480).
- Conflict detection uses range overlap: `new_start < existing_end AND new_end > existing_start`.
- `GET /api/v1/appointments/available?doctor_id=1&date=2026-06-15T00:00:00Z&duration_minutes=30` returns available slots.
- Available slots: 30-minute intervals from 08:00 to 17:00, excluding booked ranges.

### Graceful Shutdown (Phase 6)
- Dockerfile uses `--timeout-graceful-shutdown 10`.
- `lifespan` shutdown disposes engine (closes DB pool).
- Production: `stop_grace_period: 15s` in `docker-compose.prod.yml`.

### CI/CD Pipeline (Phase 6)
- `.github/workflows/ci.yml` runs on push/PR to `main`.
- Stages: lint (ruff), security (bandit), unit tests, integration tests.
- Integration tests use GitHub Actions services for Postgres + Redis.

### Production Deployment (Phase 6)
- Use `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`.
- Required env vars: `SECRET_KEY`, `DB_PASSWORD`, `REDIS_PASSWORD`, `FRONTEND_URL`.
- Memory/CPU limits configured per service.
- Redis requires password authentication in production.
- `CHAOS_ENABLED` defaults to `false` in production (do not set it).

### Doctor `is_active` Column (Phase 7)
- `is_active` is now a `BOOLEAN` column (was `VARCHAR(10)`).
- `DoctorRepository.list_all()` filters on `Doctor.is_active.is_(True)`.
- Migration `003` converts existing `"true"`/`"false"` strings to proper booleans.

### Audit Log Indexes (Phase 7)
- `ix_audit_log_actor` on `actor` — query by user
- `ix_audit_log_entity` on `(entity_type, entity_id)` — query by entity
- `ix_audit_log_created_at` on `created_at` — query by date range
- Migration `004` creates these indexes.

### X-Request-ID Header (Phase 7)
- All responses include `X-Request-ID` header (UUIDv4 or forwarded from client).
- Useful for correlating logs across NGINX, workers, and database.
- Middleware wired in `app/main.py` after `MetricsMiddleware`.

### Async Metrics (Phase 7)
- `MetricsCollector` uses `redis.asyncio` — all methods are `async def`.
- `MetricsMiddleware.dispatch()` awaits all metric calls.
- No event loop blocking under load.

### Conflict Query Optimization (Phase 7)
- `check_conflict()` now includes a lower-bound filter: `appointment_time >= naive_time - 480 minutes`.
- Prevents full-table scans on large appointment tables.

## Dev Commands

```bash
# Rebuild workers only (no volume reset)
docker compose up -d --build

# Full reset (destroys DB data)
docker compose down -v && docker compose up -d --build

# Check worker logs
docker logs clinic-scheduler-worker-1

# Reload NGINX config without restart
docker compose exec nginx nginx -s reload

# Run load test
& "C:\Program Files\k6\k6.exe" run loadtest/scheduler.js

# Run automated tests (requires running Docker stack)
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/unit/test_security.py -v
python -m pytest tests/integration/test_auth.py -v
python -m pytest tests/integration/test_appointments.py -v
python -m pytest tests/integration/test_concurrent_booking.py -v
python -m pytest tests/integration/test_timezone.py -v
python -m pytest tests/unit/test_circuit_breaker.py -v
python -m pytest tests/integration/test_circuit_breaker.py -v
python -m pytest tests/integration/test_middleware.py -v
python -m pytest tests/integration/test_chaos.py -v
python -m pytest tests/integration/test_security_phase5.py -v
python -m pytest tests/integration/test_admin.py -v

# Verify chaos trigger in worker logs
docker logs clinic-scheduler-worker-1 | grep CHAOS

# Run load tests (requires k6 installed)
& "C:\Program Files\k6\k6.exe" run loadtest/scheduler.js
& "C:\Program Files\k6\k6.exe" run --env SCENARIO=write loadtest/scheduler.js

# Run baseline load test (1 worker)
docker compose -f docker-compose.yml -f docker-compose.baseline.yml up -d
& "C:\Program Files\k6\k6.exe" run loadtest/scheduler.js

# Run scaling load test (3 workers)
docker compose up -d
& "C:\Program Files\k6\k6.exe" run loadtest/scheduler.js

# Check Redis memory usage
docker compose exec redis redis-cli INFO memory

# Verify database indexes
docker compose exec db psql -U clinic -d clinic_db -c "\di"

# Run EXPLAIN ANALYSE on check_conflict query
docker compose exec db psql -U clinic -d clinic_db -c "EXPLAIN ANALYSE SELECT * FROM appointments WHERE doctor_id = 1 AND appointment_time = '2027-01-01 10:00:00' AND status != 'cancelled';"

# View audit log entries
docker compose exec db psql -U clinic -d clinic_db -c "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 10;"

# Test GDPR export (requires admin token)
curl -s -H "Authorization: Bearer <admin_token>" http://localhost/api/v1/admin/patients/1/export

# Test GDPR anonymisation (requires admin token)
curl -s -X DELETE -H "Authorization: Bearer <admin_token>" http://localhost/api/v1/admin/patients/1

# Set CORS to specific origin
FRONTEND_URL=https://app.clinic.example.com docker compose up -d

# Run Alembic migrations (production)
ALEMBIC_ENABLED=true docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Generate new Alembic migration
alembic revision --autogenerate -m "description"

# Apply Alembic migrations
alembic upgrade head

# Check Prometheus metrics
curl -s http://localhost/api/v1/metrics

# Run production stack
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Run duration tests
python -m pytest tests/unit/test_duration.py -v
python -m pytest tests/integration/test_duration.py -v

# Run metrics tests
python -m pytest tests/integration/test_metrics.py -v

# Run Phase 7 unit tests
python -m pytest tests/unit/test_metrics_async.py -v
python -m pytest tests/unit/test_conflict_query.py -v
python -m pytest tests/unit/test_patient_repository.py -v

# Verify X-Request-ID header
python -c "import httpx; r = httpx.get('http://localhost/api/v1/health'); print(r.headers.get('X-Request-ID'))"

# Verify async metrics (no blocking)
python -c "import httpx; r = httpx.get('http://localhost/api/v1/metrics'); print(r.status_code, 'http_requests_total' in r.text)"

# Verify CHAOS_ENABLED is off in production (default)
python -c "from app.config import settings; print('CHAOS_ENABLED:', settings.CHAOS_ENABLED)"
```
