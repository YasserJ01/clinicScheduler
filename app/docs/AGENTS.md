# Clinic Scheduler — Agent Instructions

## Quick Start

```bash
docker compose up -d --build          # Start all services (nginx + 3 workers + postgres + redis)
docker compose down -v                # Tear down everything including DB volume
```

**Ports**: NGINX on `:80`, Postgres (primary) on `:5433`, Redis on `:6380` (host-mapped to avoid conflicts).

## Architecture

- **NGINX** (port 80) → consistent hashing LB → **3 FastAPI workers** (port 8000 each) → **Postgres primary** (`db:5432`) for writes + **Postgres replica** (`db-replica:5432`) for reads + **Redis**
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
| `app/core/security.py` | JWT creation, refresh tokens, bcrypt password hashing. |
| `app/core/metrics.py` | Async Redis-backed Prometheus metrics collector. |
| `app/core/metrics_middleware.py` | HTTP request tracking middleware (async). |
| `app/core/request_id_middleware.py` | X-Request-ID correlation header middleware. |
| `app/core/audit.py` | Audit logging helper (DB + stdout). |
| `app/core/tenant_middleware.py` | X-Tenant-ID header extraction for multi-tenant support. |
| `app/core/telemetry.py` | OpenTelemetry configuration (Jaeger exporter). |
| `app/core/webhooks.py` | Webhook delivery with HMAC-SHA256 signing and retry logic. |
| `app/core/email.py` | Email service abstraction (Null/SMTP/SendGrid). |
| `app/core/deprecation_middleware.py` | Deprecation headers for v1 API endpoints. |
| `nginx/nginx.conf` | NGINX config: round-robin, rate limiting (30r/s), retry on 502/503, SPA serving, security hardening. |
| `loadtest/scheduler.js` | k6 load test: 30s ramp to 50 VUs, 1m at 200 VUs, 30s ramp down. |
| `tests/unit/test_security.py` | 15 unit tests: password hashing, JWT creation/validation, `alg: none` attack. |
| `tests/integration/test_auth.py` | 10 integration tests: register, login, JWT validation. |
| `tests/integration/test_doctors.py` | 6 integration tests: list doctors, create doctor (admin). |
| `tests/integration/test_patients.py` | 6 integration tests: list patients, profile, real patient ID lookup. |
| `tests/integration/test_appointments.py` | 14 integration tests: booking success/conflict/validation, list, get by ID, status lifecycle. |
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

### NGINX Config Hardening (Phase 17-F)

#### Rate Limiting
- **30 requests/second per IP** (was 500 r/s — hardened from load testing default)
- Burst: 50 requests with `nodelay`
- Zone: 10 MB shared memory (`$binary_remote_addr` key)
- Exceeded requests return HTTP 429 (`limit_req_status 429`)

#### Connection Limiting
- **10 concurrent connections per IP**
- `limit_conn_zone $binary_remote_addr zone=conn_limit:10m`
- Exceeded connections return HTTP 429 (`limit_conn_status 429`)
- Applied to `/api/` location block

#### Load Balancing
- **Round-robin** via upstream block (was consistent hashing in TLS config)
- Non-TLS config: DNS-based round-robin via `resolver 127.0.0.11` + `set $backend`
- TLS config: `upstream clinic_backend` block (no `hash` directive = default round-robin)
- `proxy_next_upstream_tries 3` (was 2) with `proxy_next_upstream_timeout 5s`

#### Security Headers (all locations)
| Header | Value |
|---|---|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `SAMEORIGIN` |
| `X-XSS-Protection` | `0` (modern browsers) |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` |
| `Strict-Transport-Security` (TLS only) | `max-age=63072000; includeSubDomains; preload` |
| `Content-Security-Policy` (TLS only) | `default-src 'self'; script-src 'self'; ...` |

#### Information Disclosure Prevention
- `server_tokens off` — hides NGINX version from error pages and headers
- `proxy_hide_header X-Powered-By` — removes server info from upstream responses

#### Buffer Overflow Hardening
| Setting | Value |
|---|---|
| `client_body_buffer_size` | 128k |
| `client_max_body_size` | 1m |
| `client_header_buffer_size` | 1k |
| `large_client_header_buffers` | 4 8k |
| `output_buffers` | 32 32k |
| `postpone_output` | 1460 |

#### Timeouts
| Setting | Value |
|---|---|
| `client_header_timeout` | 15s |
| `client_body_timeout` | 15s |
| `send_timeout` | 10s |
| `proxy_connect_timeout` | 3s |
| `proxy_send_timeout` | 5s |
| `proxy_read_timeout` | 10s |

#### HTTP Method Restriction
- Only `GET`, `POST`, `HEAD`, `PATCH`, `PUT`, `DELETE`, `OPTIONS` allowed in `/api/`
- `OPTIONS` is required for CORS preflight — passed through to backend
- Dangerous methods (`TRACE`, `CONNECT`, etc.) blocked at NGINX level with HTTP 405 (HTML response, not passed to backend)

#### TLS Hardening (`nginx.conf.tls`)
- Protocols: TLSv1.2, TLSv1.3 only
- Ciphers: strong AEAD ciphers (GCM, ChaCha20) with forward secrecy
- `ssl_prefer_server_ciphers off` — client preference when possible
- `ssl_session_tickets off` — prevents session ticket reuse
- HSTS with `preload` directive

### k6 load test (updated Phase 17-G)
- k6 binary location: `C:\Program Files\k6\k6.exe` (not on PATH).
- k6 may OOM on Windows with 200 VUs; run on Linux for full-scale tests.

**Two configurations:**
1. **Production NGINX (30r/s)**: Use with `NGINX_RATE_LIMIT=30` env var. Test backend under real rate limits.
2. **Loadtest NGINX (500r/s)**: Use `docker compose -f docker-compose.yml -f docker-compose.loadtest.yml up -d` for NGINX with 500r/s rate limit, burst=200, conn_limit=50.

**Per-VU authentication**: Each VU registers its own user + creates its own patient to bypass per-user rate limits (100 req/60s). This is a well-known pattern for authenticated k6 tests with rate-limited APIs.

**Commands:**
```bash
# Load test with production NGINX (30r/s — backend under real limits)
& "C:\Program Files\k6\k6.exe" run loadtest/scheduler.js

# Load test with relaxed NGINX (500r/s — stress the backend)
docker compose -f docker-compose.yml -f docker-compose.loadtest.yml up -d
& "C:\Program Files\k6\k6.exe" run loadtest/scheduler.js

# Revert production NGINX after loadtest config
docker compose up -d nginx

# Custom VUs and duration (overrides script defaults)
& "C:\Program Files\k6\k6.exe" run loadtest/scheduler.js --vus 50 --duration 60s
```

### Read Replica (Phase 17-A)
- `db-replica` service in docker-compose.yml uses streaming replication from the primary `db`.
- `app/db/session.py` provides `get_read_db()` — used by all read-only GET endpoints.
- `READ_DATABASE_URL` env var defaults to `DATABASE_URL` if empty (single-DB dev mode).
- Replication user `replicator` is created by `scripts/init-replication.sh` mounted into `db`.
- Reminder scheduler also uses `get_read_db()` for its read queries.
- Replication lag: acceptable for list/analytics queries; write endpoints bypass the replica.

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
- **Note**: k6 may OOM on Windows with 200 VUs; use 50 VUs for local testing
- **NGINX rate limit**: 30 r/s per IP (hardened in Phase 17-F)
- **Loadtest NGINX override**: `docker compose -f docker-compose.yml -f docker-compose.loadtest.yml up -d` (500r/s, burst=200)
- **Per-VU auth**: Each VU registers its own user to bypass per-user rate limits (Phase 17-G update)
- **New files**: `nginx/nginx.conf.loadtest`, `docker-compose.loadtest.yml`
- **Phase 17-G results**:
  - 10 VUs: p95 booking 478ms, p95 doctors 447ms — thresholds met
  - 30 VUs: p95 booking 3.56s, p95 doctors 2.78s — backend saturated (DB pool limit: 20 connections)
  - Bottleneck: DB connection pool (15+5=20) limits concurrent request handling
  - Error rate: 5.98% (mostly from cancel-by-non-owner failures, not service errors)

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

### Secrets Management (Phase 17-B)

#### Kubernetes — External Secrets Operator
- Production K8s secrets are managed by [External Secrets Operator](https://external-secrets.io) with HashiCorp Vault.
- **ClusterSecretStore**: `k8s/cluster-secret-store.yaml` — points to Vault at `https://vault.cluster.internal:8200`, path `clinic-scheduler/production`, with Kubernetes auth.
- **ExternalSecret**: `k8s/external-secret.yaml` — syncs `SECRET_KEY`, `DB_PASSWORD`, `REDIS_PASSWORD`, `SENDGRID_API_KEY`, `SMTP_HOST`, `SMTP_PORT`, `FROM_EMAIL` from Vault.
- The old `k8s/secret.yaml` is kept as a fallback template for development and is annotated as deprecated.
- Apply: `kubectl apply -f k8s/cluster-secret-store.yaml -f k8s/external-secret.yaml`

#### Docker Compose — `.env` file
- Copy `.env.example` to `.env` and fill in values.
- `docker-compose.yml` uses `${VAR:-default}` syntax for optional vars.
- `docker-compose.prod.yml` uses `${VAR:?error}` syntax — fails fast if required vars are missing.
- `.env` is in `.gitignore`. Never commit it.

#### SECRET_KEY Rotation
- Generate new key: `python -c "import secrets; print(secrets.token_hex(32))"`
- Changing key invalidates all existing JWTs (users must re-authenticate).
- For zero-downtime rotation: implement dual-key validation with fallback period.

#### Full Secret Rotation Procedure
```bash
# 1. Update secret in Vault
vault kv put clinic-scheduler/production SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")

# 2. Wait for External Secrets Operator refresh (default 1h interval)
#    Or force immediate refresh:
kubectl annotate externalsecret clinic-scheduler-secrets force-sync=$(date +%s)

# 3. Verify new secret is propagated
kubectl get secret clinic-scheduler-secrets -o jsonpath='{.data.SECRET_KEY}' | base64 -d

# 4. Roll workers to pick up new secret
kubectl rollout restart deployment/clinic-worker-blue

# For Docker Compose:
# 1. Edit .env with new value
# 2. Rebuild and restart: docker compose up -d --build
```

### Blue-Green Deployment (Phase 17-C)

#### Architecture
- Two long-lived deployments: `clinic-worker-blue` (3 replicas) and `clinic-worker-green` (0 replicas).
- Main service `clinic-worker` selects `version: blue` — all production traffic hits blue.
- Green service `clinic-worker-green` selects `version: green` — used for smoke tests before cutover.
- Ingress points to `clinic-worker` service — no ingress changes during deploy.

#### Deploy Workflow (`.github/workflows/deploy-blue-green.yml`)
1. **Build & push** `clinic-scheduler-worker:green` to registry
2. **Scale green to 0** (clean state)
3. **Update green image** tag
4. **Scale green to 3** replicas
5. **Wait for rollout** (readiness probes, 180s timeout)
6. **Smoke tests** against `clinic-worker-green` service (health, docs, metrics)
7. **Switch** `clinic-worker` service selector to `version: green`
8. **Scale blue to 0**
9. On failure: green scaled to 0, blue unchanged

#### Rollback Workflow (`.github/workflows/rollback.yml`)
- Manual trigger with `confirm: rollback` input.
- Scales blue to 3, waits ready, switches service selector back to `version: blue`, scales green to 0.

#### Smoke Tests (`scripts/smoke-test.sh`)
- Validates: health endpoint (200), Swagger UI (200), metrics endpoint (200).
- Exits non-zero on any failure — prevents service cutover.

#### Manifests
| File | Purpose |
|---|---|
| `k8s/deployment-worker.yaml` | Blue deployment (`version: blue`, 3 replicas) |
| `k8s/deployment-green.yaml` | Green deployment (`version: green`, 0 replicas) |
| `k8s/service-worker.yaml` | Main service (selector: `version: blue` initially) |
| `k8s/service-worker-green.yaml` | Green service for pre-switch smoke tests |
| `.github/workflows/deploy-blue-green.yml` | Blue-green deploy automation |
| `.github/workflows/rollback.yml` | Emergency rollback to blue |
| `scripts/smoke-test.sh` | Health check suite for green validation |

#### Commands
```bash
# Manual deploy
kubectl set image deployment/clinic-worker-green -n clinic-scheduler worker=clinic-scheduler-worker:green
kubectl scale deployment/clinic-worker-green -n clinic-scheduler --replicas=3
kubectl rollout status deployment/clinic-worker-green -n clinic-scheduler --timeout=180s
./scripts/smoke-test.sh http://clinic-worker-green:8000
kubectl patch service clinic-worker -n clinic-scheduler \
  -p '{"spec":{"selector":{"app":"clinic-worker","version":"green"}}}'
kubectl scale deployment/clinic-worker-blue -n clinic-scheduler --replicas=0

# Rollback
kubectl scale deployment/clinic-worker-blue -n clinic-scheduler --replicas=3
kubectl rollout status deployment/clinic-worker-blue -n clinic-scheduler --timeout=180s
kubectl patch service clinic-worker -n clinic-scheduler \
  -p '{"spec":{"selector":{"app":"clinic-worker","version":"blue"}}}'
kubectl scale deployment/clinic-worker-green -n clinic-scheduler --replicas=0
```

### Alembic Migrations (Phase 6, updated Phase 7)
- Development: `init_db()` uses `create_all` (default).
- Production: set `ALEMBIC_ENABLED=true` to use Alembic.
- Generate migration: `alembic revision --autogenerate -m "description"`
- Apply migration: `alembic upgrade head`
- Migrations: `001_initial_schema`, `002_add_duration_minutes`, `003_fix_doctor_is_active_boolean`, `004_audit_log_indexes`

### SLA Monitoring & Error Budget (Phase 17-D)

#### SLO Definitions

| Metric | SLO Target | Error Budget (30d) |
|--------|-----------|-------------------|
| Availability (`GET /health` 200) | 99.9% | 43.2 minutes downtime |
| p95 Latency | < 500ms | 5% of requests may exceed |
| Booking Error Rate | < 1% HTTP 500 | 1% of booking attempts |
| Webhook Delivery Success | > 95% | 5% delivery failures acceptable |

#### Prometheus Recording Rules (`observability/prometheus-rules.yml`)
Pre-computed SLO metrics for dashboard and alert queries:
- `slo:availability:burn_rate_1h` — how fast error budget is consumed (× SLO)
- `slo:availability:error_budget_remaining_percent` — remaining budget
- `slo:latency:slow_requests_1h` — requests exceeding 500ms
- `slo:booking:error_requests_1h` — 5xx on POST /appointments
- `slo:webhook:failed_deliveries_1h` — undelivered webhooks

#### Alert Rules (`observability/alerts.yml`)
5 Grafana alert rules provisioned at startup:

| Alert | Severity | Condition |
|-------|----------|-----------|
| Availability SLO — Burn Rate Critical | critical | Burn rate > 2× for 5m AND budget < 50% |
| Availability SLO — Budget Exhausted | critical | Budget ≤ 0% |
| Latency SLO — p95 Approaching Threshold | warning | p95 > 400ms for 10m |
| Booking Error Rate SLO — Exceeded | critical | > 1% errors for 5m |
| Webhook Delivery SLO — Below Threshold | warning | < 95% success for 5m |

#### Grafana SLA Dashboard (`observability/grafana-dashboard-sla.json`)
Pre-provisioned dashboard with 8 panels:
- **SLO Overview** stat - aggregate health
- **Availability** (time series + gauge + burn rate) — 4 panels
- **Latency** time series with SLO threshold line
- **Booking Error Rate** time series
- **Webhook Success Rate** time series
- **Total Requests** time series (all + 5xx)

#### Observability Stack (`docker-compose.observability.yml`)
- **Prometheus** (`:9090`) scrapes `/api/v1/metrics` on workers
- **Loki** (`:3100`) — log aggregation
- **Promtail** — log shipping from Docker containers
- **Grafana** (`:3000`) — dashboards + alerting (admin/admin)
- Start: `docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d`

#### K8s ServiceMonitor
- `k8s/servicemonitor.yaml` — scrapes `/api/v1/metrics` on port `http` every 15s
- Compatible with Prometheus Operator

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

### Pagination (Phase 8)
- All list endpoints (`GET /doctors`, `GET /patients`, `GET /appointments`) return a paginated envelope.
- Response: `{"items": [...], "total": N, "page": 1, "page_size": 20, "pages": N}`
- Query params: `page` (default 1), `page_size` (default 20, max 100)
- Appointments: `doctor_id`, `patient_id`, `status`, `from_date`, `to_date`
- Patients: `search` (name ILIKE)
- Doctors: `specialty` (ILIKE)
- Existing tests updated to unwrap `data["items"]` instead of `data` directly.

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

### Appointment Status Lifecycle (Phase 8)
- `PATCH /api/v1/appointments/{id}/status` with body `{"status": "confirmed"}`.
- Allowed transitions: `scheduled → confirmed/cancelled`, `confirmed → completed/cancelled`.
- Patients can only cancel their own appointments. Doctors can confirm/complete/cancel. Admins can do anything.
- Invalid transitions return HTTP 409. Unauthorized access returns HTTP 403.
- Every status change creates an audit log entry.

### Doctor Deactivation (Phase 8)
- `PATCH /api/v1/doctors/{id}` with `{"is_active": false}` deactivates a doctor.
- Deactivated doctors are excluded from `GET /doctors` and cannot be booked.
- Booking a deactivated doctor returns HTTP 400 with `"Doctor not found or inactive"`.

### JWT Refresh Tokens (Phase 8)
- Access tokens now expire in 15 minutes (was 30).
- Login/register returns `{"access_token": "...", "refresh_token": "..."}`.
- `POST /api/v1/auth/refresh` exchanges a refresh token for a new access + refresh token pair.
- Refresh tokens rotate on each use (old one becomes invalid).
- `POST /api/v1/auth/logout` adds the access token to a Redis deny-list.
- Revoked tokens are rejected by `get_current_user` with HTTP 401.

### TLS Configuration (Phase 8)
- `nginx/nginx.conf.tls` provides HTTPS with HSTS and HTTP→HTTPS redirect.
- Dev certificates: `./scripts/generate_dev_certs.sh`
- Production: use Let's Encrypt / Certbot.

### Observability Stack (Phase 8)
- `docker-compose.observability.yml` adds Loki (3100), Promtail, Grafana (3000).
- Grafana default password: `admin` (set `GRAFANA_PASSWORD` to change).
- Promtail scrapes Docker container logs and forwards to Loki.

### PgBouncer (Phase 9)
- `edoburu/pgbouncer:latest` (v1.25.1) in transaction pooling mode
- Port mapping: `6432:5432` (host:container). Internal Docker network: `pgbouncer:5432`
- **asyncpg compatibility**: asyncpg uses SCRAM-SHA-256 by default, which is incompatible with PgBouncer transaction pooling. Workers connect directly to `db:5432` instead.
- PostgreSQL `pg_hba.conf` uses `md5` authentication (not `scram-sha-256`)
- Password encryption set to `md5` via `ALTER SYSTEM SET password_encryption = 'md5'`
- Reserved for synchronous clients (e.g., migration scripts, admin tools)

### Redis AOF (Phase 9)
- `appendonly yes` enabled for persistence
- JWT deny-list and metrics survive Redis restarts

### Kubernetes (Phase 9)
- 11 manifests in `k8s/` directory
- Apply: `kubectl apply -f k8s/`
- Dry-run: `kubectl apply -f k8s/ --dry-run=client`

### Disaster Recovery (Phase 9, updated Phase 17-E)

- Runbook: `app/docs/Disaster_Recovery_Runbook.md`

#### Scripts
| Script | Purpose |
|---|---|
| `scripts/backup.sh` | Standalone backup with integrity check, optional AES-256-CBC encryption, and 30-day retention |
| `scripts/restore.sh` | Restore from backup with decryption support; drops/recreates schema |
| `scripts/dr-test.sh` | Full DR drill: backup → teardown → restore → verify → measure RTO |

#### Usage
```bash
# Full DR test (requires running docker compose stack)
./scripts/dr-test.sh

# Manual backup
./scripts/backup.sh

# Manual backup with encryption
BACKUP_ENCRYPTION_KEY="your-key" ./scripts/backup.sh --encrypt

# Manual restore (WARNING: destroys existing data)
./scripts/restore.sh backups/clinic_scheduler_20260524_020000.sql.gz

# DR test without destroying volume (backup verification only)
./scripts/dr-test.sh --no-teardown
```

#### Kubernetes backup
- `kubectl apply -f k8s/cronjob-backup.yaml`
- Daily at 02:00 UTC, 30-day retention
- Includes integrity verification and optional AES-256-CBC encryption
- CronJob updated in Phase 17-E with encryption support

#### RTO Baseline
On the test dataset (~26k appointments):
- Backup: ~500ms
- Restore: ~1,200ms
- **Total: ~1.7 seconds**
- RTO scales linearly with dataset size (estimate 10 GB → ~5 minutes)

#### External Secrets
- `BACKUP_ENCRYPTION_KEY` added to `k8s/external-secret.yaml` (optional)

### Doctor Availability Windows (Phase 10)
- `DoctorSchedule` model: `doctor_schedules` table with `day_of_week` (0=Monday), `start_time`, `end_time`
- `GET /doctors/{id}/schedule` — list schedule windows
- `PUT /doctors/{id}/schedule` — replace entire schedule (admin only)
- `PATCH /doctors/{id}/schedule/{day}` — update single day (admin only)
- `DELETE /doctors/{id}/schedule/{day}` — remove a day (admin only)
- `GET /appointments/available` now uses doctor schedule if set, falls back to 08:00-17:00 default
- Response includes `schedule_based: true/false` to indicate which mode was used

### Email Notifications (Phase 10)
- `app/core/email.py` — abstract `EmailService` with `NullEmailService` (default), `SMTPEmailService`, `SendGridEmailService`
- Config: `EMAIL_PROVIDER` ("null"/"smtp"/"sendgrid"), `SMTP_HOST`, `SMTP_PORT`, `SENDGRID_API_KEY`, `FROM_EMAIL`
- Triggers: booking confirmation, cancellation, doctor confirmation
- Uses FastAPI `BackgroundTasks` for non-blocking delivery

### Appointment Notes (Phase 10)
- `PATCH /appointments/{id}/notes` with body `{"notes": "..."}`
- RBAC: doctor or admin only (patients get 403)
- Audit-logged on every change

### Recurring Appointments (Phase 10)
- `RecurringSeries` model links individual appointments to a series
- `POST /appointments/recurring` — creates N appointments with weekly/biweekly/monthly recurrence
- `DELETE /appointments/series/{series_id}` — cancels all remaining appointments in series
- Conflicts reported per occurrence; returns `{"created": [...], "conflicts": [...]}`
- Alembic migration `007` adds `series_id`, `next_reminder_at`, `reminder_sent` columns

### API Versioning (Phase 10)
- `app/api/v2/` package with updated routers
- v2 appointments: paginated response by default, includes `series_id`
- v2 doctors: list response includes `schedule` array
- `DeprecationMiddleware` adds `Deprecation`, `Sunset`, `Link` headers to all v1 responses
- Policy document: `app/docs/API_Versioning_Policy.md`

### NGINX Dynamic Resolution (Phase 10)
- `resolver 127.0.0.11 valid=5s` for Docker DNS
- `set $backend http://worker:8000` + `proxy_pass $backend` for dynamic upstream resolution
- Compatible with worker restarts and scaling

### Enhanced Load Testing (Phase 10)
- Mixed scenario: 70% reads, 20% bookings, 10% status updates
- Setup fails fast if auth or patient creation fails
- `bookings_per_second` custom metric with threshold

### Analytics Dashboard (Phase 11)
- `GET /api/v1/admin/analytics/summary` — aggregate stats with optional date range
- `GET /api/v1/admin/analytics/doctors/{id}/utilisation` — doctor utilisation rate
- `GET /api/v1/admin/analytics/peak-hours` — booking histogram by hour
- `GET /api/v1/admin/analytics/patients/{id}/history` — patient appointment history
- `GET /api/v1/admin/analytics/audit-log` — paginated, filterable audit log
- All endpoints require `admin` role

### Webhook Notifications (Phase 11)
- `Webhook` and `WebhookDelivery` models with HMAC-SHA256 signing
- `POST/GET/PATCH/DELETE /api/v1/admin/webhooks` — CRUD for webhook subscriptions
- `GET /api/v1/admin/webhooks/{id}/deliveries` — delivery history
- Retry policy: exponential backoff `[1, 5, 25]` seconds, max 3 retries
- Delivery headers: `X-Webhook-Signature`, `X-Webhook-Event`
- All webhook endpoints require `admin` role

### Patient Self-Service Portal (Phase 11)
- `frontend/index.html` — single-file SPA served by NGINX
- Features: login/register, view/cancel appointments, book new, browse doctors
- NGINX `location /` with `try_files $uri $uri/ /index.html`

### Doctor Mobile API (Phase 11)
- `GET /api/v1/doctors/{id}/appointments/today` — today's appointments
- `GET /api/v1/doctors/{id}/appointments/upcoming?days=7` — upcoming appointments
- `GET /api/v1/doctors/{id}/patients` — all patients the doctor has seen
- RBAC: doctor (own data only) or admin (any doctor)

### OpenTelemetry (Phase 11)
- `app/core/telemetry.py` — Jaeger exporter, FastAPI + SQLAlchemy instrumentation
- `jaeger` service in docker-compose (UI on `:16686`)
- Activate: `ENABLE_TELEMETRY=true docker compose up -d`

### CI/CD Pipeline Fix (Phase 11/12)
- `FATAL: role "root" does not exist` fixed by removing `docker compose` from CI
- CI now starts uvicorn directly: `nohup uvicorn app.main:app --host 0.0.0.0 --port 8000 &`
- `BASE_URL` environment variable support in `tests/conftest.py`

### Multi-Tenant Support (Phase 12)
- `Tenant` model: `id`, `name`, `slug`, `is_active`
- `tenant_id` column on ALL domain models (users, doctors, patients, appointments, etc.)
- `TenantMiddleware` extracts `X-Tenant-ID` header → `request.state.tenant_id`
- JWT tokens include `tenant_id` claim (set during register/login/refresh)
- `get_current_tenant` dependency validates header matches token tenant_id
- All repository methods accept `tenant_id` parameter for query filtering
- Tenant-scoped unique constraints: `(tenant_id, username)`, `(tenant_id, email)`
- Default tenant: `id=1, slug="default"` — all existing data backfilled
- **Security**: Tenant mismatch returns HTTP 403; all queries filtered by tenant_id

### FR‑PAT‑3 Compliance (SRS Gap Analysis)
**Requirement**: Booking endpoint returns 404 for missing patient; patients created only via explicit endpoints.

**Status**: COMPLIANT ✅
- `POST /appointments` (appointments.py:247-257) returns 404 with `{"success":false,"node_id":"...","error":"Patient with id N not found"}` when `patient_id` doesn't exist — verified by `test_book_appointment_invalid_patient`
- `POST /appointments/recurring` (appointments.py:383-386) also returns 404 for missing patient
- Patient records created only via:
  - `POST /patients` (explicit admin endpoint) ✅
  - `POST /auth/register` (user registration, lines 116-125) ✅
- Booking endpoints never create patient records as a side effect ✅
- `GET /patients/me` (patients.py:114-122) does auto-create a patient record for the authenticated user's convenience profile — minor deviation but doesn't affect booking ghost-record concern

### Rate Limiter Bug Fix (Phase 17-G Follow-up)
- `app/core/rate_limiter.py:113` returned `None` when `call_next()` raised an exception after `called_next` was set to `True`. Starlette's `BaseHTTPMiddleware` crashed with `TypeError: 'NoneType' object is not callable`.
- Fix: Added fallback `Response(500)` when `response is None` after exception handler.

### DB Replica Setup Fixes
- `scripts/init-replication.sh`: Added `pg_hba.conf` entry for replication user (missing after volume wipe)
- `docker-compose.yml` db-replica command: Changed `exec postgres` → `exec su-exec postgres postgres` (PostgreSQL refuses to run as root), added `chmod -R 0700 /var/lib/postgresql/data` (pg_basebackup as root creates files with wrong permissions)

## Dev Commands

```bash
# Rebuild workers only (no volume reset)
docker compose up -d --build

# Full reset (destroys DB data)
docker compose down -v && docker compose up -d --build

# Check worker logs
docker logs clinic-scheduler-worker-1

# Reload NGINX config without restart
docker compose exec nginx nginx -t && docker compose exec nginx nginx -s reload

# Verify NGINX security headers
curl -s -I http://localhost/api/v1/health | head -15

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

# Run load test with relaxed NGINX config (500r/s — stress the backend)
docker compose -f docker-compose.yml -f docker-compose.loadtest.yml up -d
& "C:\Program Files\k6\k6.exe" run loadtest/scheduler.js

# Revert NGINX to production config after load test
docker compose up -d nginx

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

# Update appointment status (requires admin/doctor/patient token)
curl -s -X PATCH -H "Authorization: Bearer <token>" -H "Content-Type: application/json" -d '{"status":"confirmed"}' http://localhost/api/v1/appointments/1/status

# Get doctor profile
curl -s -H "Authorization: Bearer <token>" http://localhost/api/v1/doctors/1

# Deactivate a doctor (admin only)
curl -s -X PATCH -H "Authorization: Bearer <admin_token>" -H "Content-Type: application/json" -d '{"is_active":false}' http://localhost/api/v1/doctors/1

# Get patient by ID (admin/doctor only)
curl -s -H "Authorization: Bearer <token>" http://localhost/api/v1/patients/1

# Update patient (admin only)
curl -s -X PATCH -H "Authorization: Bearer <admin_token>" -H "Content-Type: application/json" -d '{"name":"Jane Doe"}' http://localhost/api/v1/patients/1

# Refresh access token
curl -s -X POST -H "Content-Type: application/json" -d '{"refresh_token":"<refresh_token>"}' http://localhost/api/v1/auth/refresh

# Logout (revokes current access token)
curl -s -X POST -H "Authorization: Bearer <token>" http://localhost/api/v1/auth/logout

# Generate dev TLS certificates
./scripts/generate_dev_certs.sh

# Start observability stack (Loki + Grafana)
docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d

# Apply all Alembic migrations (including 005_refresh_tokens)
alembic upgrade head

# Run Phase 10 tests
python -m pytest tests/integration/test_phase10.py -v

# Run Phase 11 tests
python -m pytest tests/integration/test_phase11.py -v

# Run analytics endpoints (requires admin token)
curl -s -H "Authorization: Bearer <admin_token>" http://localhost/api/v1/admin/analytics/summary
curl -s -H "Authorization: Bearer <admin_token>" http://localhost/api/v1/admin/analytics/peak-hours

# Manage webhooks (requires admin token)
curl -s -X POST -H "Authorization: Bearer <admin_token>" -H "Content-Type: application/json" -d '{"url":"https://example.com/hook","events":["appointment.created"]}' http://localhost/api/v1/admin/webhooks
curl -s -H "Authorization: Bearer <admin_token>" http://localhost/api/v1/admin/webhooks
curl -s -H "Authorization: Bearer <admin_token>" http://localhost/api/v1/admin/webhooks/1/deliveries

# Access patient portal
open http://localhost

# Access Jaeger UI (when ENABLE_TELEMETRY=true)
open http://localhost:16686

# Multi-tenant: register with specific tenant
curl -s -X POST -H "Content-Type: application/json" -d '{"username":"user1","password":"test1234","tenant_id":2}' http://localhost/api/v1/auth/register

# Multi-tenant: set X-Tenant-ID header
curl -s -H "Authorization: Bearer <token>" -H "X-Tenant-ID: 2" http://localhost/api/v1/doctors

# View tenant data
docker compose exec db psql -U clinic -d clinic_db -c "SELECT id, name, slug FROM tenants;"
docker compose exec db psql -U clinic -d clinic_db -c "SELECT tenant_id, COUNT(*) FROM users GROUP BY tenant_id;"
```
