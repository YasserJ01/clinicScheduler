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
| `app/models/__init__.py` | SQLAlchemy models: `User`, `Doctor`, `Patient`, `Appointment`. |
| `app/api/v1/routers/` | Route handlers: `auth`, `doctors`, `patients`, `appointments`, `health`. |
| `app/core/middleware.py` | MessagePack serialization + `X-Response-Time` header. |
| `app/core/circuit_breaker.py` | Circuit breaker for DB/Redis partial failure isolation. |
| `app/core/security.py` | JWT creation (with role claim), bcrypt password hashing. |
| `nginx/nginx.conf` | NGINX config: consistent hashing, rate limiting (500r/s), retry on 502/503. |
| `loadtest/scheduler.js` | k6 load test: 30s ramp to 50 VUs, 1m at 200 VUs, 30s ramp down. |
| `tests/unit/test_security.py` | 15 unit tests: password hashing, JWT creation/validation, `alg: none` attack. |
| `tests/integration/test_auth.py` | 10 integration tests: register, login, JWT validation. |
| `tests/integration/test_doctors.py` | 6 integration tests: list doctors, create doctor (admin). |
| `tests/integration/test_patients.py` | 5 integration tests: list patients, profile. |
| `tests/integration/test_appointments.py` | 14 integration tests: booking success/conflict/validation, list, get by ID. |
| `tests/integration/test_concurrent_booking.py` | 1 integration test: concurrent same-slot booking (201 + 409). |
| `tests/integration/test_timezone.py` | 5 integration tests: Z suffix, UTC offset, naive datetime, invalid strings. |
| `tests/unit/test_circuit_breaker.py` | 8 unit tests: CLOSED→OPEN→HALF_OPEN→CLOSED state machine transitions. |
| `tests/integration/test_circuit_breaker.py` | 5 integration tests: health check with circuit breakers, breaker state validation. |
| `tests/integration/test_middleware.py` | 6 integration tests: MessagePack content negotiation, X-Response-Time header. |
| `tests/integration/test_chaos.py` | 2 integration tests: chaos backdoor (patient_id 999) returns 503. |
| `loadtest/scheduler.js` | k6 load test: read/write scenarios, 50-200 VUs, p95<500ms threshold. |
| `docker-compose.baseline.yml` | Override file for 1-worker baseline load testing. |
| `tests/conftest.py` | Pytest fixtures: HTTP client, admin/user tokens, auth headers, patient_id, future_time_slot. |

## Gotchas

### Appointment booking API (FR-1)
- Request body uses `patient_id` (int or string) and `time_slot` (ISO 8601 string), **not** `patient_name` and `appointment_time`.
- Success returns HTTP 201 with `{"success": true, "node_id": "<container_id>", "error": null, "appointment": {...}}`.
- Conflict returns HTTP 409 with `{"success": false, "error": "Slot already occupied by patient <name>", "appointment": {...}}`.
- `check_conflict()` in `app/db/repository.py` returns `Appointment | None` (not bool) so the error message includes who holds the slot.
- Node ID comes from `socket.gethostname()` — inside Docker this is the container ID.

### Chaos backdoor (FR-2)
- `patient_id == 999` (int or string) triggers an immediate HTTP 503 with `{"detail": "CHAOS: Simulated node failure"}`.
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
- `POST /api/v1/patients` creates or retrieves a patient by name/email (idempotent via `get_or_create_by_name`).
- Required before booking appointments — the booking endpoint validates patient existence.

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
```
