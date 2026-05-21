# Clinic Scheduler — Engineering Roadmap
## What's Next: Enhancements, Bug Fixes, and Future Phases

| Field | Value |
|---|---|
| Document Version | 1.0.0 |
| Status | Active — Engineering Reference |
| Prepared By | Senior Engineering Lead |
| Date | 2026-05-21 |
| Baseline | Phases 0–6 Complete · 116 Tests Passing |
| Classification | Internal / Technical |

---

## Executive Summary

All six planned phases have been delivered. The system is functionally complete for its original scope and is passing 116 automated tests. However, a thorough cross-examination of the codebase, phase reports, SRS, and AGENTS.md reveals a clear set of **critical defects**, **SRS requirements that were never implemented**, **performance bottlenecks**, and **production-readiness gaps** that must be addressed before the service can be considered genuinely production-grade.

This document organises all outstanding work into five structured phases (7–11), ordered by business criticality. Each phase is self-contained, independently deployable, and gated by measurable acceptance criteria.

**Priority summary:**

| Phase | Title | Priority | Effort |
|---|---|---|---|
| 7 | Bug Fixes and Critical Defects | 🔴 Critical | ~6 days |
| 8 | SRS Completeness — Missing Endpoints and Features | 🟠 High | ~12 days |
| 9 | Infrastructure Hardening and Production Reliability | 🟠 High | ~10 days |
| 10 | Advanced Feature Delivery | 🟡 Medium | ~18 days |
| 11 | Analytics, Client Portal, and Ecosystem Integrations | 🟢 Low–Medium | ~20 days |

---

## Table of Contents

1. [Current State Assessment](#1-current-state-assessment)
2. [Phase 7 — Bug Fixes and Critical Defects](#2-phase-7--bug-fixes-and-critical-defects)
3. [Phase 8 — SRS Completeness](#3-phase-8--srs-completeness)
4. [Phase 9 — Infrastructure Hardening and Production Reliability](#4-phase-9--infrastructure-hardening-and-production-reliability)
5. [Phase 10 — Advanced Feature Delivery](#5-phase-10--advanced-feature-delivery)
6. [Phase 11 — Analytics, Client Portal, and Ecosystem Integrations](#6-phase-11--analytics-client-portal-and-ecosystem-integrations)
7. [Cross-Phase Architecture Decisions](#7-cross-phase-architecture-decisions)
8. [Dependency and Risk Register](#8-dependency-and-risk-register)
9. [Definition of Done (Updated)](#9-definition-of-done-updated)
10. [Consolidated Milestone Timeline](#10-consolidated-milestone-timeline)

---

## 1. Current State Assessment

### 1.1 What Is Working

| Area | Status | Evidence |
|---|---|---|
| Authentication (register, login, JWT, RBAC) | ✅ Complete | 10 integration tests pass |
| Appointment booking (basic) | ✅ Complete | 14 integration tests pass |
| Conflict detection (exact-time) | ✅ Complete | Partial unique index + IntegrityError handling |
| Duration-aware conflict detection | ✅ Complete | Range overlap logic, 8 integration tests |
| Chaos engineering backdoor | ✅ Complete | 2 integration tests pass |
| Circuit breaker (CLOSED/OPEN/HALF_OPEN) | ✅ Complete | 8 unit tests pass |
| MessagePack middleware + X-Response-Time | ✅ Complete | 6 integration tests pass |
| Health check endpoint | ✅ Complete | DB + Redis probed |
| GDPR export + anonymisation | ✅ Complete | 7 integration tests pass |
| Audit logging (DB + stdout) | ✅ Complete | Used on create_appointment, anonymise |
| Prometheus metrics (Redis-backed) | ✅ Complete | 4 integration tests pass |
| Alembic migrations (2 revisions) | ✅ Complete | 001 initial schema, 002 duration |
| CI/CD pipeline (GitHub Actions) | ✅ Complete | lint, security, unit, integration |
| Available slots endpoint | ✅ Complete | Range-aware, 08:00–17:00 window |

### 1.2 Confirmed Bugs and Defects

These are **verified issues in the current production codebase**. They require fixes before any new feature work.

| ID | Severity | Location | Description |
|---|---|---|---|
| BUG-01 | 🔴 Critical | `app/core/metrics.py:12` | `MetricsCollector` uses **synchronous** `redis.from_url()` inside an async FastAPI app. Every metric write blocks the event loop, degrading throughput under load. |
| BUG-02 | 🔴 Critical | `app/db/repository.py:check_conflict()` | SQL query uses `WHERE appointment_time < end_time` but has **no lower-bound filter** (`appointment_time >= naive_time - max_duration`). On large tables this performs a near-full table scan per booking request. |
| BUG-03 | 🟠 High | `app/api/v1/routers/patients.py:get_my_profile()` | Returns `id: 0` hardcoded. No lookup against the `patients` table using the authenticated user's username. The endpoint is functionally broken for any patient-facing use. |
| BUG-04 | 🟠 High | `app/db/repository.py:get_or_create_by_name()` | Patient lookup uses `WHERE name = :name` as the unique key. Email is the unique-indexed column. Two patients with the same name but different emails will collide or retrieve the wrong record. Should be `WHERE email = :email`. |
| BUG-05 | 🟠 High | `app/db/session.py:seed_data()` / `init_db()` | When `ALEMBIC_ENABLED=true`, `init_db()` calls Alembic migrations but `seed_data()` still runs and checks `COUNT(*) FROM doctors`. If Alembic ran on a fresh DB, the table exists but is empty, so seeds are inserted correctly — but if Alembic is re-run on an existing DB (rolling deployment), the check is idempotent. **The risk** is that `seed_data()` is never called after Alembic mode is enabled because `init_db()` path skips `create_all`, and `seed_data()` runs in `lifespan` independently. This is actually fine, but the two code paths are not tested together and the interaction is undocumented. |
| BUG-06 | 🟡 Medium | `app/models/__init__.py:Doctor.is_active` | `is_active` is stored as `VARCHAR(10)` with values `"true"` and `"false"`. The `DoctorRepository.list_all()` filters on `Doctor.is_active == "true"`. This is a string comparison anti-pattern. A `BOOLEAN` column with `WHERE is_active = TRUE` is type-safe, index-friendly, and immune to casing bugs. |
| BUG-07 | 🟡 Medium | `app/models/__init__.py:AuditLog` | `audit_log` table has no indexes on `created_at`, `actor`, `entity_type`, or `entity_id`. Any query for audit history (e.g., "show all actions by user X") will be a full sequential scan. |
| BUG-08 | 🟡 Medium | `app/core/audit.py:audit_log()` | The `log_msg` is constructed as a tuple `(format_string, *args)` and then passed as `logger.info(*log_msg)`. This is correct Python logging API usage, but the `log_msg` variable is named confusingly and the pattern is not consistent with other loggers in the codebase. Minor but creates maintenance confusion. |
| BUG-09 | 🟡 Medium | `nginx/nginx.conf` | The `worker:8000` upstream entry is a single line — Docker Compose's internal DNS for `worker` resolves to all replica IPs, but NGINX's `hash $request_uri consistent` with a single named server entry may not properly distribute across all 3 replicas. Should use `resolver 127.0.0.11` and dynamic resolution or explicit replica entries. |
| BUG-10 | 🟢 Low | `loadtest/scheduler.js` | The k6 `setup()` function returns a `token` and `patientId`, but if `createPatient()` returns `null` (e.g., auth failure during setup), the default function silently returns early without recording an error against the test metrics. A null `patientId` should fail the setup and abort the test. |

### 1.3 SRS Requirements Never Implemented

Requirements explicitly stated in the SRS that have no corresponding code:

| FR ID | Requirement | Status |
|---|---|---|
| FR-APT-9 | Appointment status lifecycle (`PATCH /appointments/{id}/status`) | ❌ Not implemented |
| FR-APT-10 | Appointment cancellation by patient (own) or admin (any) | ❌ Not implemented |
| FR-DOC-3 | Doctor activation / deactivation (admin-only) | ❌ Not implemented |
| FR-DOC-4 | `GET /doctors/{id}` — doctor profile with schedule | ❌ Not implemented |
| FR-PAT-4 | Full patient CRUD (create, update, deactivate) for admin | ❌ Partial — only create exists |
| NFR-SEC-8 | TLS — all production traffic encrypted | ❌ Documented only, not implemented |
| NFR-OBS-3 | Centralised log aggregation | ❌ Not implemented |

---

## 2. Phase 7 — Bug Fixes and Critical Defects

### 2.1 Objectives

Resolve all confirmed bugs before they reach production users. This phase is non-negotiable — no new features should ship until every item below is fixed and tested. Estimated effort: **~6 days**.

### 2.2 Fix Catalogue

---

#### FIX-01: Replace Synchronous Redis with Async Redis in MetricsCollector

**File:** `app/core/metrics.py`

**Problem:** `redis.from_url()` is the synchronous Redis client. Calling `.incr()`, `.set()`, and `.keys()` on it inside an async FastAPI request blocks the entire Uvicorn event loop thread for the duration of the Redis RTT (typically 0.5–2ms, but up to 50ms+ under load). With 200 concurrent VUs, this creates a serialisation bottleneck that directly contradicts the p95 < 500ms NFR.

**Fix:**

```python
# app/core/metrics.py
import redis.asyncio as aioredis

class MetricsCollector:
    def __init__(self, redis_url: str = settings.REDIS_URL):
        self.redis = aioredis.from_url(redis_url, decode_responses=True)
        self._prefix = "clinic_metrics"

    async def increment_request(self, method: str, endpoint: str, status: int) -> None:
        key = self._key("http_requests_total", method, endpoint, str(status))
        await self.redis.incr(key)

    async def observe_duration(self, method: str, endpoint: str, duration: float) -> None:
        key = self._key("http_request_duration_sum", method, endpoint)
        await self.redis.incrbyfloat(key, duration)
        count_key = self._key("http_request_duration_count", method, endpoint)
        await self.redis.incr(count_key)
        bucket = self._duration_bucket(duration)
        bucket_key = self._key("http_request_duration_bucket", method, endpoint, str(bucket))
        await self.redis.incr(bucket_key)

    async def get_all_metrics(self) -> str:
        # All redis.keys() and redis.get() calls become await
        ...
```

`MetricsMiddleware.dispatch()` must then `await` all metric calls. The `get_metrics()` router handler must also be `async def` and `await metrics_collector.get_all_metrics()`.

**Tests to add:** `tests/unit/test_metrics_async.py` — mock `aioredis` and verify all methods are awaited; assert no sync Redis calls exist in the module.

---

#### FIX-02: Add Lower-Bound Filter to `check_conflict()`

**File:** `app/db/repository.py`

**Problem:** The current query:
```python
select(Appointment).where(
    Appointment.doctor_id == doctor_id,
    Appointment.appointment_time < end_time,   # upper bound only
    Appointment.status != AppointmentStatus.CANCELLED,
)
```
fetches **every appointment from the beginning of time** up to `end_time`. With 100k+ appointments, this returns thousands of rows into Python memory for filtering. The Python loop then checks the range overlap — this is O(N) where N is the entire appointment history for that doctor.

**Fix:** Add the lower-bound SQL condition. The earliest an existing appointment could overlap `[naive_time, end_time)` is one where `existing_start > naive_time - max_duration`. Using the maximum possible appointment duration (480 minutes) as a conservative bound:

```python
async def check_conflict(
    self, doctor_id: int, appointment_time: datetime, duration_minutes: int = 30
) -> Appointment | None:
    naive_time = appointment_time.replace(tzinfo=None) if appointment_time.tzinfo else appointment_time
    end_time = naive_time + timedelta(minutes=duration_minutes)
    # Lower bound: no appointment starting before (naive_time - 480 min) can overlap
    lower_bound = naive_time - timedelta(minutes=480)

    result = await self.session.execute(
        select(Appointment).where(
            Appointment.doctor_id == doctor_id,
            Appointment.appointment_time >= lower_bound,   # <-- NEW
            Appointment.appointment_time < end_time,
            Appointment.status != AppointmentStatus.CANCELLED,
        )
    )
    appointments = result.scalars().all()
    for appt in appointments:
        appt_end = appt.appointment_time + timedelta(minutes=appt.duration_minutes)
        if naive_time < appt_end:
            return appt
    return None
```

**Alembic migration:** No schema change needed. The existing `ix_appointments_doctor_id` index will still be used; the composite filter will be efficient.

**Tests to add:** `tests/unit/test_conflict_query.py` — mock the session and verify the SQL includes both upper and lower bounds.

---

#### FIX-03: Fix `GET /patients/me` to Return Real Patient Record

**File:** `app/api/v1/routers/patients.py`

**Problem:** Returns `{"id": 0, "name": current_user["user_id"], "email": "...@clinic.com"}` — all hardcoded, no DB lookup. The `id: 0` is a sentinel value that will break any client trying to use the returned ID to book an appointment.

**Fix:** Look up the patient by the user's username, then by email if not found:

```python
@router.get("/me", response_model=PatientResponse)
async def get_my_profile(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    username = current_user["user_id"]
    patient_repo = PatientRepository(db)
    # Try to find patient by email convention first
    result = await db.execute(
        select(Patient).where(
            Patient.email == f"{username}@clinic.com"
        )
    )
    patient = result.scalar_one_or_none()
    if patient:
        return {"id": patient.id, "name": patient.name, "email": patient.email}
    # Return profile shell if no patient record linked yet
    return {"id": 0, "name": username, "email": f"{username}@clinic.com"}
```

A cleaner long-term fix (Phase 8) is to add a `user_id` FK to the `patients` table. Document this as a Phase 8 task.

**Tests to add:** Update `test_patients.py::TestPatientProfile::test_get_my_profile_returns_username` to assert that `id` is not 0 when a matching patient exists.

---

#### FIX-04: Fix Patient Lookup Key in `get_or_create_by_name()`

**File:** `app/db/repository.py`

**Problem:** `get_or_create_by_name()` queries `WHERE Patient.name == name`. Name is not unique. Two patients named "John Smith" will return the first one found, regardless of email.

**Fix:** Rename and fix the method to look up by email (the unique-indexed column):

```python
async def get_or_create_by_email(self, name: str, email: str) -> Patient:
    result = await self.session.execute(
        select(Patient).where(Patient.email == email)   # use the unique column
    )
    patient = result.scalar_one_or_none()
    if not patient:
        patient = Patient(name=name, email=email)
        self.session.add(patient)
        await self.session.flush()
    return patient
```

Update `app/api/v1/routers/patients.py` to call `get_or_create_by_email()` instead of `get_or_create_by_name()`.

**Alembic migration:** None needed (email is already unique-indexed).

**Tests to add:** `tests/unit/test_patient_repository.py` — test that two patients with the same name but different emails are created as separate records.

---

#### FIX-05: Fix Doctor `is_active` Column Type

**File:** `app/models/__init__.py`, `app/db/repository.py`

**Problem:** `is_active = Column(String(10), ..., default="true")` is a string comparison anti-pattern. The filter `Doctor.is_active == "true"` is fragile — `"True"`, `"TRUE"`, or a stray `" true"` would silently break filtering.

**Fix:** Migrate to a proper Boolean column.

Alembic migration `003_fix_doctor_is_active_boolean.py`:
```python
def upgrade():
    op.execute("ALTER TABLE doctors ADD COLUMN is_active_bool BOOLEAN NOT NULL DEFAULT TRUE")
    op.execute("UPDATE doctors SET is_active_bool = (is_active = 'true')")
    op.execute("ALTER TABLE doctors DROP COLUMN is_active")
    op.execute("ALTER TABLE doctors RENAME COLUMN is_active_bool TO is_active")
```

Update model:
```python
is_active = Column(Boolean, nullable=False, default=True)
```

Update repository:
```python
select(Doctor).where(Doctor.is_active == True)
```

**Tests to add:** Verify that deactivated doctors (is_active=False) are excluded from `GET /doctors`.

---

#### FIX-06: Add Indexes to `audit_log` Table

**File:** New Alembic migration `004_audit_log_indexes.py`

**Problem:** `audit_log` has no indexes. Any admin query for audit entries by actor, entity, or time range scans the entire table.

```python
def upgrade():
    op.create_index('ix_audit_log_actor', 'audit_log', ['actor'])
    op.create_index('ix_audit_log_entity', 'audit_log', ['entity_type', 'entity_id'])
    op.create_index('ix_audit_log_created_at', 'audit_log', ['created_at'])
```

---

#### FIX-07: Add `CHAOS_ENABLED` Environment Toggle

**File:** `app/config.py`, `app/api/v1/routers/appointments.py`

**Problem:** The chaos backdoor (`patient_id=999`) is always active. The Phase 5 security review explicitly flagged this as a residual risk. In production, this is an undocumented attack surface — any caller who discovers `patient_id=999` can trigger 503 errors at will.

**Fix:**

```python
# app/config.py
CHAOS_ENABLED: bool = False   # must be explicitly opted into
```

```python
# app/api/v1/routers/appointments.py
if patient_id_str == "999" and settings.CHAOS_ENABLED:
    logger.error("CHAOS: ...")
    raise HTTPException(status_code=503, detail="CHAOS: Simulated node failure")
```

`docker-compose.yml` development environment: `CHAOS_ENABLED=true`
`docker-compose.prod.yml`: omit (defaults to False)

**Tests:** Update `test_chaos.py` to set `CHAOS_ENABLED=true` in the test environment. Add a test verifying that `patient_id=999` with `CHAOS_ENABLED=false` proceeds normally through the booking flow.

---

#### FIX-08: Add `X-Request-ID` Correlation Header Middleware

**File:** New `app/core/request_id_middleware.py`

**Problem:** There is no correlation ID across a distributed request. When a booking fails on one of three workers, you cannot trace the error back to a specific log line across NGINX, the worker, and the database without an external correlation ID. This is a standard observability requirement.

**Fix:**

```python
import uuid
from starlette.middleware.base import BaseHTTPMiddleware

class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
```

Wire in `create_app()`. Log `X-Request-ID` in all audit log entries and exception handlers. NGINX should forward the header via `proxy_set_header X-Request-ID $http_x_request_id`.

### 2.3 Phase 7 Acceptance Criteria

| Criterion | Verification |
|---|---|
| All list endpoints return correct results after `is_active` fix | Integration tests on active/inactive doctors |
| `GET /patients/me` returns real patient data with correct `id` | Updated integration test |
| `POST /patients` with duplicate email returns existing record | Unit test for `get_or_create_by_email` |
| `check_conflict()` query includes lower-bound time filter | Unit test mocking SQLAlchemy session |
| Metrics middleware uses async Redis — no sync blocking | Async unit test with mocked aioredis |
| `patient_id=999` with `CHAOS_ENABLED=false` books normally | Integration test |
| `patient_id=999` with `CHAOS_ENABLED=true` returns 503 | Integration test |
| `X-Request-ID` header present on all responses | Middleware integration test |
| Alembic migrations 003 and 004 run cleanly | `alembic upgrade head` from revision 002 |
| All 116 existing tests still pass | Full test suite run |

### 2.4 Estimated Effort

| Task | Estimate |
|---|---|
| FIX-01: Async Redis in metrics | 1 day |
| FIX-02: check_conflict lower bound | 0.5 day |
| FIX-03: patients/me fix | 0.5 day |
| FIX-04: get_or_create_by_email | 0.5 day |
| FIX-05: is_active Boolean migration | 1 day |
| FIX-06: audit_log indexes migration | 0.5 day |
| FIX-07: CHAOS_ENABLED toggle | 0.5 day |
| FIX-08: X-Request-ID middleware | 0.5 day |
| Tests and regression verification | 1 day |
| **Phase 7 Total** | **~6 days** |

---

## 3. Phase 8 — SRS Completeness

### 3.1 Objectives

Implement every requirement explicitly stated in the SRS that has not yet been delivered. This phase brings the system to full SRS compliance. Estimated effort: **~12 days**.

### 3.2 Feature: Appointment Status Lifecycle and Cancellation (FR-APT-9, FR-APT-10)

**New endpoint:** `PATCH /api/v1/appointments/{id}/status`

**Request:**
```json
{ "status": "confirmed" }
```

**Business rules:**

| Caller Role | Allowed Transitions |
|---|---|
| `patient` | `scheduled → cancelled` (own appointments only) |
| `doctor` | `scheduled → confirmed`, `confirmed → completed`, `confirmed → cancelled` |
| `admin` | Any transition on any appointment |

**Implementation notes:**
- Add `PATCH /appointments/{id}/status` route in `appointments.py`
- Add `AppointmentRepository.update_status(id, new_status, actor_id, actor_role)`
- Validate ownership: `patient` role can only cancel their own (`appointment.patient_id == current_user patient record`)
- Create audit log entry for every status change
- `status` must be a valid `AppointmentStatus` enum value
- HTTP 409 if the transition is invalid (e.g., `completed → scheduled`)
- HTTP 403 if a patient tries to update status on another patient's appointment

**Alembic migration:** None needed — status column and enum values already exist.

**Tests:**

| Test | Scenario |
|---|---|
| `test_patient_can_cancel_own_appointment` | Patient cancels → 200 |
| `test_patient_cannot_cancel_others_appointment` | Patient cancels wrong appointment → 403 |
| `test_doctor_can_confirm_appointment` | Doctor confirms → 200 |
| `test_admin_can_complete_appointment` | Admin sets completed → 200 |
| `test_invalid_status_transition_rejected` | `completed → scheduled` → 409 |
| `test_cancelled_appointment_excluded_from_conflict` | Cancel, then rebook same slot → 201 |

---

### 3.3 Feature: Doctor Profile and Deactivation (FR-DOC-3, FR-DOC-4)

**New endpoints:**

```
GET    /api/v1/doctors/{id}          — any authenticated user
PATCH  /api/v1/doctors/{id}          — admin only (update name, specialty, is_active)
```

**`GET /doctors/{id}` response:**
```json
{
  "id": 1,
  "name": "Dr. Smith",
  "specialty": "Cardiology",
  "is_active": true,
  "appointments_today": 3,
  "upcoming_appointments": 12
}
```

**`PATCH /doctors/{id}` request:**
```json
{
  "name": "Dr. Smith Jr.",
  "specialty": "Interventional Cardiology",
  "is_active": false
}
```

**Business rules:**
- Deactivating a doctor (`is_active: false`) does not cancel their existing appointments. Admins must separately cancel or reassign those appointments.
- A deactivated doctor's `id` can still be referenced in existing appointments (referential integrity).
- Deactivated doctors SHALL be excluded from `GET /doctors` and from booking validation (FR-APT-3 must check `doctor.is_active`).

**`FR-APT-3` fix:** The current `appointments.py` checks `if not doctor` but does NOT check `doctor.is_active`. Add:
```python
if not doctor or not doctor.is_active:
    return JSONResponse(status_code=400, content=BookingResponse(
        success=False, node_id=NODE_ID, error="Doctor not found or inactive"
    ).model_dump())
```

**Tests:** 8 tests covering: get by valid ID, get by invalid ID, deactivate doctor, deactivated doctor excluded from list, deactivated doctor not bookable, update name/specialty, unauthenticated access rejected, non-admin update rejected.

---

### 3.4 Feature: Full Patient CRUD for Admin (FR-PAT-4)

**New endpoints:**

```
GET    /api/v1/patients/{id}         — admin or doctor
PATCH  /api/v1/patients/{id}         — admin only
```

**`PATCH /patients/{id}` request:**
```json
{
  "name": "Jane Doe-Smith",
  "email": "jane.doesmith@example.com",
  "phone": "+31612345678"
}
```

**Business rules:**
- Updating email must not conflict with another patient's email (unique constraint enforced by DB; catch `IntegrityError`).
- Updating a patient creates an audit log entry.
- A patient cannot update their own record via this endpoint (use a future self-service endpoint).

**Repository additions:**

```python
async def update(self, patient_id: int, **fields) -> Patient | None:
    patient = await self.get_by_id(patient_id)
    if not patient:
        return None
    for field, value in fields.items():
        if value is not None:
            setattr(patient, field, value)
    await self.session.flush()
    return patient
```

---

### 3.5 Feature: JWT Refresh Tokens

**Problem:** JWTs expire after 30 minutes. Users must re-authenticate. For long-running client sessions this is disruptive. The current token has no refresh mechanism.

**New endpoints:**

```
POST /api/v1/auth/refresh     — exchange a valid (non-expired) refresh token for a new access token
POST /api/v1/auth/logout      — add access token to Redis deny-list (implements token revocation)
```

**Implementation:**

- Issue a **short-lived access token** (15 minutes) and a **long-lived refresh token** (7 days) on login/register
- Refresh token stored in the `users` table as a hashed value (`refresh_token_hash`, `refresh_token_expires_at` columns — new Alembic migration)
- `POST /auth/refresh` validates the refresh token against the stored hash, issues a new access token
- `POST /auth/logout` adds the current access token's `jti` (JWT ID claim) to a Redis set with TTL equal to the token's remaining lifetime — all subsequent requests check this deny-list in `get_current_user`

**Security notes:**
- Refresh tokens should only be transmitted over HTTPS
- Refresh tokens should be rotated on each use (issue a new refresh token and invalidate the old one)
- Refresh token hash stored in DB, raw value only ever transmitted to client

**Alembic migration `005_refresh_tokens.py`:**
```python
op.add_column('users', sa.Column('refresh_token_hash', sa.String(255), nullable=True))
op.add_column('users', sa.Column('refresh_token_expires_at', sa.DateTime(), nullable=True))
```

---

### 3.6 Feature: Pagination and Filtering on List Endpoints

**Problem:** `GET /appointments`, `GET /patients`, `GET /doctors` return the entire table with no pagination. At scale (tens of thousands of records), this causes unbounded memory consumption, long response times, and excessive data transfer.

**Query parameters (all list endpoints):**

```
GET /api/v1/appointments?page=1&page_size=20&doctor_id=1&patient_id=5&status=scheduled&from=2026-06-01&to=2026-06-30
GET /api/v1/patients?page=1&page_size=50&search=Jane
GET /api/v1/doctors?page=1&page_size=20&specialty=Cardiology
```

**Response envelope:**
```json
{
  "items": [...],
  "total": 1423,
  "page": 1,
  "page_size": 20,
  "pages": 72
}
```

**Implementation notes:**
- Add `page: int = 1` and `page_size: int = Query(default=20, le=100)` query params to all list endpoints
- Enforce `page_size <= 100` hard limit
- Add `offset()` and `limit()` to all repository `list_all()` methods
- Add `func.count()` subquery for total count
- Add filtering parameters to `AppointmentRepository.list_filtered(doctor_id, patient_id, status, from_date, to_date, page, page_size)`
- Existing tests that call `GET /appointments` and assert `isinstance(data, list)` need updating to unwrap `data["items"]`

**Alembic migration:** None needed for schema. The existing indexes support the new filter queries.

---

### 3.7 Feature: TLS at NGINX (NFR-SEC-8)

The SRS mandates TLS for production. Phase 5 documented the procedure but did not implement it. This phase delivers a working TLS setup.

**Implementation:**

1. Add `nginx/ssl/` directory to `.gitignore`
2. Create `nginx/nginx.conf.tls` with HTTPS server block, HTTP redirect, and HSTS header:
   ```nginx
   server {
       listen 80;
       return 301 https://$host$request_uri;
   }
   server {
       listen 443 ssl http2;
       ssl_certificate /etc/nginx/ssl/server.crt;
       ssl_certificate_key /etc/nginx/ssl/server.key;
       ssl_protocols TLSv1.2 TLSv1.3;
       ssl_session_cache shared:SSL:10m;
       ssl_session_timeout 10m;
       add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
       # ... existing location blocks unchanged ...
   }
   ```
3. `docker-compose.prod.yml`: mount `./nginx/ssl:/etc/nginx/ssl:ro` and expose port 443
4. Add a shell script `scripts/generate_dev_certs.sh` for local self-signed certificate generation
5. Document Let's Encrypt / Certbot procedure for production in a new `docs/TLS_Setup.md`
6. Add a CI step that validates the NGINX config with `nginx -t`

---

### 3.8 Feature: Centralised Log Aggregation (NFR-OBS-3)

**Implementation (Loki + Grafana stack):**

Add a `docker-compose.observability.yml` override:
```yaml
services:
  loki:
    image: grafana/loki:2.9.0
    ports: ["3100:3100"]
    
  promtail:
    image: grafana/promtail:2.9.0
    volumes:
      - /var/lib/docker/containers:/var/lib/docker/containers:ro
      - ./observability/promtail-config.yml:/etc/promtail/config.yml:ro
    
  grafana:
    image: grafana/grafana:10.0.0
    ports: ["3000:3000"]
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD:-admin}
```

Configure Promtail to scrape Docker container logs, label by service name (`clinic-worker`, `clinic-nginx`), and forward to Loki. Provide pre-built Grafana dashboards for:
- Request rate by endpoint
- Error rate by HTTP status
- p50/p95/p99 latency over time
- Booking success vs. conflict rate
- Circuit breaker state timeline

### 3.9 Phase 8 Acceptance Criteria

| Feature | Gate |
|---|---|
| Appointment status lifecycle | 6 tests pass; status transitions enforced; audit entry created |
| Appointment cancellation | Patient can cancel own; freed slot is immediately re-bookable |
| Doctor deactivation | Deactivated doctor excluded from list and booking |
| `GET /doctors/{id}` | Returns full profile; 404 for unknown ID |
| Patient PATCH | Email uniqueness enforced; audit logged |
| JWT refresh + logout | New access token issued on refresh; logout invalidates token |
| Pagination | All list endpoints return envelope; page_size hard-capped at 100 |
| TLS | HTTPS works in staging; HTTP redirects; HSTS header present |
| Log aggregation | Grafana shows logs from all 3 workers in single view |

### 3.10 Estimated Effort

| Task | Estimate |
|---|---|
| Appointment status lifecycle + cancellation | 2 days |
| Doctor profile, deactivation, inactive-booking fix | 1.5 days |
| Patient CRUD (GET /{id}, PATCH) | 1 day |
| JWT refresh tokens + logout + deny-list | 2 days |
| Pagination + filtering on all list endpoints | 2 days |
| TLS at NGINX | 1 day |
| Loki/Grafana observability stack | 1.5 days |
| Tests for all above | 1 day |
| **Phase 8 Total** | **~12 days** |

---

## 4. Phase 9 — Infrastructure Hardening and Production Reliability

### 4.1 Objectives

Eliminate single points of failure, prepare for cloud deployment, establish automated backup procedures, and deliver the Kubernetes manifests needed for production-grade orchestration. Estimated effort: **~10 days**.

### 4.2 Database High Availability

**Current state:** Single PostgreSQL 16 container. If this container crashes or the host machine fails, the entire service is down — 0% uptime.

**Target:** Primary + async read replica with automatic failover.

**Option A (Docker Compose, simpler):** Add a read replica via streaming replication:
```yaml
db_replica:
  image: postgres:16-alpine
  environment:
    - PGUSER=replicator
    - PGPASSWORD=${REPLICA_PASSWORD}
  command: >
    bash -c "until pg_basebackup -h db -D /var/lib/postgresql/data -U replicator -P -Xs -R; do sleep 1; done"
```

Configure `DATABASE_URL` for read-heavy operations (appointment list, patient list, doctor list) to use the replica. Write operations (create, update) continue to use the primary.

**Option B (Cloud, recommended for production):** Replace the self-hosted container with a managed DB service (AWS RDS, Google Cloud SQL, Azure Database for PostgreSQL). Benefits: automated failover, point-in-time recovery, automated backups, connection pooling via pgBouncer-compatible proxy.

**Required SQLAlchemy change:** Add a second engine for read operations:
```python
read_engine = create_async_engine(settings.READ_DATABASE_URL or settings.DATABASE_URL, ...)
read_session_factory = async_sessionmaker(read_engine, ...)
```

---

### 4.3 PgBouncer Connection Pooler

**Problem:** Each of 3 workers has `pool_size=20`, `max_overflow=10` — up to 90 PostgreSQL connections total. PostgreSQL's default `max_connections=100` leaves almost no headroom. Under load spikes or when scaling to 5+ workers, connection exhaustion will occur.

**Fix:** Deploy PgBouncer as a sidecar to the `db` service in transaction-pooling mode. Workers connect to PgBouncer (port 6432), which multiplexes connections against a smaller pool to PostgreSQL:

```yaml
pgbouncer:
  image: pgbouncer/pgbouncer:1.21
  environment:
    - DATABASE_URL=postgresql://clinic:clinicpass@db:5432/clinic_db
    - POOL_MODE=transaction
    - MAX_CLIENT_CONN=200
    - DEFAULT_POOL_SIZE=20
  ports: ["6432:6432"]
```

Update worker `DATABASE_URL` to point at `pgbouncer:6432`. Reduce `pool_size` to 5 per worker (PgBouncer handles the multiplexing).

---

### 4.4 Redis High Availability

**Current state:** Single Redis container with `allkeys-lru`. All metrics, future session data, and the deny-list (Phase 8) are lost on Redis restart.

**Fix:** Redis Sentinel (3-node: 1 primary + 2 replicas + 3 sentinels) for automatic failover. For simpler deployments, Redis persistence (`appendonly yes`) ensures recovery after restart.

```yaml
redis:
  command: >
    redis-server 
    --maxmemory 128mb 
    --maxmemory-policy allkeys-lru 
    --appendonly yes 
    --appendfilename clinic_aof.aof
    --requirepass ${REDIS_PASSWORD}
```

---

### 4.5 Kubernetes Manifests

**Deliverables:** A complete `k8s/` directory with:

```
k8s/
  namespace.yaml
  configmap.yaml          # non-secret configuration
  secret.yaml             # SECRET_KEY, DB_PASSWORD (use External Secrets Operator in prod)
  deployment-worker.yaml  # 3 replicas, resource limits, liveness/readiness probes
  service-worker.yaml     # ClusterIP service
  ingress.yaml            # NGINX Ingress with TLS termination (cert-manager)
  hpa.yaml               # HorizontalPodAutoscaler (min: 3, max: 10, CPU target: 70%)
  pdb.yaml               # PodDisruptionBudget (minAvailable: 2)
  deployment-redis.yaml   # Redis with persistence
  statefulset-postgres.yaml  # PostgreSQL StatefulSet with PVC
  cronjob-backup.yaml     # Daily pg_dump to S3/GCS
  servicemonitor.yaml     # Prometheus ServiceMonitor for metrics scraping
```

**Key Kubernetes features to use:**
- `readinessProbe`: poll `GET /api/v1/health` before sending traffic
- `livenessProbe`: restart worker if health fails 3 times in a row
- `HorizontalPodAutoscaler`: scale workers 3–10 based on CPU and custom metric `http_requests_total` from Prometheus
- `PodDisruptionBudget`: ensure at least 2 workers are always available during rolling updates
- Rolling update strategy: `maxSurge: 1, maxUnavailable: 0` for zero-downtime deployments

---

### 4.6 Automated Database Backup

**Deliverables:** A `CronJob` that runs `pg_dump` daily, compresses the output, and uploads to cloud object storage:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: postgres-backup
spec:
  schedule: "0 2 * * *"   # 02:00 UTC daily
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: pg-dump
            image: postgres:16-alpine
            command:
            - /bin/sh
            - -c
            - |
              pg_dump $DATABASE_URL | gzip > /backup/clinic_$(date +%Y%m%d_%H%M%S).sql.gz
              # Upload to S3: aws s3 cp /backup/*.gz s3://${BACKUP_BUCKET}/
```

Retention policy: keep 30 daily backups, 12 monthly backups, 7 yearly backups (implemented via S3 lifecycle rules or equivalent).

**Restore procedure** documented in `docs/Disaster_Recovery_Runbook.md`.

---

### 4.7 Prometheus AlertManager Rules

Complement the existing metrics endpoint with alerting rules:

```yaml
# observability/prometheus/alerts.yml
groups:
  - name: clinic_scheduler
    rules:
    - alert: HighErrorRate
      expr: rate(http_requests_total{status=~"5.."}[5m]) / rate(http_requests_total[5m]) > 0.05
      for: 2m
      annotations:
        summary: "HTTP error rate exceeds 5%"
        
    - alert: CircuitBreakerOpen
      expr: circuit_breaker_state{name="db"} == 1
      for: 30s
      annotations:
        summary: "Database circuit breaker is OPEN"
        
    - alert: HighP95Latency
      expr: histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m])) > 0.5
      for: 5m
      annotations:
        summary: "p95 latency exceeds 500ms SLA"

    - alert: BookingConflictRateHigh
      expr: rate(appointment_bookings_total{status="conflict"}[10m]) / rate(appointment_bookings_total[10m]) > 0.3
      for: 5m
      annotations:
        summary: "Booking conflict rate above 30% — possible scheduling issue"
```

Configure AlertManager to send alerts to Slack, PagerDuty, or email.

### 4.8 Phase 9 Acceptance Criteria

| Deliverable | Gate |
|---|---|
| Read replica | Write to primary, read from replica; replica lag < 100ms |
| PgBouncer | Worker connects via bouncer; PostgreSQL `pg_stat_activity` shows ≤ 25 connections |
| Redis persistence | Restart Redis; deny-list and metrics survive (Phase 8 dependency) |
| Kubernetes manifests | `kubectl apply -f k8s/` completes; all pods reach `Running` state |
| HPA | Scale workers to 6 under simulated load; scale back down after 10 minutes |
| Zero-downtime rolling update | Deploy new image; no 5xx errors during rollout |
| Daily backup CronJob | Backup file appears in object storage within 5 minutes of scheduled time |
| AlertManager | Trigger a test alert; Slack notification received within 60 seconds |

### 4.9 Estimated Effort

| Task | Estimate |
|---|---|
| Read replica setup | 1.5 days |
| PgBouncer integration | 1 day |
| Redis HA / persistence | 0.5 day |
| Kubernetes manifests (all) | 3 days |
| Backup CronJob + restore runbook | 1 day |
| Prometheus AlertManager rules | 1 day |
| End-to-end staging validation | 2 days |
| **Phase 9 Total** | **~10 days** |

---

## 5. Phase 10 — Advanced Feature Delivery

### 5.1 Objectives

Deliver the high and medium priority feature extensions from the Phase 6 deferred list, plus the NGINX dynamic upstream resolution fix. Estimated effort: **~18 days**.

### 5.2 Doctor Availability Windows

**Problem:** The current `GET /appointments/available` returns slots from 08:00–17:00 for every doctor every day, with no awareness of the doctor's actual schedule. Dr. Smith may work Monday, Wednesday, Friday only. Dr. Jones may work 09:00–12:00 on Tuesday.

**New table:** `doctor_schedules`

```sql
CREATE TABLE doctor_schedules (
    id          SERIAL PRIMARY KEY,
    doctor_id   INTEGER REFERENCES doctors(id) NOT NULL,
    day_of_week SMALLINT NOT NULL,  -- 0=Monday, 6=Sunday (ISO weekday)
    start_time  TIME NOT NULL,      -- e.g. 09:00
    end_time    TIME NOT NULL,      -- e.g. 17:00
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(doctor_id, day_of_week)
);

CREATE INDEX ix_doctor_schedules_doctor_id ON doctor_schedules(doctor_id);
```

**Alembic migration:** `006_doctor_schedules.py`

**New endpoints:**
```
GET    /api/v1/doctors/{id}/schedule            — get all schedule windows
PUT    /api/v1/doctors/{id}/schedule            — replace entire schedule (admin/doctor)
PATCH  /api/v1/doctors/{id}/schedule/{day}      — update single day (admin/doctor)
DELETE /api/v1/doctors/{id}/schedule/{day}      — remove a day (admin/doctor)
```

**Impact on `GET /appointments/available`:**
- Filter the 08:00–17:00 default by the doctor's actual schedule for the requested date's weekday
- Return HTTP 404 with `{"detail": "Doctor does not work on this day"}` if no schedule entry exists
- Slot boundaries respect `start_time` / `end_time` from the schedule, not hardcoded constants

---

### 5.3 Email Notifications

**Triggers:**
- Appointment created → confirmation email to patient
- Appointment cancelled → cancellation email to patient
- Appointment confirmed by doctor → confirmation email to patient
- 24 hours before appointment → reminder email to patient

**Architecture:** Use a Redis-backed background task queue (Celery with Redis broker, or a lightweight Python `asyncio` task producer/consumer). For simplicity in the current Docker Compose stack, implement as a FastAPI `BackgroundTask`:

```python
from fastapi import BackgroundTasks

@router.post("")
async def create_appointment(
    appt: AppointmentCreate,
    background_tasks: BackgroundTasks,
    ...
):
    # ... existing booking logic ...
    background_tasks.add_task(send_booking_confirmation, patient.email, new_appt)
    return JSONResponse(status_code=201, ...)
```

**Email provider:** Abstract behind an `EmailService` interface to support SMTP (dev), SendGrid, or AWS SES (production) without code changes:

```python
class EmailService:
    async def send(self, to: str, subject: str, body: str) -> None: ...

class SMTPEmailService(EmailService): ...
class SendGridEmailService(EmailService): ...
class NullEmailService(EmailService):  # for testing
    async def send(self, *args, **kwargs) -> None:
        logger.info("NULL EMAIL: would send to %s", args[0])
```

**Configuration:**
```python
# app/config.py
EMAIL_PROVIDER: str = "null"   # "smtp", "sendgrid", "null"
SMTP_HOST: str = "localhost"
SMTP_PORT: int = 1025
SENDGRID_API_KEY: str = ""
FROM_EMAIL: str = "clinic@example.com"
```

**Reminder scheduling:** Introduce a `CronJob` (Kubernetes) or a Docker Compose cron container that queries appointments in the next 24 hours and sends reminders. Alternatively, when an appointment is created, schedule a Redis `PEXPIREAT`-keyed entry that a background worker polls.

---

### 5.4 Appointment Notes and Attachments

The `Appointment` model already has a `notes` TEXT column. Expose it via the API:

**`PATCH /api/v1/appointments/{id}/notes`:**
```json
{ "notes": "Patient reported peanut allergy. Bring EpiPen." }
```

Permitted roles: `doctor`, `admin`. Patients cannot write clinical notes. Notes changes must be audit-logged.

**Attachment support (future):** Store attachment metadata in a new `appointment_attachments` table; actual files in S3 / GCS. Pre-signed URLs issued on request. Out of scope for Phase 10 but table should be designed with this in mind.

---

### 5.5 Recurring Appointments

Allow booking a repeating appointment (weekly, bi-weekly, monthly) for a fixed number of occurrences.

**New endpoint:**
```
POST /api/v1/appointments/recurring
```

**Request:**
```json
{
  "doctor_id": 1,
  "patient_id": 42,
  "start_time": "2026-07-01T09:00:00Z",
  "duration_minutes": 30,
  "recurrence": "weekly",
  "occurrences": 12
}
```

**Response:** Creates 12 individual `Appointment` records. Conflict is checked for each occurrence individually. Returns a list of created appointments and a list of conflicts (slots that could not be booked).

**New table:** `recurring_series` (links individual appointments to a series, enabling batch cancellation):

```sql
CREATE TABLE recurring_series (
    id          SERIAL PRIMARY KEY,
    doctor_id   INTEGER REFERENCES doctors(id),
    patient_id  INTEGER REFERENCES patients(id),
    recurrence  VARCHAR(20) NOT NULL,   -- 'weekly', 'biweekly', 'monthly'
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE appointments ADD COLUMN series_id INTEGER REFERENCES recurring_series(id);
```

**New endpoint:**
```
DELETE /api/v1/appointments/series/{series_id}   — cancel all remaining appointments in a series
```

---

### 5.6 NGINX Dynamic Upstream Resolution

**Problem (BUG-09 resolution):** NGINX resolves Docker Compose service names at startup. When workers restart or scale, stale IPs may be cached. The fix requires NGINX to use Docker's embedded DNS for dynamic resolution:

```nginx
resolver 127.0.0.11 valid=5s;

upstream clinic_backend {
    # Removed: hash $request_uri consistent (not compatible with resolver variables)
}

location /api/ {
    set $backend http://worker:8000;
    proxy_pass $backend;
    ...
}
```

Alternatively, switch to NGINX Plus (commercial) or use Traefik as the ingress in Kubernetes (which handles this natively).

---

### 5.7 API Versioning Strategy

**Current:** All endpoints are under `/api/v1/`. There is no `v2` strategy.

**Implement:**

1. Define a versioning policy: URI versioning (`/api/v2/`) is already in use
2. Create `app/api/v2/` package with a router that re-uses v1 repositories but exposes updated schemas (e.g., paginated responses, new fields)
3. Document the deprecation timeline for v1 endpoints in a new `docs/API_Versioning_Policy.md`
4. Add `Deprecation` and `Sunset` HTTP headers to v1 endpoints when v2 is live
5. NGINX routes both `/api/v1/` and `/api/v2/` to the backend (workers handle versioning internally)

The first v2 change: `GET /api/v2/appointments` returns the paginated envelope (from Phase 8) while `GET /api/v1/appointments` continues to return a flat list for backward compatibility.

---

### 5.8 Enhanced Load Testing

**Problem:** The current k6 test only exercises `GET /doctors`. The write path (`POST /appointments`) is in a separate `SCENARIO=write` mode but is not part of the default CI validation. The 200 VU target has never been validated.

**Deliverables:**

1. Fix k6 `setup()` null-safety (BUG-10 from Phase 7)
2. Create a realistic mixed-load scenario: 70% reads (GET /doctors, GET /appointments), 20% bookings (POST /appointments), 10% status updates (PATCH /appointments/{id}/status)
3. Add a dedicated staging environment load test stage in CI (post-deploy, optional, triggered manually)
4. Run the 200 VU test on Linux (GitHub Actions) and publish the results as a CI artifact
5. Add booking throughput as a tracked metric: `bookings_per_second` custom threshold ≥ 10 bps at 200 VUs

### 5.9 Phase 10 Estimated Effort

| Task | Estimate |
|---|---|
| Doctor availability windows + schedule endpoints | 3 days |
| Email notifications (background tasks + providers) | 2.5 days |
| Appointment notes PATCH endpoint | 0.5 day |
| Recurring appointments | 3 days |
| NGINX dynamic resolution fix | 0.5 day |
| API v2 scaffolding + versioning policy | 1.5 days |
| Enhanced load testing (mixed scenario, 200 VU) | 2 days |
| Tests for all above | 2 days |
| Documentation updates | 0.5 day |
| Migration + regression testing | 2.5 days |
| **Phase 10 Total** | **~18 days** |

---

## 6. Phase 11 — Analytics, Client Portal, and Ecosystem Integrations

### 6.1 Objectives

Deliver the administrative visibility layer, a patient-facing self-service portal, and the integrations that make the system part of a wider healthcare ecosystem. Estimated effort: **~20 days**.

### 6.2 Admin Dashboard API

An admin-facing analytics API that aggregates data from the PostgreSQL database. No new tables required — uses existing data.

**New endpoints under `/api/v1/admin/analytics/`:**

| Endpoint | Description |
|---|---|
| `GET /analytics/summary` | Total appointments, patients, doctors, cancellation rate, avg duration |
| `GET /analytics/doctors/{id}/utilisation` | Booked vs. available slots ratio over a date range |
| `GET /analytics/peak-hours` | Histogram of bookings by hour of day |
| `GET /analytics/cancellation-reasons` | (Requires adding `cancellation_reason` field to appointments) |
| `GET /analytics/patients/{id}/history` | Full appointment history for a patient |
| `GET /analytics/audit-log` | Paginated, filterable audit log (actor, action, date range) |

**Performance:** All analytics queries run against the read replica (Phase 9). Heavy aggregations are cached in Redis with a 5-minute TTL.

---

### 6.3 Patient Self-Service Portal (Web UI)

A minimal, single-file HTML/JS frontend served by NGINX as a static site.

**Features:**
- Patient login (calls `POST /auth/login`)
- View upcoming appointments
- Book a new appointment (select doctor → select date → select available slot)
- Cancel an upcoming appointment
- View/update own profile

**Technical approach:**
- Single `index.html` file using vanilla JS (no build step)
- Served by NGINX from `/ui/` location block
- Calls the existing REST API — no new backend code required
- Responsive design for mobile (patients use phones)
- Stored at `frontend/index.html` in the repository

**NGINX addition:**
```nginx
location /ui/ {
    alias /usr/share/nginx/html/;
    index index.html;
    try_files $uri $uri/ /ui/index.html;
}
```

---

### 6.4 Doctor Mobile App API Extensions

New endpoints to support a doctor-facing mobile application:

```
GET /api/v1/doctors/{id}/appointments/today    — today's appointments, ordered by time
GET /api/v1/doctors/{id}/appointments/upcoming — next 7 days
PATCH /api/v1/appointments/{id}/notes           — add clinical notes (doctor only)
GET /api/v1/doctors/{id}/patients              — unique patients seen (distinct patient_id)
```

All require `doctor` or `admin` role. The authenticated `doctor` user can only access their own schedule (verify `doctor_id` matches the user's linked doctor record — requires adding `user_id FK` to the `doctors` table, implemented as a Phase 11 Alembic migration).

---

### 6.5 Webhook Notifications

Allow external systems (e.g., EHR systems, insurance platforms, communication tools) to subscribe to appointment events.

**New table:** `webhooks`
```sql
CREATE TABLE webhooks (
    id          SERIAL PRIMARY KEY,
    url         VARCHAR(500) NOT NULL,
    secret      VARCHAR(255) NOT NULL,    -- HMAC-SHA256 signing secret
    events      TEXT[] NOT NULL,          -- ['appointment.created', 'appointment.cancelled']
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_by  VARCHAR(100) NOT NULL,    -- admin username
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
```

**Webhook delivery:**
- Triggered as a background task after each appointment mutation
- Signed with HMAC-SHA256 using the webhook secret
- Payload: `{"event": "appointment.created", "timestamp": "...", "data": {...}}`
- Retry policy: 3 attempts with exponential backoff (1s, 5s, 25s)
- Delivery status logged in a `webhook_deliveries` table for debugging

**Admin endpoints:**
```
POST   /api/v1/admin/webhooks        — register a webhook
GET    /api/v1/admin/webhooks        — list all webhooks
DELETE /api/v1/admin/webhooks/{id}   — deactivate a webhook
POST   /api/v1/admin/webhooks/{id}/test  — send a test payload
```

---

### 6.6 Multi-Tenant Support (Multiple Clinics)

Add a `clinic_id` tenant identifier to all entities, enabling a single deployment to serve multiple independent clinics.

**New table:** `clinics`
```sql
CREATE TABLE clinics (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(200) NOT NULL,
    slug        VARCHAR(50) UNIQUE NOT NULL,  -- URL-safe identifier
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
```

**Schema changes:** Add `clinic_id INTEGER REFERENCES clinics(id) NOT NULL` to `users`, `doctors`, `patients`, `appointments`. All repository queries must include `WHERE clinic_id = :clinic_id`.

**Routing:** Each tenant identified by a subdomain (`clinic-a.scheduler.example.com`) or by a request header (`X-Clinic-ID: 42`). NGINX passes the header; a new `get_current_clinic` dependency resolves it.

**Data isolation:** Row-level security (PostgreSQL RLS) policies ensure that no query can accidentally cross tenant boundaries:
```sql
ALTER TABLE appointments ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON appointments
    USING (clinic_id = current_setting('app.current_clinic_id')::INTEGER);
```

---

### 6.7 OpenTelemetry Distributed Tracing

Replace the manual `X-Request-ID` + `node_id` approach with full OpenTelemetry instrumentation.

**Implementation:**

```python
# requirements.txt additions:
opentelemetry-api
opentelemetry-sdk
opentelemetry-instrumentation-fastapi
opentelemetry-instrumentation-sqlalchemy
opentelemetry-exporter-otlp-proto-grpc

# app/core/telemetry.py
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

def configure_telemetry(app: FastAPI):
    provider = TracerProvider()
    provider.add_span_exporter(OTLPSpanExporter(endpoint=settings.OTLP_ENDPOINT))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    SQLAlchemyInstrumentor().instrument(engine=engine)
```

Deploy Jaeger or Grafana Tempo as the trace backend. Every database query, Redis call, and external HTTP request becomes a span, enabling end-to-end request tracing across workers.

### 6.8 Phase 11 Estimated Effort

| Task | Estimate |
|---|---|
| Admin analytics API | 2.5 days |
| Patient self-service web portal | 3 days |
| Doctor mobile API extensions + user-doctor linking | 2 days |
| Webhook delivery system | 3 days |
| Multi-tenant support (schema + RLS) | 4 days |
| OpenTelemetry instrumentation | 2 days |
| Tests, documentation, migration | 3.5 days |
| **Phase 11 Total** | **~20 days** |

---

## 7. Cross-Phase Architecture Decisions

These decisions affect multiple phases and should be made before Phase 8 begins.

### 7.1 User–Patient Record Linkage

**Current problem:** The `users` table and `patients` table are entirely disconnected. There is no FK from a registered user to their patient record. `GET /patients/me` returns a fabricated profile (BUG-03).

**Decision needed:** Add `user_id INTEGER REFERENCES users(id) UNIQUE` to the `patients` table. When a patient registers, automatically create their patient record. This requires:
- Alembic migration
- Update `POST /auth/register` to create a `Patient` record (with email from username, or require email at registration)
- Update `GET /patients/me` to join through this FK
- Update booking to allow `patient_id` to come from the authenticated user's linked patient record

**This is the most architecturally significant change in the backlog.** It should be prioritised at the top of Phase 8.

### 7.2 Event-Driven Architecture

As features grow (email notifications, webhooks, reminders, analytics), the synchronous FastAPI request handler increasingly becomes a bottleneck. Consider:

- **Short term (Phase 10):** FastAPI `BackgroundTasks` for fire-and-forget operations (email, webhook delivery)
- **Medium term (Phase 11):** Redis Streams or a lightweight message broker (RabbitMQ) for durable event delivery
- **Long term:** Full event-sourcing with a dedicated event log table, enabling replay, audit, and analytics from the same event stream

### 7.3 Secrets Management

Current secrets handling is via environment variables in Docker Compose files. For production:
- **Kubernetes:** Use External Secrets Operator + AWS Secrets Manager / HashiCorp Vault
- **Docker Compose:** Use Docker Secrets (swarm mode) or sops-encrypted `.env` files
- All secrets must be rotatable without redeployment downtime

---

## 8. Dependency and Risk Register

| Risk | Probability | Impact | Phase | Mitigation |
|---|---|---|---|---|
| BUG-01 (sync Redis) causes event loop starvation under load | High | Critical | 7 | Fix immediately; rerun k6 after fix |
| BUG-04 (name lookup) causes wrong patient record in booking | Medium | Critical | 7 | Fix before any production traffic |
| User–Patient linkage refactor breaks existing integrations | Medium | High | 8 | API versioning; backward-compatible defaults |
| JWT refresh tokens introduce new attack surface | Low | High | 8 | Security review of refresh flow before shipping |
| Multi-tenant RLS migration corrupts existing data | Low | Critical | 11 | Feature-flagged; run on empty staging DB first |
| Doctor availability windows conflict with existing open-hours assumption | Medium | Medium | 10 | Default schedule (08:00–17:00 Mon–Fri) for all existing doctors on migration |
| PgBouncer transaction-mode incompatible with SQLAlchemy session assumptions | Medium | High | 9 | Test session commit/rollback behaviour explicitly; may need to use statement-mode |
| NGINX dynamic resolver variable breaks consistent hashing | Low | Medium | 10 | Switch to round-robin for K8s; consistent hashing is NGINX Plus feature |
| Async metrics Redis fails silently, metrics gaps | Low | Low | 7 | Add circuit breaker around metric calls; never let metric failure fail the request |
| k6 OOM on Windows at 200 VUs | High | Medium | 10 | Run 200 VU tests only in CI (Linux runners) |

---

## 9. Definition of Done (Updated)

All phases from 7 onwards must meet these criteria before being declared complete.

### 9.1 Code Quality Gates

| Gate | Tool | Threshold |
|---|---|---|
| Linting | `ruff check app/ tests/` | Zero errors |
| Formatting | `ruff format --check app/ tests/` | Zero diffs |
| Type checking | `mypy app/ --strict` | Zero errors (incremental, new files only) |
| Security scan | `bandit -r app/ -ll` | Zero HIGH or CRITICAL findings |
| No sync Redis in async code | `grep -rn "^import redis$"` in async modules | Zero matches |

### 9.2 Testing Gates

| Gate | Threshold |
|---|---|
| All existing tests still pass | 100% pass rate (no regressions) |
| New feature unit test coverage | ≥ 80% line coverage on new modules |
| New integration tests | At least 1 integration test per new endpoint |
| Concurrent booking still passes | `test_concurrent_same_slot_one_succeeds` passes |
| Load test thresholds | p95 < 500ms, error rate < 5% at 200 VUs (Linux CI) |

### 9.3 Documentation Gates

| Gate | Requirement |
|---|---|
| `AGENTS.md` updated | New gotchas, commands, and endpoints documented |
| Swagger UI | All new endpoints appear with correct schemas |
| Alembic | New migration reviewed, tested with `upgrade head` and `downgrade -1` |
| Phase implementation report | Written and committed to `app/docs/` |
| ADR (Architecture Decision Record) | Written for any non-obvious architectural choice |

### 9.4 Operational Gates

| Gate | Requirement |
|---|---|
| `docker compose up -d --build` succeeds from clean checkout | All services healthy within 90 seconds |
| `GET /api/v1/health` returns 200 | Both DB and Redis healthy |
| Graceful shutdown | SIGTERM drains in-flight requests within 15 seconds |
| Zero-downtime rolling update | No 5xx during `docker compose up -d` restart |

---

## 10. Consolidated Milestone Timeline

The timeline below assumes a team of 2–3 backend engineers, 1 DevOps engineer, and 1 QA engineer.

| Milestone | Phase | Key Deliverables | Estimated Weeks | Cumulative |
|---|---|---|---|---|
| M7: All Bugs Fixed | 7 | Async Redis, query bounds, patient/me, bool is_active, CHAOS toggle | W1–2 | W2 |
| M8a: Core SRS Compliance | 8 (first half) | Cancellation, status lifecycle, doctor deactivation, GET /doctors/{id}, user-patient linkage | W3–4 | W4 |
| M8b: Auth and API Hardening | 8 (second half) | JWT refresh, pagination, TLS, Loki/Grafana | W5–6 | W6 |
| M9a: DB HA + PgBouncer | 9 (first half) | Read replica, connection pooler, Redis persistence | W7–8 | W8 |
| M9b: Kubernetes and Backup | 9 (second half) | K8s manifests, HPA, PDB, backup CronJob, AlertManager | W9–10 | W10 |
| M10a: Doctor Schedules + Notifications | 10 (first half) | Availability windows, schedule CRUD, email notifications | W11–13 | W13 |
| M10b: Recurring + Versioning + Load | 10 (second half) | Recurring appointments, API v2, 200 VU load test | W14–16 | W16 |
| M11a: Analytics and Portal | 11 (first half) | Admin analytics API, patient web portal, doctor mobile API | W17–19 | W19 |
| M11b: Integrations and Ecosystem | 11 (second half) | Webhooks, multi-tenant, OpenTelemetry | W20–23 | W23 |

> **Critical path:** Phase 7 bugs must be fixed before any Phase 8 feature work begins. The user–patient linkage decision (Section 7.1) must be made in the first week of Phase 8 as it affects the schema for all subsequent features.

---

*This document should be reviewed and updated at the start of each phase. Architecture decisions made during implementation should be captured as Architecture Decision Records (ADRs) in `docs/adr/`.*

*Prepared by: Senior Engineering Lead | Clinic Scheduler Project | 2026-05-21*
