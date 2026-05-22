# Phase 11 Analytics, Webhooks & Patient Portal — Report

## Summary
Phase 11 delivers the admin dashboard analytics API, webhook notification system with HMAC-SHA256 signing, patient self-service portal (single-file SPA), doctor mobile API extensions, and OpenTelemetry instrumentation scaffolding. All 161 tests pass, ruff lint/format clean.

## Changes

### 1. Admin Dashboard Analytics

**New Router:** `app/api/v1/routers/analytics.py`

| Method | Path | Access | Description |
|--------|------|--------|-------------|
| `GET` | `/admin/analytics/summary` | Admin only | Aggregate stats (appointments, patients, doctors, cancellation rate) |
| `GET` | `/admin/analytics/doctors/{id}/utilisation` | Admin only | Doctor utilisation rate over a date range |
| `GET` | `/admin/analytics/peak-hours` | Admin only | Booking histogram by hour of day |
| `GET` | `/admin/analytics/patients/{id}/history` | Admin only | Full appointment history for a patient |
| `GET` | `/admin/analytics/audit-log` | Admin only | Paginated, filterable audit log entries |

**Query Parameters:**
- `/summary`: `from_date`, `to_date` (ISO 8601)
- `/utilisation`: `from_date`, `to_date` (defaults to last 30 days)
- `/peak-hours`: `days` (1-365, default 30)
- `/audit-log`: `page`, `page_size`, `actor`, `action`, `from_date`, `to_date`

**Response Example (`/summary`):**
```json
{
  "total_appointments": 1250,
  "total_patients": 340,
  "total_doctors": 12,
  "cancelled_appointments": 85,
  "cancellation_rate": 6.8,
  "avg_duration_minutes": 32.5,
  "period": {"from": null, "to": null}
}
```

### 2. Webhook Notifications

**New Models:** `Webhook`, `WebhookDelivery` in `app/models/__init__.py`
- `webhooks` table: `url`, `secret` (auto-generated), `events` (JSON array), `is_active`, `created_by`
- `webhook_deliveries` table: `webhook_id`, `event_type`, `payload`, `response_status`, `response_body`, `attempt`, `success`
- Cascade delete: deleting a webhook removes all its deliveries

**Alembic Migration:** `009_webhooks.py`

**New Module:** `app/core/webhooks.py`
- `sign_payload(secret, payload)` — HMAC-SHA256 signature (`sha256=<hex>`)
- `deliver_webhook(session, webhook, event_type, data)` — HTTP POST with retry logic
- `trigger_webhooks(session, event_type, data)` — dispatches to all matching active webhooks

**Retry Policy:**
- Exponential backoff: `[1, 5, 25]` second delays
- Maximum 3 retries (4 total attempts)
- Success: HTTP 2xx; anything else triggers retry

**HTTP Headers on Delivery:**
```
Content-Type: application/json
X-Webhook-Signature: sha256=<hmac_hex>
X-Webhook-Event: appointment.created
```

**Payload Format:**
```json
{
  "event": "appointment.created",
  "timestamp": "2026-05-22T10:30:00",
  "data": { ... }
}
```

**Admin CRUD Endpoints** in `app/api/v1/routers/admin.py`:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/admin/webhooks` | Create webhook (returns secret once) |
| `GET` | `/admin/webhooks` | List webhooks (paginated) |
| `GET` | `/admin/webhooks/{id}` | Get single webhook |
| `PATCH` | `/admin/webhooks/{id}` | Update webhook (url, events, is_active) |
| `DELETE` | `/admin/webhooks/{id}` | Delete webhook (cascades to deliveries) |
| `GET` | `/admin/webhooks/{id}/deliveries` | List delivery history (paginated) |

**Create Request:**
```json
{
  "url": "https://example.com/webhook",
  "events": ["appointment.created", "appointment.cancelled"],
  "is_active": true
}
```

### 3. Patient Self-Service Portal

**New File:** `frontend/index.html` — Single-file SPA (HTML + CSS + JS)

**Features:**
- Login / Register (creates patient profile automatically)
- View my appointments (with status badges)
- Cancel appointments (scheduled only)
- Book new appointment (doctor selection, date picker, time slot grid)
- Browse doctors (name + specialty cards)

**NGINX Integration:**
- Added `location /` with `try_files $uri $uri/ /index.html` for SPA routing
- Volume mount: `./frontend:/usr/share/nginx/html:ro`

**API Calls:** All requests go through `/api/v1/` with Bearer token authentication.

### 4. Doctor Mobile API Extensions

**New Endpoints** in `app/api/v1/routers/doctors.py`:

| Method | Path | Access | Description |
|--------|------|--------|-------------|
| `GET` | `/doctors/{id}/appointments/today` | Doctor/Admin | Today's appointments with patient names |
| `GET` | `/doctors/{id}/appointments/upcoming` | Doctor/Admin | Upcoming appointments (configurable days) |
| `GET` | `/doctors/{id}/patients` | Doctor/Admin | All patients the doctor has seen |

**Ownership Validation:**
- Doctors can only access their own appointments/patients (validated via `Doctor.user_id`)
- Admins can access any doctor's data
- Patients receive HTTP 403

**Response Example (`/appointments/today`):**
```json
{
  "doctor_id": 1,
  "date": "2026-05-22",
  "appointments": [
    {
      "id": 42,
      "patient_name": "Jane Doe",
      "appointment_time": "2026-05-22T09:00:00",
      "duration_minutes": 30,
      "status": "scheduled",
      "notes": "Patient has peanut allergy"
    }
  ]
}
```

### 5. OpenTelemetry Instrumentation

**New Module:** `app/core/telemetry.py`
- Jaeger Thrift exporter (`jaeger:6831`)
- FastAPI auto-instrumentation via `FastAPIInstrumentor`
- SQLAlchemy instrumentation via `SQLAlchemyInstrumentor`
- Conditional activation via `ENABLE_TELEMETRY=true` env var

**New Service:** `jaeger` in `docker-compose.yml`
- Image: `jaegertracing/all-in-one:latest`
- Ports: `16686` (UI), `6831/udp` (agent), `14268` (collector)
- Jaeger UI: `http://localhost:16686`

**Activation:**
```bash
ENABLE_TELEMETRY=true docker compose up -d
```

### 6. CI/CD Pipeline Fix

**Problem:** `FATAL: role "root" does not exist` in GitHub Actions
- Root cause: `services` block (postgres/redis) conflicted with `docker compose up -d`
- GitHub Actions runner runs as `root`; docker compose health checks used default OS user

**Fix** in `.github/workflows/ci.yml`:
- Removed `docker compose build` and `docker compose up -d` steps
- Start uvicorn directly: `nohup uvicorn app.main:app --host 0.0.0.0 --port 8000 &`
- Added `BASE_URL` environment variable for test configuration
- Updated `tests/conftest.py` to read `BASE_URL` from environment (default: `http://localhost`)
- Redis service now uses `--requirepass redispass` matching dev configuration

## Test Results
- **Unit tests:** 40 passed
- **Integration tests:** 121 passed (17 new Phase 11 tests)
- **Total:** 161 passed, 5 skipped
- **Ruff:** All checks passed, all files formatted

## Infrastructure Health
```
NAME                           STATUS
clinic-scheduler-db-1          Up (healthy)
clinic-scheduler-nginx-1       Up
clinic-scheduler-pgbouncer-1   Up
clinic-scheduler-redis-1       Up (healthy)
clinic-scheduler-jaeger-1      Up (new)
clinic-scheduler-worker-1      Up (healthy)
clinic-scheduler-worker-2      Up (healthy)
clinic-scheduler-worker-3      Up (healthy)
```

## New Files
| Path | Purpose |
|------|---------|
| `alembic/versions/009_webhooks.py` | Migration for webhooks + webhook_deliveries tables |
| `app/api/v1/routers/analytics.py` | Admin analytics endpoints |
| `app/core/telemetry.py` | OpenTelemetry configuration |
| `app/core/webhooks.py` | Webhook delivery with HMAC signing and retries |
| `frontend/index.html` | Patient self-service portal SPA |
| `tests/integration/test_phase11.py` | 17 integration tests for Phase 11 features |

## Modified Files
| Path | Changes |
|------|---------|
| `app/models/__init__.py` | Added `Webhook`, `WebhookDelivery` models |
| `app/api/v1/routers/admin.py` | Added webhook CRUD endpoints |
| `app/api/v1/routers/doctors.py` | Added mobile API extensions (today, upcoming, patients) |
| `app/db/repository.py` | Added `get_today_appointments`, `get_upcoming_appointments`, `get_patients_for_doctor` |
| `app/main.py` | Wired analytics router, conditional telemetry init |
| `docker-compose.yml` | Added jaeger service, frontend volume mount |
| `nginx/nginx.conf` | Added SPA serving with `try_files` |
| `.github/workflows/ci.yml` | Fixed postgres role error, use uvicorn directly |
| `tests/conftest.py` | Added `BASE_URL` environment variable support |
| `app/docs/AGENTS.md` | Added Phase 11 documentation sections |

## Next Steps (Phase 12)
- Multi-Tenant Support (`tenant_id` on all entities)
- Tenant-scoped data isolation
- Tenant resolution via header + JWT claim
- Tenant management admin endpoints
