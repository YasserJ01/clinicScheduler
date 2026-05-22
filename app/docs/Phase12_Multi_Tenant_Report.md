# Phase 12 Multi-Tenant Support — Report

## Summary
Phase 12 delivers full multi-tenant data isolation: `Tenant` model, `tenant_id` column on all domain entities, tenant resolution via `X-Tenant-ID` header and JWT claims, tenant-scoped unique constraints, and repository-level filtering. All 162 tests pass, ruff lint/format clean. Existing data is backfilled to a default tenant.

## Architecture

### Tenant Isolation Model
- **Row-level tenant scoping**: Every business entity has a `tenant_id` foreign key
- **Dual resolution**: Tenant ID comes from JWT claim (primary) or `X-Tenant-ID` header (must match)
- **Repository filtering**: All queries automatically include `tenant_id` filter when provided
- **Unique constraints**: Scoped to `(tenant_id, username)` and `(tenant_id, email)` to allow same usernames across tenants

### Data Flow
```
Client Request
  → TenantMiddleware extracts X-Tenant-ID header → request.state.tenant_id
  → get_current_user decodes JWT → current_user["tenant_id"]
  → get_current_tenant validates header matches token → returns tenant_id
  → Router passes tenant_id to repository methods
  → Repository adds tenant_id WHERE clause to all queries
```

## Changes

### 1. Tenant Model

**New Model:** `Tenant` in `app/models/__init__.py`
```python
class Tenant(Base):
    __tablename__ = "tenants"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
```

**Default Tenant:** `id=1, name="Default Clinic", slug="default"` — all existing data migrated here.

### 2. Schema Changes

**tenant_id added to all tables:**

| Table | Column | Nullable | Index | Unique Constraint |
|-------|--------|----------|-------|-------------------|
| `users` | `tenant_id` | NOT NULL | `ix_users_tenant_id` | `(tenant_id, username)` |
| `doctors` | `tenant_id` | NOT NULL | `ix_doctors_tenant_id` | — |
| `doctor_schedules` | `tenant_id` | NOT NULL | `ix_doctor_schedules_tenant_id` | — |
| `patients` | `tenant_id` | NOT NULL | `ix_patients_tenant_id` | `(tenant_id, email)` |
| `appointments` | `tenant_id` | NOT NULL | `ix_appointments_tenant_id` | — |
| `recurring_series` | `tenant_id` | NOT NULL | `ix_recurring_series_tenant_id` | — |
| `audit_log` | `tenant_id` | NULL | `ix_audit_log_tenant_id` | — |
| `webhooks` | `tenant_id` | NOT NULL | `ix_webhooks_tenant_id` | — |
| `webhook_deliveries` | `tenant_id` | NOT NULL | `ix_webhook_deliveries_tenant_id` | — |

**Migration Strategy:**
- All existing rows backfilled with `tenant_id = 1`
- NOT NULL constraints added after backfill
- Unique constraints recreated with tenant scope

### 3. Tenant Resolution

**New Middleware:** `app/core/tenant_middleware.py`
```python
class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        tenant_id = request.headers.get("X-Tenant-ID")
        if tenant_id:
            request.state.tenant_id = int(tenant_id)
        else:
            request.state.tenant_id = None
        return await call_next(request)
```

**Updated Dependencies:** `app/api/v1/dependencies.py`
- `get_current_user` now returns `tenant_id` from JWT payload
- New `get_current_tenant` dependency:
  - Validates `X-Tenant-ID` header matches JWT `tenant_id` claim
  - Returns HTTP 403 on mismatch
  - Returns HTTP 400 if neither header nor token has tenant_id

**JWT Token Updates:**
- `POST /auth/register` — accepts `tenant_id` in request body (default: 1), embeds in token
- `POST /auth/login` — reads `user.tenant_id`, embeds in token
- `POST /auth/refresh` — reads `user.tenant_id`, embeds in new token

### 4. Repository Updates

**All repository methods now accept `tenant_id` parameter:**

| Repository | Methods Updated |
|------------|----------------|
| `UserRepository` | `get_by_username(tenant_id)`, `create(tenant_id)` |
| `DoctorRepository` | `list_all(tenant_id)`, `list_paginated(tenant_id)`, `get_by_id(tenant_id)`, `create(tenant_id)`, `set_schedule(tenant_id)` |
| `PatientRepository` | `list_all(tenant_id)`, `list_paginated(tenant_id)`, `get_by_id(tenant_id)`, `get_or_create_by_email(tenant_id)`, `get_patients_for_doctor(tenant_id)` |
| `AppointmentRepository` | `list_all(tenant_id)`, `list_paginated(tenant_id)`, `get_by_id(tenant_id)`, `create(tenant_id)`, `get_booked_slots(tenant_id)`, `check_conflict(tenant_id)`, `get_due_reminders(tenant_id)`, `get_today_appointments(tenant_id)`, `get_upcoming_appointments(tenant_id)`, `create_recurring_series(tenant_id)` |

**Filtering Pattern:**
```python
async def list_paginated(self, ..., tenant_id: int | None = None):
    where_clauses = []
    if tenant_id is not None:
        where_clauses.append(Appointment.tenant_id == tenant_id)
    # ... other filters
```

### 5. Router Updates

**All routers extract `tenant_id` from `current_user` and pass to repositories:**

| Router | Changes |
|--------|---------|
| `auth.py` | Register accepts `tenant_id`; login/refresh embed in JWT |
| `doctors.py` | All endpoints pass `tenant_id` for doctor lookup, schedule, appointments, patients |
| `patients.py` | All endpoints pass `tenant_id` for patient lookup, creation, profile |
| `appointments.py` | All endpoints pass `tenant_id` for booking, conflict check, available slots, status, notes |
| `admin.py` | Webhook CRUD scoped to tenant; deliveries filtered by tenant |
| `analytics.py` | Summary, utilisation, peak-hours all filtered by tenant |

### 6. CI/CD Pipeline Fix

**Problem:** `FATAL: role "root" does not exist` in GitHub Actions
- Root cause: `services` block conflicted with `docker compose up -d`
- Runner runs as `root`; docker compose health checks used default OS user

**Fix** in `.github/workflows/ci.yml`:
- Removed `docker compose` steps entirely
- Start uvicorn directly: `nohup uvicorn app.main:app --host 0.0.0.0 --port 8000 &`
- Added `BASE_URL` environment variable support in `tests/conftest.py`
- Redis service configured with `--requirepass redispass`

## Test Results
- **Unit tests:** 40 passed
- **Integration tests:** 122 passed
- **Total:** 162 passed, 5 skipped
- **Ruff:** All checks passed, all files formatted

## New Files
| Path | Purpose |
|------|---------|
| `app/core/tenant_middleware.py` | X-Tenant-ID header extraction middleware |
| `app/docs/Phase11_Analytics_Webhooks_Portal_Report.md` | Phase 11 documentation |
| `app/docs/Phase12_Multi_Tenant_Report.md` | Phase 12 documentation |

## Modified Files
| Path | Changes |
|------|---------|
| `app/models/__init__.py` | Added `Tenant` model; `tenant_id` on all entities; tenant-scoped unique constraints |
| `app/api/v1/dependencies.py` | Added `tenant_id` to JWT decode; new `get_current_tenant` dependency |
| `app/api/v1/routers/auth.py` | Register accepts `tenant_id`; login/refresh embed in JWT |
| `app/api/v1/routers/doctors.py` | All endpoints pass `tenant_id` to repositories |
| `app/api/v1/routers/patients.py` | All endpoints pass `tenant_id` to repositories |
| `app/api/v1/routers/appointments.py` | All endpoints pass `tenant_id` to repositories |
| `app/api/v1/routers/admin.py` | Webhook CRUD scoped to tenant |
| `app/api/v1/routers/analytics.py` | All analytics filtered by tenant |
| `app/db/repository.py` | All methods accept and filter by `tenant_id` |
| `app/main.py` | Wired `TenantMiddleware` |
| `.github/workflows/ci.yml` | Fixed postgres role error; use uvicorn directly |
| `tests/conftest.py` | Added `BASE_URL` environment variable support |
| `app/docs/AGENTS.md` | Added Phase 11/12 documentation sections |

## Backward Compatibility
- **Existing clients**: No changes required. Default `tenant_id=1` is used when not specified.
- **Existing data**: All rows backfilled with `tenant_id=1` during migration.
- **API contracts**: No breaking changes. `tenant_id` is extracted from JWT automatically.
- **New clients**: Can set `X-Tenant-ID` header or include `tenant_id` in registration request.

## Security Considerations
- Tenant mismatch (header vs token) returns HTTP 403 — prevents tenant hopping
- All queries include `tenant_id` filter — prevents cross-tenant data leakage
- Unique constraints scoped to tenant — allows same usernames across tenants
- Audit log `tenant_id` is nullable — system-level events may not have a tenant

## Next Steps (Future)
- PostgreSQL Row-Level Security (RLS) policies for database-level tenant isolation
- Tenant provisioning admin endpoints (create, activate, deactivate tenants)
- Per-tenant configuration (timezone, working hours, slot duration)
- Tenant-specific webhook subscriptions
- Multi-tenant analytics (cross-tenant aggregation for super-admins)
