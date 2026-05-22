# Clinic Scheduler — Engineering Roadmap
## Phases 13–17: What's Next

| Field | Value |
|---|---|
| Document Version | 2.0.0 |
| Status | Active — Engineering Reference |
| Prepared By | Senior Engineering Lead |
| Date | 2026-05-22 |
| Baseline | Phases 0–12 Complete · 162 Tests Passing |
| Classification | Internal / Technical |

---

## Executive Summary

Phases 0–12 delivered a fully featured, multi-tenant, production-deployable booking system. However, a thorough forensic audit of the delivered codebase — every router, model, migration, middleware, background task, and frontend file — has uncovered **10 confirmed bugs**, several of which are **security-relevant or data-corrupting** in production. Additionally, several features that were designed and scaffolded (reminder scheduler, tenant management, password reset, read replica, RLS) were never fully implemented.

**This document is not aspirational. Every item in Phases 13 and 14 must be completed before Phase 12 code is deployed to production.**

| Phase | Title | Criticality | Effort |
|---|---|---|---|
| **13** | Critical Bug Fixes — Production Blockers | 🔴 Must ship before prod | ~7 days |
| **14** | Data Layer Completion — Missing Migrations and Architecture | 🔴 Must ship before prod | ~8 days |
| **15** | Appointment Lifecycle Completions | 🟠 High — functional gaps | ~7 days |
| **16** | Platform Hardening — Auth, Tenants, and Security | 🟠 High — security posture | ~10 days |
| **17** | Operational Excellence — Infrastructure and Deployment | 🟡 Medium — production maturity | ~12 days |

---

## Table of Contents

1. [Current State: Confirmed Bugs and Gaps](#1-current-state-confirmed-bugs-and-gaps)
2. [Phase 13 — Critical Bug Fixes](#2-phase-13--critical-bug-fixes)
3. [Phase 14 — Data Layer Completion](#3-phase-14--data-layer-completion)
4. [Phase 15 — Appointment Lifecycle Completions](#4-phase-15--appointment-lifecycle-completions)
5. [Phase 16 — Platform Hardening](#5-phase-16--platform-hardening)
6. [Phase 17 — Operational Excellence](#6-phase-17--operational-excellence)
7. [Dependency Map](#7-dependency-map)
8. [Consolidated Risk Register](#8-consolidated-risk-register)
9. [Definition of Done (Phase 13+)](#9-definition-of-done-phase-13)
10. [Milestone Timeline](#10-milestone-timeline)

---

## 1. Current State: Confirmed Bugs and Gaps

The following were discovered by reading the delivered source code directly — not from test failures, because most of these bugs are untested paths that pass silently.

### 1.1 Confirmed Bugs (Ordered by Severity)

---

**BUG-A — Security: Refresh Token Lookup is O(N × bcrypt_cost)**

**File:** `app/api/v1/routers/auth.py` — `POST /auth/refresh`

```python
# Current code — scans every user in the database
result = await db.execute(select(User).where(User.refresh_token_hash.isnot(None)))
users = result.scalars().all()
matched_user = None
for u in users:
    if u.refresh_token_hash and verify_refresh_token(req.refresh_token, u.refresh_token_hash):
        ...
```

With 10,000 registered users, `POST /auth/refresh` performs 10,000 bcrypt verifications sequentially. bcrypt at cost factor 12 takes ~250ms per verification. That is **41 minutes** of CPU time per refresh request. This will completely stall the event loop and bring down all three workers under any real load.

**Root cause:** The refresh token raw value is never stored or indexed. The system has no way to look up the correct user without scanning everyone.

---

**BUG-B — Critical: Refresh Token Expiry Check Raises TypeError**

**File:** `app/api/v1/routers/auth.py` — `POST /auth/refresh`

```python
# u.refresh_token_expires_at is a NAIVE datetime (no tzinfo)
# datetime.now(timezone.utc) is an AWARE datetime (has tzinfo)
if u.refresh_token_expires_at and u.refresh_token_expires_at < datetime.now(timezone.utc):
    raise HTTPException(status_code=401, detail="Refresh token expired")
```

In Python 3, comparing a naive datetime to an aware datetime raises `TypeError: can't compare offset-naive and offset-aware datetimes`. This means `POST /auth/refresh` **always throws an unhandled 500 error** when a user has a stored refresh token. The refresh token feature, delivered in Phase 8, has never worked.

---

**BUG-C — Security: Doctor Ownership Check Never Matches**

**File:** `app/api/v1/routers/doctors.py` — `GET /doctors/{id}/appointments/today`, `GET /doctors/{id}/appointments/upcoming`, `GET /doctors/{id}/patients`

```python
# current_user.get("user_id") returns the username STRING from JWT sub claim
# e.g., "dr_smith_abc123"
# Doctor.user_id is INTEGER FK → users.id
# e.g., 7

doc_result = await db.execute(
    select(Doctor).where(
        Doctor.user_id == current_user.get("user_id"),  # "dr_smith_abc123" != 7
        Doctor.tenant_id == tenant_id,
    )
)
linked_doctor = doc_result.scalar_one_or_none()
if not linked_doctor or linked_doctor.id != doctor_id:
    raise HTTPException(status_code=403, detail="Can only access own appointments")
```

Because a username string will never equal an integer, `linked_doctor` is always `None`, and every doctor-role user gets HTTP 403 when trying to view their own schedule. The doctor mobile API endpoints (`today`, `upcoming`, `patients`) are completely non-functional for `doctor` role users. Only `admin` users can use them.

---

**BUG-D — Data Corruption: Webhook Delivery Uses Closed Session**

**File:** `app/core/webhooks.py` — `trigger_webhooks()`

```python
async def trigger_webhooks(session, event_type, data):
    result = await session.execute(select(Webhook).where(...))
    webhooks = result.scalars().all()
    for webhook in webhooks:
        # This task runs AFTER the request completes
        # The session is ALREADY CLOSED by then
        asyncio.create_task(deliver_webhook(session, webhook, event_type, data))
```

`asyncio.create_task()` schedules the coroutine to run on the event loop after the current coroutine yields. By the time `deliver_webhook` executes, the `get_db()` context manager has already committed and closed the session. The `WebhookDelivery` insert inside `deliver_webhook` will raise `sqlalchemy.exc.InvalidRequestError: Session is closed`. Every webhook delivery attempt silently fails with an unhandled exception that is swallowed by the task.

---

**BUG-E — Security: Token Deny-List Creates Unbounded Redis Connections**

**File:** `app/api/v1/dependencies.py` — `get_current_user()`

```python
async def get_current_user(...):
    # A brand-new Redis connection is opened on EVERY authenticated request
    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    jti = user_id + ":" + credentials.credentials[:8]
    denied = await redis.get(f"token_denylist:{jti}")
    await redis.aclose()   # closed, but TCP teardown still costs ~1ms
```

At 200 concurrent VUs, this creates and destroys 200 Redis connections per second. Redis has a default `maxclients=10000` limit, but the repeated connection/teardown overhead adds ~1–2ms to every authenticated request and generates significant GC pressure. The `jti` using only 8 characters of the token is also a weak identifier — two users with the same username and tokens sharing the same first 8 characters would share a deny-list entry.

---

**BUG-F — Functional: Frontend Cancel Button Calls Non-Existent Endpoint**

**File:** `frontend/index.html` — `cancelAppointment()` function

```javascript
// Wrong — this endpoint does not exist
async function cancelAppointment(id) {
    const resp = await api(`/appointments/${id}/cancel`, { method: 'PATCH' });
    ...
}
```

The actual endpoint is `PATCH /api/v1/appointments/{id}/status` with request body `{"status": "cancelled"}`. Every patient who clicks "Cancel" in the self-service portal receives an HTTP 405 Method Not Allowed or 404 Not Found. The portal's primary action for patients is silently broken.

---

**BUG-G — Production Blocker: Alembic Migrations Do Not Create Multi-Tenant Schema**

**Files:** `alembic/versions/001_initial_schema.py` through `009_webhooks.py`

The nine existing Alembic migrations create all tables **without `tenant_id` columns**. The SQLAlchemy models (`app/models/__init__.py`) now define `tenant_id` as `NOT NULL` on every table. When `ALEMBIC_ENABLED=true`:

1. `alembic upgrade head` runs migrations 001–009 → tables created without `tenant_id`
2. `seed_data()` runs and tries to insert `Doctor(name=..., tenant_id=1)` → **psycopg2 error: column "tenant_id" of relation "doctors" does not exist**
3. The entire application fails to start in production mode

Development mode (`ALEMBIC_ENABLED=false`, `create_all`) works because SQLAlchemy reads the current model definition. **Production deployment is completely broken.**

---

**BUG-H — Performance: Connection Pool Too Small for Direct PostgreSQL**

**File:** `app/db/session.py`

```python
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=5,        # Reduced in Phase 9 for PgBouncer
    max_overflow=5,     # Total: 10 connections per worker
    ...
)
```

Phase 9 reduced `pool_size` from 20 to 5 to avoid overwhelming PostgreSQL through PgBouncer. However, Phase 9's own report notes: "Workers connect directly to `db:5432` instead" (PgBouncer had asyncpg SCRAM incompatibility). With 3 workers at `pool_size=5, max_overflow=5`, the total maximum is **30 PostgreSQL connections**. Under the k6 load test at 200 VUs, connections exhaust within seconds, causing `pool_timeout` errors across all workers.

---

**BUG-I — Analytics: Potential Cross-Tenant Data Exposure**

**File:** `app/api/v1/routers/analytics.py`

```python
tenant_id = current_user.get("tenant_id")  # May be None for tokens issued before Phase 12

where_clauses = []
if tenant_id is not None:              # ← If None, no tenant filter is applied
    where_clauses.append(Appointment.tenant_id == tenant_id)
```

Tokens issued before Phase 12 (without the `tenant_id` claim) will result in `tenant_id = None`, bypassing all tenant filters in the analytics endpoints. Admin users with old tokens can see appointments, patients, and doctors across all tenants. This is a cross-tenant data leak.

---

**BUG-J — Functional: `GET /patients/me` Returns `id: 0` in Common Case**

**File:** `app/api/v1/routers/patients.py`

```python
result = await db.execute(
    select(Patient).where(
        Patient.email == f"{username}@clinic.com",
        Patient.tenant_id == tenant_id,
    )
)
patient = result.scalar_one_or_none()
if patient:
    return {"id": patient.id, "name": patient.name, "email": patient.email}
return {"id": 0, "name": username, "email": f"{username}@clinic.com"}  # ← id: 0
```

Unless an admin has manually created a patient record with the exact email `{username}@clinic.com`, every user gets `id: 0` from this endpoint. Since no registration flow automatically creates a patient record, **every newly registered patient user gets `id: 0`**, making the booking flow impossible without an admin manually creating their patient record first.

---

### 1.2 Unimplemented Features (Committed in Phase Reports as "Done" or "Next")

| Feature | Status | Evidence |
|---|---|---|
| Appointment reminder scheduler | Columns exist, no scheduler | `reminder_sent`, `next_reminder_at` columns in model, `get_due_reminders()` in repo, nothing calls them |
| Tenant management CRUD API | Model exists, no endpoints | `Tenant` model in `models/__init__.py`, no `/tenants` router |
| Password reset / forgot-password | Never started | No endpoints, no token mechanism |
| Account lockout after failed logins | Never started | No failed-attempt counter in users table |
| PostgreSQL Row-Level Security | Documented as future | Phase 12 report says "future: RLS policies" |
| Read replica for PostgreSQL | Documented as Option A/B | Phase 9 delivered only PgBouncer skeleton |
| Secrets management (Vault) | Kubernetes manifests reference it | `k8s/secret.yaml` uses hardcoded base64 values |
| Blue-green deployment | Mentioned in Phase 10 report | Only rolling update strategy exists |
| API key authentication | Mentioned in roadmap | No implementation |
| WebSocket / SSE for real-time | Mentioned in future scope | No implementation |

---

## 2. Phase 13 — Critical Bug Fixes

### 2.1 Objectives

Fix every confirmed bug. **Nothing proceeds to Phase 14 until all 10 bugs are resolved and tested.** Estimated effort: **~7 days**.

---

### FIX-13-A: Rearchitect Refresh Token Lookup (BUG-A + BUG-B)

**Root cause:** There is no indexed, non-sensitive identifier for a refresh token. The raw token is never stored (correct for security), but there is also no lookup key.

**Fix:** Add a `refresh_token_jti` column — a short, URL-safe random ID stored in plaintext. The full token sent to the client is `{jti}.{raw_secret}`. On refresh, the server parses the `jti`, looks up the user by `jti` in O(1), then bcrypt-verifies only that one user's hash.

**Alembic migration `010_refresh_token_jti.py`:**

```python
def upgrade():
    op.add_column("users",
        sa.Column("refresh_token_jti", sa.String(32), nullable=True, index=True))
    op.create_index("ix_users_refresh_token_jti", "users", ["refresh_token_jti"],
                    unique=True, postgresql_where=sa.text("refresh_token_jti IS NOT NULL"))
```

**Updated `app/core/security.py`:**

```python
def create_refresh_token(subject: str) -> tuple[str, str, str]:
    """Returns (raw_token_for_client, jti, hash_for_db)."""
    jti = secrets.token_urlsafe(16)      # 22-char URL-safe ID, stored in DB
    raw_secret = secrets.token_urlsafe(32)
    full_token = f"{jti}.{raw_secret}"   # Client receives jti.secret
    token_hash = refresh_context.hash(raw_secret)
    return full_token, jti, token_hash

def verify_refresh_token(full_token: str, stored_hash: str) -> bool:
    """Verify the secret portion against the stored hash."""
    try:
        _, raw_secret = full_token.split(".", 1)
        return refresh_context.verify(raw_secret, stored_hash)
    except Exception:
        return False
```

**Updated `POST /auth/refresh`:**

```python
@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(req: RefreshRequest, db: AsyncSession = Depends(get_db)):
    try:
        jti, _ = req.refresh_token.split(".", 1)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid refresh token format")

    # O(1) lookup by indexed JTI — not a full table scan
    result = await db.execute(select(User).where(User.refresh_token_jti == jti))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # Fix BUG-B: compare naive to naive
    now_naive = datetime.utcnow()
    if user.refresh_token_expires_at and user.refresh_token_expires_at < now_naive:
        raise HTTPException(status_code=401, detail="Refresh token expired")

    if not verify_refresh_token(req.refresh_token, user.refresh_token_hash):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # Rotate: issue new JTI and hash
    new_full_token, new_jti, new_hash = create_refresh_token(user.username)
    user.refresh_token_jti = new_jti
    user.refresh_token_hash = new_hash
    user.refresh_token_expires_at = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    await db.flush()

    access_token = create_access_token(
        subject=user.username,
        extra_claims={"role": user.role.value, "tenant_id": user.tenant_id},
    )
    return TokenResponse(access_token=access_token, refresh_token=new_full_token)
```

Also update register and login to use the new 3-tuple return value from `create_refresh_token`.

**Tests:** Unit test for JTI parsing; integration test for refresh that verifies only one DB query is issued; test for expired token returning 401; test for invalid secret returning 401.

---

### FIX-13-B: Fix Doctor Ownership Check (BUG-C)

**File:** `app/api/v1/routers/doctors.py`

The `current_user["user_id"]` is the username string (the JWT `sub` claim). The FK `Doctor.user_id` references `users.id` (integer). They can never be equal.

**Fix:** Look up the `User` record by username first to get the integer `id`, then compare:

```python
async def _get_linked_doctor(db: AsyncSession, username: str, tenant_id: int) -> Doctor | None:
    """Resolve a doctor-role user to their linked Doctor record."""
    user_result = await db.execute(
        select(User).where(User.username == username, User.tenant_id == tenant_id)
    )
    user = user_result.scalar_one_or_none()
    if not user:
        return None
    doctor_result = await db.execute(
        select(Doctor).where(Doctor.user_id == user.id, Doctor.tenant_id == tenant_id)
    )
    return doctor_result.scalar_one_or_none()
```

Replace all three ownership checks in `get_doctor_today_appointments`, `get_doctor_upcoming_appointments`, and `get_doctor_patients` with a call to `_get_linked_doctor`. This also requires `User` to be importable in `doctors.py`.

**Tests:** Integration test that registers a `doctor` role user, links them to a doctor record via `user_id`, and confirms they can call `GET /doctors/{id}/appointments/today`.

---

### FIX-13-C: Fix Webhook Delivery Session Lifecycle (BUG-D)

**File:** `app/core/webhooks.py`

The session must not be passed into a background task. Each delivery needs its own session.

**Fix:** Use a factory function to create a fresh session inside the task:

```python
from app.db.session import async_session_factory

async def _deliver_webhook_background(
    webhook_id: int,
    webhook_url: str,
    webhook_secret: str,
    webhook_events_json: str,
    event_type: str,
    data: dict,
    tenant_id: int,
) -> None:
    """Background webhook delivery with its own database session."""
    async with async_session_factory() as session:
        try:
            webhook_stub = SimpleNamespace(
                id=webhook_id, url=webhook_url, secret=webhook_secret,
                events=webhook_events_json, tenant_id=tenant_id
            )
            await deliver_webhook(session, webhook_stub, event_type, data)
            await session.commit()
        except Exception as e:
            logger.error("Webhook delivery failed: webhook_id=%s error=%s", webhook_id, e)
            await session.rollback()


async def trigger_webhooks(session, event_type, data):
    result = await session.execute(select(Webhook).where(Webhook.is_active.is_(True)))
    webhooks = result.scalars().all()
    for webhook in webhooks:
        events = json.loads(webhook.events)
        if event_type in events:
            # Pass only serialisable values — NOT the session
            asyncio.create_task(
                _deliver_webhook_background(
                    webhook.id, webhook.url, webhook.secret,
                    webhook.events, event_type, data, webhook.tenant_id
                )
            )
```

**Tests:** Integration test that mocks `httpx.AsyncClient.post` and verifies a webhook delivery record is persisted after the request completes.

---

### FIX-13-D: Fix Redis Connection Pool in `get_current_user` (BUG-E)

**File:** `app/api/v1/dependencies.py`

Create a module-level shared async Redis client with connection pooling, rather than a new connection per request.

```python
# app/core/redis_client.py — new shared module
import redis.asyncio as aioredis
from app.config import settings

# Single connection pool shared across all requests in this worker process
_redis_pool: aioredis.Redis | None = None

def get_redis() -> aioredis.Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=20,       # Matches DB pool_size
        )
    return _redis_pool
```

Update `app/api/v1/dependencies.py`:

```python
from app.core.redis_client import get_redis

async def get_current_user(...):
    ...
    redis = get_redis()                              # Reuses pool — no new connection
    jti = f"{user_id}:{credentials.credentials}"   # Use full token as JTI basis
    # Hash the full token to create a fixed-length, collision-free deny-list key
    import hashlib
    token_hash = hashlib.sha256(credentials.credentials.encode()).hexdigest()[:32]
    denied = await redis.get(f"token_denylist:{token_hash}")
    # No aclose() — pool manages the connection
```

Also update `app/api/v1/routers/auth.py` logout to use the same pool and the same `sha256`-based key.

Wire `_redis_pool` cleanup in `lifespan` shutdown in `app/main.py`:

```python
async with lifespan(app):
    yield
    if _redis_pool:
        await _redis_pool.aclose()
```

**Tests:** Verify that 100 sequential authenticated requests result in exactly 0 new Redis connection creations (mock the `aioredis.from_url` constructor and assert it is called only once).

---

### FIX-13-E: Fix Frontend Cancel Button (BUG-F)

**File:** `frontend/index.html`

```javascript
// Before (broken):
async function cancelAppointment(id) {
    const resp = await api(`/appointments/${id}/cancel`, { method: 'PATCH' });
    ...
}

// After (correct):
async function cancelAppointment(id) {
    if (!confirm('Cancel this appointment?')) return;
    const resp = await api(
        `/appointments/${id}/status`,
        {
            method: 'PATCH',
            body: JSON.stringify({ status: 'cancelled' }),
        }
    );
    if (resp && resp.ok) {
        showAlert('Appointment cancelled', 'success');
        loadAppointments();
    } else {
        const data = resp ? await resp.json() : {};
        showAlert(data.detail || 'Could not cancel appointment');
    }
}
```

**Tests:** Playwright or Selenium E2E test that logs in as a patient, books an appointment, clicks Cancel, and verifies the status changes to `cancelled`.

---

### FIX-13-F: Restore Connection Pool Size (BUG-H)

**File:** `app/db/session.py`

Since workers connect directly to PostgreSQL (`db:5432`), restore the pool size to support load:

```python
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=15,        # 3 workers × 15 = 45 connections — safe under PostgreSQL default 100
    max_overflow=5,      # Burst capacity: up to 60 total
    pool_timeout=10,
    pool_recycle=1800,
    echo=False,
)
```

Add `POOL_SIZE` and `MAX_OVERFLOW` to `app/config.py` so they can be tuned per environment without code changes:

```python
POOL_SIZE: int = 15
MAX_OVERFLOW: int = 5
```

**Tests:** Rerun the k6 load test at 50 VUs and verify zero `pool_timeout` errors in worker logs.

---

### FIX-13-G: Fix Analytics Cross-Tenant Exposure (BUG-I)

**File:** `app/api/v1/routers/analytics.py`

Replace the conditional `if tenant_id is not None` pattern with a hard requirement:

```python
@router.get("/summary")
async def get_analytics_summary(..., current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)

    tenant_id = current_user.get("tenant_id")
    if tenant_id is None:
        # Token pre-dates multi-tenant — force re-authentication
        raise HTTPException(
            status_code=401,
            detail="Token does not contain tenant claim. Please re-authenticate."
        )
    ...
    # All where_clauses now ALWAYS include tenant_id — no conditional
    where_clauses = [Appointment.tenant_id == tenant_id]
```

Apply the same pattern to all five analytics endpoints and to the `list_webhooks` and `list_webhook_deliveries` admin endpoints.

**Tests:** Register an admin user with an old-format token (no `tenant_id` claim, crafted via `create_access_token` without extra_claims), call `/admin/analytics/summary`, assert 401.

---

### 2.2 Phase 13 Acceptance Criteria

| Bug | Test | Pass Condition |
|---|---|---|
| BUG-A (refresh O(N)) | Unit + integration | Single DB query per refresh request |
| BUG-B (datetime TypeError) | Integration | `POST /auth/refresh` returns 200 for valid token |
| BUG-C (doctor ownership) | Integration | Doctor-role user can GET /doctors/{id}/appointments/today |
| BUG-D (webhook session) | Integration | WebhookDelivery record created after request completes |
| BUG-E (Redis connections) | Unit | `aioredis.from_url` called exactly once per worker process |
| BUG-F (frontend cancel) | Manual / E2E | Cancel button returns 200 and appointment status changes |
| BUG-H (pool size) | Load test | Zero pool_timeout errors at 50 VUs |
| BUG-I (analytics tenant) | Integration | Old token on analytics returns 401, not cross-tenant data |
| All existing tests | Full suite | 162+ tests pass, zero regressions |

### 2.3 Estimated Effort

| Task | Estimate |
|---|---|
| FIX-13-A: Refresh token rearchitecture | 2 days |
| FIX-13-B: Doctor ownership fix | 0.5 day |
| FIX-13-C: Webhook session fix | 0.5 day |
| FIX-13-D: Redis connection pool | 0.5 day |
| FIX-13-E: Frontend cancel button | 0.5 day |
| FIX-13-F: Pool size restoration | 0.5 day |
| FIX-13-G: Analytics tenant guard | 0.5 day |
| Tests, regression, migration | 2 days |
| **Phase 13 Total** | **~7 days** |

---

## 3. Phase 14 — Data Layer Completion

### 3.1 Objectives

Deliver the missing Alembic migrations for multi-tenancy, implement the User–Patient FK linkage (the most architecturally significant deferred item), and resolve all remaining data model inconsistencies. Estimated effort: **~8 days**.

---

### 3.2 Alembic Migration 010: Multi-Tenant Schema (BUG-G)

The existing 9 migrations create a schema that does not match the current models. A new migration must bridge this gap for any database created via Alembic (production).

**`alembic/versions/010_multi_tenant_schema.py`:**

```python
"""Add multi-tenant support to all tables

Revision ID: 010_multi_tenant_schema
Revises: 009_webhooks
"""
from alembic import op
import sqlalchemy as sa

def upgrade():
    # 1. Create tenants table
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(100), unique=True, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)

    # 2. Insert default tenant
    op.execute("INSERT INTO tenants (id, name, slug) VALUES (1, 'Default Clinic', 'default')")

    # 3. Add tenant_id to each table with a backfill default of 1
    for table in ["users", "doctors", "patients", "appointments",
                  "audit_log", "webhooks", "webhook_deliveries"]:
        op.add_column(table, sa.Column("tenant_id", sa.Integer,
                       sa.ForeignKey("tenants.id"), nullable=True))
        op.execute(f"UPDATE {table} SET tenant_id = 1")
        op.alter_column(table, "tenant_id", nullable=False)
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])

    # 4. Tenant-scoped unique constraints
    # Drop old global unique indexes
    op.drop_index("ix_users_username", "users")
    op.create_index("uix_user_tenant_username", "users",
                    ["tenant_id", "username"], unique=True)

    op.drop_index("ix_patients_email", "patients")
    op.create_index("uix_patient_tenant_email", "patients",
                    ["tenant_id", "email"], unique=True)

    # 5. recurring_series and doctor_schedules tables
    op.add_column("appointments",
        sa.Column("series_id", sa.Integer, sa.ForeignKey("recurring_series.id"), nullable=True))
    op.add_column("appointments",
        sa.Column("next_reminder_at", sa.DateTime, nullable=True))
    op.add_column("appointments",
        sa.Column("reminder_sent", sa.Boolean, nullable=False, server_default="false"))

    # 6. Doctor user_id linkage (from migration 008)
    op.add_column("doctors",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True))
    op.create_unique_constraint("uq_doctors_user_id", "doctors", ["user_id"])
    op.create_index("ix_doctors_user_id", "doctors", ["user_id"])

    # 7. Refresh token columns (from migration 005)
    op.add_column("users",
        sa.Column("refresh_token_jti", sa.String(32), nullable=True))
    op.add_column("users",
        sa.Column("refresh_token_hash", sa.String(255), nullable=True))
    op.add_column("users",
        sa.Column("refresh_token_expires_at", sa.DateTime, nullable=True))
    op.create_index("ix_users_refresh_token_jti", "users",
                    ["refresh_token_jti"], unique=True,
                    postgresql_where=sa.text("refresh_token_jti IS NOT NULL"))

    # 8. Duration column (from migration 002)
    op.add_column("appointments",
        sa.Column("duration_minutes", sa.Integer, nullable=False, server_default="30"))

    # 9. Doctor schedule table (from migration 006)
    op.create_table(
        "doctor_schedules",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("doctor_id", sa.Integer, sa.ForeignKey("doctors.id"), nullable=False),
        sa.Column("day_of_week", sa.Integer, nullable=False),
        sa.Column("start_time", sa.Time, nullable=False),
        sa.Column("end_time", sa.Time, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("doctor_id", "day_of_week"),
    )
    op.create_index("ix_doctor_schedules_doctor_id", "doctor_schedules", ["doctor_id"])

    # 10. Recurring series table (from migration 007)
    op.create_table(
        "recurring_series",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("doctor_id", sa.Integer, sa.ForeignKey("doctors.id"), nullable=False),
        sa.Column("patient_id", sa.Integer, sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("recurrence", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    # 11. is_active boolean migration for doctors (from migration 003)
    op.execute("ALTER TABLE doctors ADD COLUMN is_active_bool BOOLEAN NOT NULL DEFAULT TRUE")
    op.execute("UPDATE doctors SET is_active_bool = (is_active = 'true')")
    op.execute("ALTER TABLE doctors DROP COLUMN is_active")
    op.execute("ALTER TABLE doctors RENAME COLUMN is_active_bool TO is_active")

    # 12. Audit log indexes (from migration 004)
    op.create_index("ix_audit_log_actor", "audit_log", ["actor"])
    op.create_index("ix_audit_log_entity", "audit_log", ["entity_type", "entity_id"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])


def downgrade():
    # Reverse in order
    ...
```

**Why a single large migration instead of applying 001–009 then adding on top?**

Migrations 001–009 were written against models that no longer match. Running 009 then a 010 would result in tables created twice (e.g., `doctor_schedules` in 006, then again in 010). The correct approach for a production database that was created via `create_all` (dev mode) is migration 010 as an additive migration. For a fresh database (no existing schema), this single migration creates everything correctly.

The `alembic.ini` and `env.py` should be updated to set the base revision detection correctly using `alembic current`.

---

### 3.3 User–Patient FK Linkage

This is the most significant outstanding architectural gap. Every patient-facing workflow (booking for self, cancellation ownership, profile) currently relies on a fragile `{username}@clinic.com` email convention.

**Alembic migration `011_user_patient_link.py`:**

```python
def upgrade():
    op.add_column("patients",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True))
    op.create_unique_constraint("uq_patients_user_id", "patients", ["user_id"])
    op.create_index("ix_patients_user_id", "patients", ["user_id"])
```

**Updated registration flow** (`app/api/v1/routers/auth.py` — `POST /auth/register`):

```python
@router.post("/register", response_model=TokenResponse)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    repo = UserRepository(db)
    existing = await repo.get_by_username(req.username, tenant_id=req.tenant_id)
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    user = await repo.create(
        username=req.username,
        password=req.password,
        role=req.role,
        tenant_id=req.tenant_id,
    )

    # Automatically create a Patient record for patient-role users
    if req.role == "patient":
        patient_email = req.email or f"{req.username}@clinic.com"
        patient_repo = PatientRepository(db)
        patient = await patient_repo.get_or_create_by_email(
            name=req.username,
            email=patient_email,
            tenant_id=req.tenant_id,
        )
        patient.user_id = user.id
        await db.flush()

    ...
```

Add optional `email: str | None = None` to `RegisterRequest`. If provided, it becomes the patient's email. If not, the `{username}@clinic.com` convention is used as a fallback.

**Updated `GET /patients/me`:**

```python
@router.get("/me", response_model=PatientResponse)
async def get_my_profile(current_user: dict = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)):
    username = current_user["user_id"]
    tenant_id = current_user.get("tenant_id", 1)

    # Primary lookup: by user_id FK (post Phase-14 registrations)
    user_result = await db.execute(
        select(User).where(User.username == username, User.tenant_id == tenant_id)
    )
    user = user_result.scalar_one_or_none()
    if user:
        patient_result = await db.execute(
            select(Patient).where(Patient.user_id == user.id)
        )
        patient = patient_result.scalar_one_or_none()
        if patient:
            return {"id": patient.id, "name": patient.name, "email": patient.email}

    # Fallback: email convention (pre-Phase-14 registrations)
    email_result = await db.execute(
        select(Patient).where(
            Patient.email == f"{username}@clinic.com",
            Patient.tenant_id == tenant_id,
        )
    )
    patient = email_result.scalar_one_or_none()
    if patient:
        return {"id": patient.id, "name": patient.name, "email": patient.email}

    return {"id": 0, "name": username, "email": f"{username}@clinic.com"}
```

**Updated appointment cancellation ownership check:**

```python
# In PATCH /appointments/{id}/status, for patient role:
if role == "patient":
    if new_status != AppointmentStatus.CANCELLED:
        raise HTTPException(status_code=403, detail="Patients can only cancel")

    # Resolve patient from authenticated user via FK
    user_result = await db.execute(
        select(User).where(User.username == username, User.tenant_id == tenant_id)
    )
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=403, detail="User not found")

    patient_result = await db.execute(
        select(Patient).where(Patient.user_id == user.id)
    )
    patient = patient_result.scalar_one_or_none()
    if not patient or patient.id != appt.patient_id:
        raise HTTPException(status_code=403, detail="Cannot cancel another patient's appointment")
```

---

### 3.4 Doctor–User Linkage API

Migration 008 added `Doctor.user_id` but there are no endpoints to set this link. Admins need a way to associate a `doctor` role user with their `Doctor` record.

**New endpoint:** `PATCH /api/v1/admin/doctors/{doctor_id}/link-user`

```
Request: {"user_id": 7}
Response: {"doctor_id": 1, "user_id": 7, "linked": true}
```

- Only `admin` role
- Validates user exists and has `role == "doctor"`
- Validates no other doctor is already linked to that `user_id` (unique constraint)
- Audit logged

This endpoint is the prerequisite for the doctor mobile API to work correctly (FIX-13-B).

---

### 3.5 Pool Size and PgBouncer Decision

The Phase 9 PgBouncer setup has an unresolved incompatibility: asyncpg uses SCRAM-SHA-256 authentication by default, which PgBouncer in transaction mode does not support. The Phase 9 report resolved this by changing PostgreSQL to use `md5`, then ultimately by having workers connect directly to `db:5432` — defeating the entire purpose of PgBouncer.

**Decision:** Remove PgBouncer from the development `docker-compose.yml` (it is still in `docker-compose.prod.yml` for sync clients like Alembic). Workers connect directly to PostgreSQL with `pool_size=15`. Document in `AGENTS.md` that PgBouncer is reserved for Alembic migrations and admin tools, not application workers.

For production scale requiring PgBouncer, switch to **PgBouncer session mode** (which is compatible with SCRAM) or adopt **pgBouncer 1.22+** which supports SCRAM passthrough.

---

### 3.6 Phase 14 Acceptance Criteria

| Deliverable | Gate |
|---|---|
| Migration 010 applies cleanly to empty DB | `docker compose down -v && ALEMBIC_ENABLED=true docker compose up -d` starts successfully |
| Migration 010 applies to existing dev DB | Running against populated dev DB adds columns, preserves data |
| New patient user gets patient record on register | `POST /auth/register` creates Patient with `user_id` FK |
| `GET /patients/me` returns real `id` for new users | `id != 0` after registration |
| Appointment cancellation uses FK not email convention | Integration test verifying correct patient record |
| Doctor–user link endpoint works | Admin can PATCH `/admin/doctors/{id}/link-user` |
| Doctor mobile API works for doctor role | After linking, doctor can GET today's appointments |
| PgBouncer removed from worker `DATABASE_URL` | Workers use `db:5432`, Alembic uses pgbouncer in CI |

### 3.7 Estimated Effort

| Task | Estimate |
|---|---|
| Migration 010 (multi-tenant) | 2 days |
| Migration 011 (user-patient FK) | 0.5 day |
| Registration flow update | 1 day |
| patients/me, cancellation ownership | 1 day |
| Doctor-user link endpoint | 0.5 day |
| PgBouncer cleanup | 0.5 day |
| Tests | 1.5 days |
| Rollback testing (`alembic downgrade -1`) | 1 day |
| **Phase 14 Total** | **~8 days** |

---

## 4. Phase 15 — Appointment Lifecycle Completions

### 4.1 Objectives

Deliver the reminder scheduler (the only feature with a complete data model but no execution path), add appointment cancellation reasons, and improve the status workflow with doctor confirmation UX. Estimated effort: **~7 days**.

---

### 4.2 Appointment Reminder Scheduler

The `reminder_sent` and `next_reminder_at` columns exist. `AppointmentRepository.get_due_reminders()` and `send_reminder_email()` exist. Nothing calls them.

**Option A: Docker Compose cron container (simpler, current stack):**

```yaml
# docker-compose.yml addition
reminder-scheduler:
  build:
    context: .
    dockerfile: Dockerfile
  command: python -m app.scheduler.reminders
  environment:
    - DATABASE_URL=postgresql+asyncpg://clinic:clinicpass@db:5432/clinic_db
    - REDIS_URL=redis://:redispass@redis:6379/0
    - EMAIL_PROVIDER=${EMAIL_PROVIDER:-null}
  depends_on:
    db:
      condition: service_healthy
  networks:
    - clinic-net
  restart: unless-stopped
```

**New file `app/scheduler/reminders.py`:**

```python
import asyncio
import logging
from datetime import datetime, timedelta

from app.db.session import async_session_factory
from app.db.repository import AppointmentRepository, PatientRepository
from app.core.email import send_reminder_email

logger = logging.getLogger("clinic.scheduler.reminders")

POLL_INTERVAL_SECONDS = 300  # 5 minutes


async def run_reminder_loop() -> None:
    logger.info("Reminder scheduler started")
    while True:
        try:
            await send_due_reminders()
        except Exception as e:
            logger.error("Reminder loop error: %s", e, exc_info=True)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def send_due_reminders() -> None:
    async with async_session_factory() as session:
        repo = AppointmentRepository(session)
        due = await repo.get_due_reminders()
        for appt in due:
            patient_repo = PatientRepository(session)
            patient = await patient_repo.get_by_id(appt.patient_id)
            if patient:
                appt_detail = {
                    "doctor_id": appt.doctor_id,
                    "time_slot": appt.appointment_time.isoformat(),
                    "duration_minutes": appt.duration_minutes,
                }
                await send_reminder_email(patient.email, appt_detail)
                await repo.mark_reminder_sent(appt.id)
                logger.info("Reminder sent: appt_id=%s patient_id=%s", appt.id, patient.id)
        await session.commit()


if __name__ == "__main__":
    asyncio.run(run_reminder_loop())
```

**Option B: Kubernetes CronJob (production):**

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: appointment-reminders
  namespace: clinic-scheduler
spec:
  schedule: "*/5 * * * *"   # Every 5 minutes
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: reminder-scheduler
              image: clinic-scheduler-worker:latest
              command: ["python", "-m", "app.scheduler.reminders_once"]
          restartPolicy: OnFailure
```

Where `reminders_once.py` runs the loop once and exits (suitable for CronJob, no infinite loop).

**`next_reminder_at` population:** When an appointment is created, set `next_reminder_at = appointment_time - 24h`. The `get_due_reminders()` query already filters `appointment_time <= now + 24h`. Update it to also respect `next_reminder_at`:

```python
async def get_due_reminders(self) -> Sequence[Appointment]:
    now = datetime.utcnow()
    result = await self.session.execute(
        select(Appointment).where(
            Appointment.appointment_time > now,
            Appointment.appointment_time <= now + timedelta(hours=24),
            Appointment.status.in_(["scheduled", "confirmed"]),
            Appointment.reminder_sent.is_(False),
        )
    )
    return result.scalars().all()
```

**Tests:** Unit test for `send_due_reminders` using mocked email service and session. Integration test that creates an appointment with `appointment_time = now + 23h`, calls `send_due_reminders()`, and verifies `reminder_sent = True`.

---

### 4.3 Appointment Cancellation Reasons

Add a `cancellation_reason` field for when appointments are cancelled — useful for analytics (FR-APT-10 mentions audit log, but the reason is never captured).

**Migration `012_cancellation_reason.py`:**

```python
def upgrade():
    op.add_column("appointments",
        sa.Column("cancellation_reason", sa.String(500), nullable=True))
    op.add_column("appointments",
        sa.Column("cancelled_at", sa.DateTime, nullable=True))
    op.add_column("appointments",
        sa.Column("cancelled_by", sa.String(100), nullable=True))
```

**Updated `PATCH /appointments/{id}/status`:**

```python
class StatusUpdate(BaseModel):
    status: str
    cancellation_reason: str | None = None   # Optional, present when status=cancelled
```

```python
if new_status == AppointmentStatus.CANCELLED:
    updated.cancellation_reason = req.cancellation_reason
    updated.cancelled_at = datetime.utcnow()
    updated.cancelled_by = username
```

**Analytics endpoint update:** `GET /admin/analytics/summary` should now return a breakdown of cancellation reasons (most common reason, percentage).

---

### 4.4 Appointment Status Enhancements

**Missing: `PATCH /appointments/{id}/status` for patient self-booking workflow**

Currently, patients register → an admin creates their patient record → patient provides their patient_id to book. After Phase 14 (user-patient linkage), patients will have a linked record. The booking flow should allow:

```
POST /api/v1/appointments/for-me
```

A convenience endpoint that reads the authenticated user's linked `patient_id` automatically:

```python
@router.post("/for-me")
async def book_for_me(
    appt: AppointmentForMeCreate,   # No patient_id field — inferred from token
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Book an appointment for the authenticated patient user."""
    username = current_user["user_id"]
    tenant_id = current_user.get("tenant_id", 1)

    user_result = await db.execute(
        select(User).where(User.username == username, User.tenant_id == tenant_id)
    )
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    patient_result = await db.execute(
        select(Patient).where(Patient.user_id == user.id)
    )
    patient = patient_result.scalar_one_or_none()
    if not patient:
        raise HTTPException(status_code=400,
            detail="No patient profile linked to your account. Contact an admin.")

    # Delegate to existing booking logic with patient.id
    ...
```

This eliminates the need for patients to know their own `patient_id`, which is an API design flaw that was carried forward from the original design.

---

### 4.5 Frontend Portal Completions

Now that BUG-F is fixed and the user-patient linkage exists (Phase 14), complete the remaining frontend gaps:

- **Booking flow**: Call `POST /appointments/for-me` instead of requiring the patient to supply a `patient_id`
- **Available slots**: Display slots respecting doctor schedule windows (Phase 10 delivered this in the backend; the frontend uses hardcoded hours)
- **Appointment notes display**: Show notes on appointment cards (read-only for patients)
- **Profile page**: Show linked patient name and email; allow updating via `PATCH /patients/{id}`
- **Status display**: Show human-readable status with colour coding (scheduled=blue, confirmed=green, etc.)

---

### 4.6 Phase 15 Estimated Effort

| Task | Estimate |
|---|---|
| Reminder scheduler (Docker Compose + K8s) | 2 days |
| Cancellation reason migration + endpoint update | 1 day |
| `POST /appointments/for-me` endpoint | 1 day |
| Frontend portal completions | 2 days |
| Tests (reminder, for-me, frontend manual) | 1 day |
| **Phase 15 Total** | **~7 days** |

---

## 5. Phase 16 — Platform Hardening

### 5.1 Objectives

Deliver the security and platform features that were identified in Phase 5/8 roadmaps but never implemented: password reset, account lockout, tenant management API, PostgreSQL RLS, and API key authentication. Estimated effort: **~10 days**.

---

### 5.2 Password Reset Flow

**Current state:** No mechanism to recover from a forgotten password. Users are locked out permanently.

**New endpoints:**

```
POST /api/v1/auth/forgot-password
POST /api/v1/auth/reset-password
```

**Flow:**

1. `POST /auth/forgot-password` body: `{"email": "user@example.com"}` — always returns 200 (to prevent email enumeration). If a user with that email exists, generate a secure token, store its hash in `users.password_reset_hash` with `password_reset_expires_at = now + 15min`, and send the token via email.

2. `POST /auth/reset-password` body: `{"token": "...", "new_password": "..."}` — look up user by token JTI (same pattern as refresh tokens), verify hash, check expiry, update `hashed_password`, clear reset token fields.

**Migration `013_password_reset.py`:**

```python
def upgrade():
    op.add_column("users", sa.Column("password_reset_jti", sa.String(32), nullable=True))
    op.add_column("users", sa.Column("password_reset_hash", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("password_reset_expires_at", sa.DateTime, nullable=True))
    op.create_index("ix_users_password_reset_jti", "users", ["password_reset_jti"],
                    unique=True,
                    postgresql_where=sa.text("password_reset_jti IS NOT NULL"))
```

**Security notes:**
- Tokens expire in 15 minutes
- Tokens are single-use (cleared immediately after use)
- Response is always 200 for `forgot-password` (no user enumeration)
- Require `EMAIL_PROVIDER` to be set to a real provider in production (not `null`)

---

### 5.3 Account Lockout After Failed Login Attempts

**Current state:** Rate limiting is at the NGINX level (per IP). A single IP can make 500 requests/second, meaning an attacker can attempt 500 username/password combinations per second before hitting the rate limit.

**Migration `014_account_lockout.py`:**

```python
def upgrade():
    op.add_column("users", sa.Column("failed_login_attempts", sa.Integer, nullable=False, server_default="0"))
    op.add_column("users", sa.Column("locked_until", sa.DateTime, nullable=True))
```

**Updated `POST /auth/login`:**

```python
MAX_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    repo = UserRepository(db)
    user = await repo.get_by_username(req.username)

    if user and user.locked_until and user.locked_until > datetime.utcnow():
        raise HTTPException(status_code=429,
            detail=f"Account locked. Try again after {user.locked_until.isoformat()}")

    if not user or not verify_password(req.password, user.hashed_password):
        if user:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= MAX_ATTEMPTS:
                user.locked_until = datetime.utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
                logger.warning("Account locked: username=%s", req.username)
            await db.flush()
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Successful login — reset counter
    user.failed_login_attempts = 0
    user.locked_until = None
    await db.flush()
    ...
```

**Audit log:** Lock events should be written to `audit_log` with `action="account_locked"`, `actor=req.username`, `outcome="warning"`.

---

### 5.4 Tenant Management API

**Current state:** `Tenant` model exists and is seeded with a single default tenant. There are no API endpoints to create, list, or manage tenants. The system is functionally single-tenant from an operator perspective.

**New endpoints (super-admin only — a new role needed):**

```
GET    /api/v1/system/tenants               — list all tenants
POST   /api/v1/system/tenants               — create a tenant
GET    /api/v1/system/tenants/{id}          — get tenant details
PATCH  /api/v1/system/tenants/{id}          — update name/slug/is_active
GET    /api/v1/system/tenants/{id}/stats    — tenant usage stats
```

**New role: `superadmin`**

Add `SUPERADMIN = "superadmin"` to the `UserRole` enum. A `superadmin` can access all tenants. An `admin` can only access their own tenant. A `superadmin` is created via a CLI command or seed script — never via the API.

**Migration `015_superadmin_role.py`:**

```python
def upgrade():
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'superadmin'")
```

**New router `app/api/v1/routers/system.py`:**

```python
router = APIRouter(prefix="/system", tags=["system"])

def _require_superadmin(current_user: dict) -> None:
    if current_user["role"] != "superadmin":
        raise HTTPException(status_code=403, detail="Superadmin access required")

@router.post("/tenants", status_code=201)
async def create_tenant(req: TenantCreate, current_user=Depends(get_current_user), db=Depends(get_db)):
    _require_superadmin(current_user)
    tenant = Tenant(name=req.name, slug=req.slug)
    db.add(tenant)
    await db.flush()
    await audit_log(db, actor=current_user["user_id"], action="create_tenant",
                    entity_type="tenant", entity_id=tenant.id)
    return {"id": tenant.id, "name": tenant.name, "slug": tenant.slug}
```

---

### 5.5 PostgreSQL Row-Level Security

Application-level tenant filtering (passing `tenant_id` to every query) provides isolation only as long as every code path correctly sets the filter. A missed filter (like BUG-I) leaks data across tenants.

PostgreSQL RLS adds a database-level guarantee: even if application code forgets to filter, PostgreSQL enforces isolation.

**Implementation:**

```sql
-- Run once after tables are created
-- Each worker connects with the role 'clinic_app'
CREATE ROLE clinic_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO clinic_app;

-- Set the tenant for each transaction via a session variable
-- Workers call this at the start of each request:
-- SET LOCAL app.current_tenant_id = '1';

ALTER TABLE appointments ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_appointments ON appointments
    USING (tenant_id = current_setting('app.current_tenant_id', true)::INTEGER);

-- Repeat for doctors, patients, users, etc.
```

**FastAPI integration:**

Add a middleware or dependency that sets `app.current_tenant_id` on the PostgreSQL session:

```python
# app/core/tenant_session.py
async def set_tenant_context(db: AsyncSession, tenant_id: int) -> None:
    """Set PostgreSQL session variable for RLS enforcement."""
    await db.execute(text(f"SET LOCAL app.current_tenant_id = '{tenant_id}'"))
```

Call this at the start of each request handler that has a `tenant_id`. This is defense-in-depth: the application filter AND the database filter both apply.

**Note:** Superadmin operations require `SET LOCAL app.current_tenant_id = '0'` (or a special bypass role) to access cross-tenant data.

---

### 5.6 API Key Authentication

Machine-to-machine integrations (EHR systems, billing platforms, appointment aggregators) should authenticate with long-lived API keys, not short-lived JWTs that require a login flow.

**New table:** `api_keys`

```python
class APIKey(Base):
    __tablename__ = "api_keys"
    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(100), nullable=False)          # Human-readable label
    key_hash = Column(String(255), nullable=False)      # bcrypt hash
    key_prefix = Column(String(8), nullable=False)      # First 8 chars for display
    scopes = Column(Text, nullable=False)               # JSON list: ["read:appointments"]
    is_active = Column(Boolean, nullable=False, default=True)
    expires_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    created_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
```

**Authentication:** Client sends `Authorization: ApiKey sk_live_<key>`. The middleware extracts the key, looks up by prefix to find candidates (O(1) on indexed prefix), then bcrypt-verifies the full key against the hash.

**Scopes:** `read:appointments`, `write:appointments`, `read:patients`, `admin:all`. The `get_current_user` dependency returns a `current_user` dict with a `scopes` key that route handlers can check.

**Admin endpoints:**

```
POST   /api/v1/admin/api-keys        — create key (returns raw key once)
GET    /api/v1/admin/api-keys        — list keys (prefix + metadata, never hash)
DELETE /api/v1/admin/api-keys/{id}   — revoke key
```

---

### 5.7 Per-User/Token Rate Limiting

Current rate limiting is purely per source IP. A patient behind a corporate NAT (sharing an IP with thousands of others) could be rate-limited unfairly. A malicious actor with multiple IPs can bypass the limit entirely.

**Add rate limiting by user ID** (for authenticated endpoints) as a second layer:

```python
# app/core/rate_limiter.py
import redis.asyncio as aioredis
from app.core.redis_client import get_redis

async def check_rate_limit(user_id: str, endpoint: str,
                           limit: int = 60, window_seconds: int = 60) -> bool:
    """Returns True if request is allowed, False if rate limited."""
    redis = get_redis()
    key = f"ratelimit:{user_id}:{endpoint}"
    current = await redis.incr(key)
    if current == 1:
        await redis.expire(key, window_seconds)
    return current <= limit
```

Apply in `get_current_user` dependency (or as a separate `Depends` on sensitive endpoints like booking and login):

```python
if not await check_rate_limit(user_id, request.url.path, limit=30, window_seconds=60):
    raise HTTPException(status_code=429, detail="Rate limit exceeded for your account")
```

---

### 5.8 Phase 16 Estimated Effort

| Task | Estimate |
|---|---|
| Password reset flow | 1.5 days |
| Account lockout | 1 day |
| Tenant management API + superadmin role | 2 days |
| PostgreSQL RLS implementation | 2 days |
| API key authentication | 2 days |
| Per-user rate limiting | 0.5 day |
| Migrations (013–016) | 0.5 day |
| Tests | 0.5 day |
| **Phase 16 Total** | **~10 days** |

---

## 6. Phase 17 — Operational Excellence

### 6.1 Objectives

Deliver the infrastructure maturity that separates a working system from a production-grade one: blue-green deployments, PostgreSQL read replica, secrets management, SLA monitoring, and a complete DR test. Estimated effort: **~12 days**.

---

### 6.2 PostgreSQL Read Replica

Phase 9 documented read replica as "Option A or B" but delivered neither.

**Implementation (Docker Compose — Option A):**

```yaml
# docker-compose.yml addition
db-replica:
  image: postgres:16-alpine
  environment:
    - POSTGRES_USER=clinic
    - POSTGRES_PASSWORD=clinicpass
    - POSTGRES_DB=clinic_db
    - PGUSER=replicator
  command: |
    bash -c "
    until PGPASSWORD=replicatorpass pg_basebackup \
      -h db -D /var/lib/postgresql/data \
      -U replicator -P -Xs -R; do
      echo 'Waiting for primary...'
      sleep 2
    done
    postgres
    "
  depends_on:
    db:
      condition: service_healthy
  networks:
    - clinic-net
```

**Primary PostgreSQL configuration** (add to `db` service via `postgresql.conf`):

```sql
-- On primary: create replication slot and user
CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'replicatorpass';
ALTER SYSTEM SET wal_level = replica;
ALTER SYSTEM SET max_wal_senders = 3;
```

**FastAPI dual-engine pattern:**

```python
# app/db/session.py
READ_DATABASE_URL = settings.READ_DATABASE_URL or settings.DATABASE_URL

read_engine = create_async_engine(READ_DATABASE_URL, pool_size=10, max_overflow=5)
read_session_factory = async_sessionmaker(read_engine, class_=AsyncSession,
                                          expire_on_commit=False)

async def get_read_db() -> AsyncSession:
    """Session for read-only operations — routes to replica."""
    async with read_session_factory() as session:
        yield session
```

Update list endpoints (`GET /appointments`, `GET /doctors`, `GET /patients`) and analytics to use `Depends(get_read_db)` instead of `Depends(get_db)`. Write endpoints (POST, PATCH, DELETE) continue using the primary.

---

### 6.3 Secrets Management

The current approach stores secrets in environment variables and Docker Compose files. `k8s/secret.yaml` contains hardcoded base64-encoded secrets — anyone with read access to the Kubernetes manifest has the database password.

**Implementation: External Secrets Operator with HashiCorp Vault**

```yaml
# k8s/external-secret.yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: clinic-scheduler-secrets
  namespace: clinic-scheduler
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend
    kind: ClusterSecretStore
  target:
    name: clinic-scheduler-secrets
    creationPolicy: Owner
  data:
    - secretKey: SECRET_KEY
      remoteRef:
        key: clinic-scheduler/production
        property: SECRET_KEY
    - secretKey: DB_PASSWORD
      remoteRef:
        key: clinic-scheduler/production
        property: DB_PASSWORD
    - secretKey: REDIS_PASSWORD
      remoteRef:
        key: clinic-scheduler/production
        property: REDIS_PASSWORD
```

**Docker Compose equivalent:** Use `docker secret` (swarm mode) or `sops`-encrypted `.env` files for development. The `.env` file should never be committed; the example `.env.example` should document all required variables.

**Secret rotation procedure** — add to `AGENTS.md`:

```bash
# Rotate SECRET_KEY (invalidates all active JWTs — 15-minute grace period)
vault kv put clinic-scheduler/production SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
kubectl rollout restart deployment/clinic-worker
```

---

### 6.4 Blue-Green Deployment

Rolling updates (`maxUnavailable: 0, maxSurge: 1`) work well but have a flaw: during the rollout window, requests can hit both old and new versions. If a schema migration changes response shapes, clients see inconsistent responses.

**Blue-green strategy:**

1. Deploy the new version alongside the old (`clinic-worker-green` deployment, 0 replicas)
2. Run smoke tests against the green deployment via a dedicated service (`clinic-worker-green-svc`)
3. Switch the NGINX ingress / Kubernetes Service selector from `version: blue` to `version: green`
4. Scale down the blue deployment

**Kubernetes implementation:**

```yaml
# k8s/deployment-worker-blue.yaml
metadata:
  name: clinic-worker-blue
  labels:
    version: blue
spec:
  replicas: 3
  selector:
    matchLabels:
      app: clinic-worker
      version: blue
---
# k8s/service-worker.yaml — switch selector to change traffic
spec:
  selector:
    app: clinic-worker
    version: blue  # Change to 'green' to flip traffic
```

**Automation:** A GitHub Actions workflow that:

1. Builds and pushes `clinic-scheduler-worker:green`
2. Deploys green at 0 replicas
3. Runs `pytest tests/integration/ --base-url=http://clinic-worker-green-svc:8000`
4. If tests pass, scales green to 3 replicas and updates Service selector
5. If tests fail, scales green to 0 and alerts the team

---

### 6.5 SLA Monitoring and Error Budget

Connect the Prometheus AlertManager rules (Phase 9) to an error budget dashboard.

**SLA definition:**

| Metric | SLO Target | Error Budget (30d) |
|---|---|---|
| Availability (`GET /health` 200) | 99.9% | 43.2 minutes downtime |
| p95 Latency | < 500ms | 5% of requests may exceed |
| Booking Error Rate | < 1% HTTP 500 | 1% of booking attempts |
| Webhook Delivery Success | > 95% | 5% delivery failures acceptable |

**Grafana SLA dashboard:** Panels for each SLO showing current burn rate and projected error budget exhaustion. Alert when burn rate exceeds 2× the SLO for more than 5 minutes.

**New AlertManager rule:**

```yaml
- alert: ErrorBudgetBurnRateCritical
  expr: |
    (
      sum(rate(http_requests_total{status=~"5.."}[1h]))
      /
      sum(rate(http_requests_total[1h]))
    ) > 0.05
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "Error budget burning at {{ $value | humanizePercentage }} — SLO at risk"
```

---

### 6.6 Full Disaster Recovery Test

Phase 9 delivered a runbook. Phase 17 delivers a **tested and validated** DR procedure.

**DR Test procedure (run quarterly in staging):**

1. Take a manual `pg_dump` backup
2. Shut down all services: `docker compose down -v`
3. Start a fresh environment: `docker compose up -d`
4. Wait for services healthy
5. Restore from backup: `gunzip -c backup.sql.gz | psql ...`
6. Run full test suite: `python -m pytest tests/ -v`
7. Document actual RTO (time from step 2 to all tests passing)
8. Commit timing results to `app/docs/DR_Test_Results.md`

**Target RTO:** < 30 minutes for Docker Compose deployment. < 60 minutes for Kubernetes.

---

### 6.7 NGINX Configuration Hardening

The current `nginx.conf` uses `set $backend http://worker:8000; proxy_pass $backend;` for dynamic resolution, but this pattern disables consistent hashing (you cannot use `hash` directive with variable backends). Every request is effectively round-robined, which is fine functionally but differs from the documented consistent-hashing algorithm.

**Decision and documentation update:**

For the Docker Compose environment, switch explicitly to round-robin and document this in `AGENTS.md`. Consistent hashing is a Kubernetes Ingress-level feature.

```nginx
# docker-compose NGINX: explicit round-robin
resolver 127.0.0.11 valid=5s;
upstream clinic_backend {
    # Round-robin (no hash) for dynamic Docker DNS resolution
    server worker:8000;
}
```

For Kubernetes, the Ingress controller (nginx-ingress) handles load distribution natively. The `nginx.conf` is only relevant for the Docker Compose deployment.

---

### 6.8 Phase 17 Estimated Effort

| Task | Estimate |
|---|---|
| PostgreSQL read replica (Compose + K8s) | 2 days |
| Secrets management (Vault + ExternalSecrets) | 2 days |
| Blue-green deployment pipeline | 2 days |
| SLA dashboard + error budget alerts | 1.5 days |
| Full DR test (staging) + results doc | 1.5 days |
| NGINX config hardening + docs update | 0.5 day |
| Load test at 200 VUs on Linux (post read-replica) | 1 day |
| Final regression: all 162+ tests pass | 1 day |
| **Phase 17 Total** | **~12 days** |

---

## 7. Dependency Map

```
Phase 13 (Bug Fixes)
    │
    ├── FIX-13-A (Refresh Token) ──────────────────────────────────────────┐
    ├── FIX-13-B (Doctor Ownership) → Phase 14 (Doctor-User Link API)      │
    ├── FIX-13-C (Webhook Session) → Phase 15 (Reminder Scheduler)         │
    ├── FIX-13-D (Redis Pool) → Phase 16 (Per-User Rate Limiting)          │
    └── FIX-13-G (Analytics Tenant Guard) → Phase 16 (RLS)                 │
                                                                            │
Phase 14 (Data Layer) ◄────────────────────────────────────────────────────┘
    │
    ├── Migration 010 (Multi-Tenant) ─── PREREQUISITE for prod deployment
    ├── Migration 011 (User-Patient FK) → Phase 15 (for-me endpoint)
    └── Doctor-User Link API → FIX-13-B verification
         │
Phase 15 (Lifecycle Completions)
    │
    ├── Reminder Scheduler (requires email provider configured)
    ├── POST /appointments/for-me (requires user-patient FK from Phase 14)
    └── Frontend completions (requires FIX-13-E + user-patient FK)
         │
Phase 16 (Platform Hardening) ← can run in parallel with Phase 15
    │
    ├── Password Reset (requires email provider)
    ├── Account Lockout (standalone)
    ├── Tenant API + Superadmin (requires Phase 12 multi-tenant)
    ├── PostgreSQL RLS (requires Phase 14 migration 010)
    └── API Keys (standalone)
         │
Phase 17 (Operational Excellence) ← requires Phases 13-16 complete
    │
    ├── Read Replica (requires stable pool_size from FIX-13-F)
    ├── Secrets Management (standalone — infra)
    ├── Blue-Green (requires CI/CD pipeline from Phase 6)
    └── DR Test (requires everything above)
```

---

## 8. Consolidated Risk Register

| Risk | Probability | Impact | Phase | Mitigation |
|---|---|---|---|---|
| Migration 010 fails on existing production data | Medium | Critical | 14 | Run against full production backup copy in staging first; write idempotent upgrade script |
| Refresh token rearchitecture (FIX-13-A) invalidates all existing refresh tokens | High | Medium | 13 | Acceptable — existing refresh tokens are broken anyway (BUG-B). Force re-login at deploy |
| Doctor-user linkage (FIX-13-B) requires admin action to link existing doctors | High | Medium | 13/14 | Provide a bulk-link CLI command: `python -m app.cli.link_doctors` |
| RLS policies block legitimate admin cross-tenant queries | Low | High | 16 | Superadmin role bypasses RLS; test every admin analytics query before enabling RLS |
| Blue-green cutover causes brief session loss | Low | Low | 17 | JWT tokens are stateless; no server-side session to lose |
| Email provider not configured breaks reminder scheduler | High | Low | 15 | Null provider logs but does not error — graceful degradation already implemented |
| Read replica replication lag causes stale reads | Medium | Medium | 17 | Monitor replica lag; set `synchronous_standby_names` for write-critical paths |
| Secret rotation (vault) causes brief auth failures | Low | Medium | 17 | Dual-key validation window during rotation (as documented in AGENTS.md) |
| Account lockout exploited for DoS on specific usernames | Medium | Medium | 16 | Add CAPTCHA after 3 attempts; expose lockout via admin API for manual override |

---

## 9. Definition of Done (Phase 13+)

In addition to all previous DoD gates, every phase from 13 onwards must also pass:

| Gate | Method |
|---|---|
| `ALEMBIC_ENABLED=true docker compose up -d --build` starts cleanly | Automated in CI |
| `alembic downgrade -1` followed by `alembic upgrade head` succeeds | Verified against staging DB |
| All new endpoints return correct responses for both old tokens (no tenant_id) and new tokens (with tenant_id) | Integration tests with both token types |
| No new unhandled `asyncio.create_task` coroutines that use closed database sessions | Code review checklist |
| Frontend cancel, book, and profile actions work end-to-end | Manual E2E test or Playwright |
| Load test at 50 VUs: zero pool_timeout or Redis connection errors | k6 run included in CI |

---

## 10. Milestone Timeline

Assuming a team of 2–3 backend engineers + 1 DevOps + 1 QA.

| Milestone | Phase | Key Outcome | Target |
|---|---|---|---|
| **M13: Production Blockers Resolved** | 13 | All 10 bugs fixed, 162+ tests pass | Week 1–2 |
| **M14a: Alembic Production-Ready** | 14 (first half) | Migration 010 works on fresh + existing DB | Week 3 |
| **M14b: User-Patient Architecture** | 14 (second half) | Registration creates patient, booking works self-service | Week 4 |
| **M15a: Reminders Live** | 15 (first half) | Scheduler running, reminder emails sent in staging | Week 5 |
| **M15b: Frontend Complete** | 15 (second half) | Patient portal fully functional end-to-end | Week 6 |
| **M16a: Auth Hardening** | 16 (first half) | Password reset, account lockout | Week 7 |
| **M16b: Platform Security** | 16 (second half) | Tenant API, RLS, API keys | Week 8–9 |
| **M17a: Infrastructure** | 17 (first half) | Read replica, secrets management | Week 10–11 |
| **M17b: Production Release** | 17 (second half) | Blue-green, DR test, 200 VU load test pass | Week 12–13 |

---

## Final Assessment

**Should you deploy Phase 12 code to production today?**

**No.** BUG-G (Alembic migrations missing tenant columns) means production deployment with `ALEMBIC_ENABLED=true` will fail at startup. BUG-B (TypeError in refresh token expiry) means `POST /auth/refresh` always returns 500. BUG-D (webhook session closed) means every webhook delivery silently fails. BUG-C (doctor ownership never matches) means the doctor mobile API is unusable for its intended users.

**Phase 13 and Phase 14 are mandatory pre-production gates.** Phases 15–17 are high-value enhancements that can be released incrementally after a stable production baseline is established.

The system is architecturally sound and feature-rich. The remaining work is primarily correcting implementation defects in the most recently delivered phases, completing the data model architecture that was designed but only partially built, and adding the operational maturity expected of a production medical service.

---

*Prepared by: Senior Engineering Lead | Clinic Scheduler Project | 2026-05-22*
*Next review: After Phase 13 completion — reassess Phase 14 scope based on migration test results.*
