# Phase 16 — Platform Hardening

## Status: Active (Sub-Phase 16-E Complete)

---

## Sub-Phase 16-A: Password Reset ✅

### Objective
Provide a secure forgot-password / reset-password flow using time-limited, single-use tokens to allow users to regain access to their accounts without administrator intervention.

### Changes

#### 1. Model (`app/models/__init__.py`)
Added three columns to `User`:
```python
password_reset_jti = Column(String(32), nullable=True, index=True)
password_reset_hash = Column(String(255), nullable=True)
password_reset_expires_at = Column(DateTime, nullable=True)
```

#### 2. Router (`app/api/v1/routers/auth.py`)
Two new endpoints:
- **`POST /auth/forgot-password`** — Accepts email, looks up patient → user, generates reset token (if user found), always returns 200 to prevent email enumeration
- **`POST /auth/reset-password`** — Accepts `{token, new_password}`, validates JTI lookup + bcrypt hash + expiry, resets password, clears lockout fields, writes audit log

#### 3. Security (`app/core/security.py`)
- `create_password_reset_token()` → returns `(full_token, jti, token_hash)`. Client token format: `{jti}.{raw_secret}`
- `verify_password_reset_token()` → splits token, verifies secret portion against stored bcrypt hash
- JTI enables O(1) DB lookup; bcrypt hash prevents DB-leak-based token forgery

#### 4. Email (`app/core/email.py`)
- `send_password_reset_email()` — sends token to user's email via configured `EmailService` (Null/SMTP/SendGrid)
- In dev (NullEmailService): token is logged to stdout; integration tests inject token directly via DB

#### 5. Database
```sql
ALTER TABLE users ADD COLUMN password_reset_jti VARCHAR(32);
ALTER TABLE users ADD COLUMN password_reset_hash VARCHAR(255);
ALTER TABLE users ADD COLUMN password_reset_expires_at TIMESTAMP WITHOUT TIME ZONE;
CREATE INDEX ix_users_password_reset_jti ON users (password_reset_jti);
```

#### 6. Alembic Migration (`alembic/versions/015_password_reset.py`)
Adds three columns + index, with conditional logic to skip if columns already exist.

### Key Design Decisions
- **Always 200 on forgot-password**: Prevents email enumeration attacks
- **JTI + bcrypt dual validation**: JTI for O(1) lookup, bcrypt hash ensures stolen DB doesn't reveal usable tokens
- **Clears lockout on reset**: Successful password reset also resets `failed_login_attempts = 0` and `locked_until = None`
- **Audit log**: Every successful reset creates a `password_reset` audit entry

### Files Changed
| File | Change |
|---|---|
| `app/models/__init__.py` | +`password_reset_jti`, +`password_reset_hash`, +`password_reset_expires_at` on `User` |
| `app/api/v1/routers/auth.py` | +`POST /auth/forgot-password`, +`POST /auth/reset-password` |
| `app/core/security.py` | +`create_password_reset_token()`, +`verify_password_reset_token()` |
| `app/core/email.py` | +`send_password_reset_email()` |
| `alembic/versions/015_password_reset.py` | New migration |
| `tests/integration/test_auth.py` | +4 password reset tests |

### Tests
| Test | Description |
|---|---|
| `test_forgot_password_always_returns_200` | Unknown email still returns 200 |
| `test_reset_password_full_flow` | Register → inject token → reset → login with new password → old password rejected |
| `test_reset_password_invalid_token` | Bad token returns 400 |
| `test_reset_password_bad_format` | No dot separator returns 400 |
- Full suite: **146 passed, 0 skipped**
- No regressions
- Ruff format: clean

---

## Sub-Phase 16-B: Account Lockout ✅

### Objective
Prevent brute-force password guessing by locking user accounts after 5 consecutive failed login attempts for 15 minutes.

### Changes

#### 1. Model (`app/models/__init__.py`)
Added two columns to `User`:
```python
failed_login_attempts = Column(Integer, nullable=False, default=0)
locked_until = Column(DateTime, nullable=True)
```

#### 2. Router (`app/api/v1/routers/auth.py`)
Updated `POST /auth/login` with lockout logic:
- **Pre-check**: If `user.locked_until > datetime.utcnow()`, return HTTP 429 with lockout timestamp
- **On wrong password**: Increment `failed_login_attempts`; if ≥ `MAX_LOGIN_ATTEMPTS (5)`, set `locked_until = now + 15min` and write audit log entry (`action="account_locked"`, `outcome="warning"`)
- **On success**: Reset `failed_login_attempts = 0` and `locked_until = None`
- Uses `await db.commit()` before raising `HTTPException` (the `get_db()` context manager would otherwise rollback on exception)
- Constants: `MAX_LOGIN_ATTEMPTS = 5`, `LOCKOUT_MINUTES = 15`

#### 3. Database
```sql
ALTER TABLE users ADD COLUMN failed_login_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN locked_until TIMESTAMP WITHOUT TIME ZONE;
```

#### 4. Alembic Migration (`alembic/versions/014_account_lockout.py`)
```python
def upgrade():
    op.add_column("users", sa.Column("failed_login_attempts", sa.Integer(), ...))
    op.add_column("users", sa.Column("locked_until", sa.DateTime(), nullable=True))
```

### Files Changed
| File | Change |
|---|---|
| `app/models/__init__.py` | +`failed_login_attempts`, +`locked_until` on `User` |
| `app/api/v1/routers/auth.py` | Lockout check, counter increment, audit logging |
| `alembic/versions/014_account_lockout.py` | New migration |
| `tests/integration/test_auth.py` | +3 lockout tests |

### Tests
| Test | Description |
|---|---|
| `test_locks_after_five_failed_attempts` | 5 wrong passwords → 401, 6th → 429 with "locked" message |
| `test_successful_login_resets_failed_attempts` | 4 wrong, 1 correct → 200, then wrong again → 401 (counter reset) |
| `test_lockout_does_not_affect_other_users` | Locked user's 429 doesn't prevent other users from logging in |
- Full suite: **142 passed, 0 skipped**
- No regressions
- Ruff format: clean

---

## Sub-Phase 16-C: Tenant Management API ✅

### Objective
Introduce a superadmin role and provide full CRUD API for managing tenants, enabling platform operators to create, read, update, and deactivate tenant organizations.

### Changes

#### 1. Model (`app/models/__init__.py`)
Added `SUPERADMIN = "superadmin"` to `UserRole` enum:
```python
class UserRole(str, enum.Enum):
    PATIENT = "patient"
    DOCTOR = "doctor"
    ADMIN = "admin"
    SUPERADMIN = "superadmin"
```

#### 2. Database (`app/db/session.py`)
- `init_db()` now calls `_add_enum_value_if_not_exists()` to add `'superadmin'` to the existing `userrole` ENUM type without dropping/recreating it
- Catches `IntegrityError` / `ProgrammingError` silently (idempotent)

#### 3. Router (`app/api/v1/routers/admin.py`)
- `ADMIN_ROLES = {"admin", "superadmin"}` — existing admin endpoints now accept both roles
- `_require_superadmin()` — new guard for tenant management endpoints (only superadmin passes)
- Five new tenant endpoints:

| Method | Path | Description |
|---|---|---|
| `GET` | `/admin/tenants` | List all tenants (paginated) |
| `GET` | `/admin/tenants/{id}` | Get tenant by ID |
| `POST` | `/admin/tenants` | Create new tenant (unique slug) |
| `PATCH` | `/admin/tenants/{id}` | Update tenant name/active status |
| `DELETE` | `/admin/tenants/{id}` | Deactivate tenant (cannot deactivate `"default"`) |

#### 4. Alembic Migration (`alembic/versions/016_add_superadmin_role.py`)
Adds `'superadmin'` to `userrole` ENUM via `ALTER TYPE userrole ADD VALUE 'superadmin'`.

### Key Design Decisions
- **Superadmin scoping**: Superadmin is cross-tenant — they see ALL tenants, unconstrained by `X-Tenant-ID`
- **Regular admin unchanged**: Existing `_require_admin` now uses `ADMIN_ROLES` set — both `admin` and `superadmin` pass
- **Default tenant protection**: Cannot deactivate tenant with slug `"default"` — prevents accidental orphaned data
- **Soft-delete only**: Tenant deactivation sets `is_active = False`; data is preserved
- **Audit log**: All tenant mutations (create, update, deactivate) create audit log entries

### Files Changed
| File | Change |
|---|---|
| `app/models/__init__.py` | +`SUPERADMIN` in `UserRole` enum |
| `app/db/session.py` | +`_add_enum_value_if_not_exists()`, updated `init_db()` |
| `app/api/v1/routers/admin.py` | +`ADMIN_ROLES`, `_require_superadmin`, 5 tenant endpoints |
| `alembic/versions/016_add_superadmin_role.py` | New migration |
| `tests/conftest.py` | +`superadmin_token` fixture |
| `tests/integration/test_tenant_management.py` | New test file (12 tests) |

### Tests
| Test | Description |
|---|---|
| `test_list_tenants_superadmin` | Superadmin can list tenants |
| `test_list_tenants_requires_superadmin` | Regular admin gets 403 |
| `test_list_tenants_requires_auth` | Unauthenticated gets 403 |
| `test_create_tenant` | Superadmin creates tenant with unique slug |
| `test_create_tenant_duplicate_slug` | Duplicate slug returns 409 |
| `test_create_tenant_requires_superadmin` | Regular admin gets 403 |
| `test_get_tenant` | Fetch tenant by ID |
| `test_get_tenant_not_found` | Non-existent ID returns 404 |
| `test_update_tenant` | PATCH name/slug/is_active |
| `test_deactivate_tenant` | DELETE sets is_active=False |
| `test_cannot_deactivate_default_tenant` | Default tenant protected (400) |
| `test_superadmin_can_access_admin_endpoints` | Superadmin can use existing admin endpoints |
- Full suite: **196 passed, 5 skipped, 0 failed**
- No regressions
- Ruff format: clean

---

## Sub-Phase 16-F: Per-User Rate Limiting ✅

### Objective
Prevent individual user abuse by enforcing a sliding-window rate limit (100 requests per 60 seconds per user) using Redis, with public endpoints (login, register, forgot-password, health) exempted.

### Changes

#### 1. Rate Limiter (`app/core/rate_limiter.py`)
New module implementing `RateLimitMiddleware` (ASGI middleware via `BaseHTTPMiddleware`):

- **Sliding window** using Redis sorted sets (`ZADD` / `ZREMRANGEBYSCORE` / `ZCARD`)
- Extracts user ID from JWT token in `Authorization` header
- **Public paths** exempted: `/auth/login`, `/auth/register`, `/auth/forgot-password`, `/auth/reset-password`, `/health`, `/docs`, `/redoc`, `/openapi.json`
- Returns **HTTP 429** with `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` headers when exceeded
- Adds `X-RateLimit-*` headers to all authenticated responses within limits
- Catches all Redis errors gracefully — the request passes through if Redis is down

#### 2. Middleware Wiring (`app/main.py`)
```python
from app.core.rate_limiter import RateLimitMiddleware
app.add_middleware(RateLimitMiddleware)
```
Added after `TenantMiddleware` to ensure JWT claims are available.

### Key Design Decisions
- **Middleware vs dependency**: Middleware runs before route handlers, so rate-limited users don't consume app resources
- **Redis sorted sets**: Provides accurate sliding window (not fixed window) — each request adds a timestamp member
- **Graceful degradation**: If Redis is unreachable, the request passes through (logged at ERROR level)
- **Per-user, not per-IP**: Works correctly across NGINX round-robin (3 workers) since Redis is shared
- **Configurable constants**: `RATE_LIMIT_REQUESTS = 100`, `RATE_LIMIT_WINDOW = 60` at module top

### Files Changed
| File | Change |
|---|---|
| `app/core/rate_limiter.py` | New module — sliding window rate limiter middleware |
| `app/main.py` | +`RateLimitMiddleware` import and registration |
| `tests/integration/test_rate_limiting.py` | New test file (5 tests) |

### Tests
| Test | Description |
|---|---|
| `test_rate_limit_headers_present` | Authenticated response includes X-RateLimit-* headers |
| `test_rate_limit_remaining_decreases` | Consecutive requests decrement remaining count |
| `test_public_endpoints_not_rate_limited` | Login endpoint doesn't have rate limit headers |
| `test_rate_limit_exceeded_returns_429` | 101st request returns 429 with Retry-After |
| `test_rate_limit_per_user_independent` | User A's exhaustion doesn't affect User B |
- Full suite: **201 passed, 5 skipped, 0 failed**
- No regressions
- Ruff format: clean

---

---

## Sub-Phase 16-E: API Key Authentication ✅

### Objective
Provide machine-to-machine API key authentication as a fallback to JWT-based auth, with admin CRUD endpoints for managing keys.

### Changes

#### 1. Model (`app/models/__init__.py`)
- Added `ApiKey` model: `tenant_id`, `key_hash`, `key_prefix`, `name`, `role`, `is_active`, `created_by`, `expires_at`, `created_at`
- Uses existing `userrole` ENUM for the `role` column with `create_type=False`

#### 2. Endpoints (`app/api/v1/routers/admin.py`)
- `POST /api/v1/admin/api-keys` — Create new API key (returns `raw_key` once)
- `GET /api/v1/admin/api-keys` — List API keys (paginated, no `raw_key`)
- `PATCH /api/v1/admin/api-keys/{id}` — Activate/deactivate
- `DELETE /api/v1/admin/api-keys/{id}` — Delete API key
- All endpoints require `admin` or `superadmin` role
- Audit logging on all operations

#### 3. Auth Logic (`app/api/v1/dependencies.py`)
- `get_current_user` checks `X-API-Key` header before falling back to JWT `HTTPBearer`
- `_get_api_key_user()` looks up key by prefix (`key_prefix = api_key[:8]`), verifies bcrypt hash, checks expiry
- Returns user dict with `_auth_method: "api_key"` for downstream RBAC

#### 4. Rate Limiter Fix (`app/core/rate_limiter.py`)
- Fixed double `call_next` bug: exception after the first `call_next` in the try block caused a second `call_next` in the except block, which hung because the ASGI stream was already consumed
- Added `called_next` boolean flag; only calls `call_next` in the except block if it wasn't already called

#### 5. Migration (`alembic/versions/017_api_keys.py`)
- Creates `api_keys` table with required indexes

### Bug Fixes
- **504 Gateway Timeout**: Fixed rate limiter double `call_next` hang. The `RateLimitMiddleware.dispatch()` called `call_next(request)` in the except block even when the exception occurred after `call_next` already ran, consuming the ASGI stream. The second call hung until nginx timeout (10s).
- **`AttributeError: 'str' object has no attribute 'value'`**: ENUM columns read from PostgreSQL return raw strings, not enum members. Changed `api_key.role.value` to `api_key.role.value if hasattr(api_key.role, 'value') else api_key.role` in all `create_api_key`, `list_api_keys`, and `patch_api_key` response serializers.

### Tests
- 10 integration tests: create (with/without expiry), list, RBAC (admin required), deactivate, delete, authenticate with valid key, invalid key rejected, deactivated key rejected, no auth rejected
- Full suite: **211 passed, 5 skipped, 0 failed**

---

## Upcoming Sub-Phases

| Sub-Phase | Status | Description |
|---|---|---|
| 16-D: PostgreSQL RLS | Pending | Row-level security policies |
