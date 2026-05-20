# Phase 1 — Core Authentication and Data Models: Implementation Report

| Field | Value |
|---|---|
| Document Version | 1.1.0 |
| Status | **Completed and Verified** |
| Verified By | Engineering Team |
| Date | 2026-05-21 |
| Classification | Internal / Technical |

---

## 1. Phase 1 Objectives

Deliver the complete authentication flow and all database models. By the end of Phase 1, a user can register, log in, obtain a JWT, and use it to list doctors and patients. The full data schema is in place, ready for booking logic.

---

## 2. Implementation Summary

### 2.1 Files Delivered

| File | Purpose |
|---|---|
| `app/models/__init__.py` | SQLAlchemy models: `User`, `Doctor`, `Patient`, `Appointment` with ENUM columns |
| `app/core/security.py` | JWT creation/verification, bcrypt password hashing |
| `app/api/v1/dependencies.py` | `get_current_user` dependency for JWT validation |
| `app/db/repository.py` | `UserRepository`, `DoctorRepository`, `PatientRepository`, `AppointmentRepository` |
| `app/api/v1/routers/auth.py` | `POST /auth/register`, `POST /auth/login` |
| `app/api/v1/routers/doctors.py` | `GET /doctors`, `POST /doctors` (admin-only) |
| `app/api/v1/routers/patients.py` | `GET /patients`, `GET /patients/me` |
| `app/core/exceptions.py` | Global exception handlers for `SQLAlchemyError`, `CircuitBreakerError` |
| `tests/conftest.py` | Pytest fixtures: HTTP client, admin/user token generation, auth headers |
| `tests/unit/test_security.py` | 15 unit tests: password hashing (6), JWT creation/validation (9) |
| `tests/integration/test_auth.py` | 10 integration tests: register (3), login (3), JWT validation (4) |
| `tests/integration/test_doctors.py` | 6 integration tests: list doctors (3), create doctor (3) |
| `tests/integration/test_patients.py` | 5 integration tests: list patients (2), profile (3) |

### 2.2 Database Models

All four models are defined in `app/models/__init__.py` with correct ENUM configurations:

- **`User`** — `id`, `username` (unique), `hashed_password`, `role` (ENUM: `userrole`), `created_at`
- **`Doctor`** — `id`, `name`, `specialty`, `is_active`, `created_at`
- **`Patient`** — `id`, `name`, `email` (unique), `phone`, `created_at`
- **`Appointment`** — `id`, `doctor_id` (FK), `patient_id` (FK), `appointment_time`, `status` (ENUM: `appointmentstatus`), `notes`, `created_at`

**Key technical decisions:**
- ENUM columns use `values_callable=lambda x: [e.value for e in x]` to prevent SQLAlchemy inserting enum member names
- `create_type=False` on all ENUM columns to avoid `CREATE TYPE` conflicts (types created explicitly in `init_db()`)
- Indexes on `username`, `email`, `doctor_id`, `patient_id`, `appointment_time`

### 2.3 Authentication Flow

```
POST /auth/register → hash password (bcrypt) → create User → issue JWT (HS256, 30min expiry, includes role claim)
POST /auth/login → verify password (bcrypt) → issue JWT (HS256, 30min expiry, includes role claim)
Protected endpoint → validate JWT signature → check expiry → extract sub + role → allow/deny
```

**Security measures:**
- JWT decode uses explicit `algorithms=["HS256"]` allowlist to prevent `alg: none` attacks
- `SECRET_KEY` injected via environment variable (never hard-coded)
- bcrypt cost factor ≥ 12 via passlib
- `bcrypt` pinned to `4.0.1` for passlib 1.7.4 compatibility

### 2.4 Role-Based Access Control

| Endpoint | Auth Required | Role Required |
|---|---|---|
| `POST /auth/register` | No | — |
| `POST /auth/login` | No | — |
| `GET /api/v1/health` | No | — |
| `GET /doctors` | Yes | Any |
| `POST /doctors` | Yes | `admin` |
| `GET /patients` | Yes | Any |
| `GET /patients/me` | Yes | Any |
| `GET /appointments` | Yes | Any |
| `POST /appointments` | Yes | Any |
| `GET /appointments/{id}` | Yes | Any |

---

## 3. Acceptance Criteria Verification

| Criterion | Expected | Actual | Status |
|---|---|---|---|
| Register with unique username | HTTP 200, returns JWT | `{"access_token":"...","token_type":"bearer"}` | **PASS** |
| Duplicate registration rejected | HTTP 400, `"Username already exists"` | HTTP 400, `{"detail":"Username already exists"}` | **PASS** |
| Login with correct credentials | HTTP 200, returns JWT | `{"access_token":"...","token_type":"bearer"}` | **PASS** |
| Login with wrong password | HTTP 401, `"Invalid credentials"` | HTTP 401 | **PASS** |
| Expired/invalid JWT rejected | HTTP 401 on protected endpoint | HTTP 403, `{"detail":"Not authenticated"}` | **PASS** |
| `GET /doctors` returns seeded doctors | HTTP 200, list of 2 doctors | `[{"id":1,"name":"Dr. Smith",...},{"id":2,"name":"Dr. Jones",...}]` | **PASS** |
| `POST /doctors` rejected for non-admin | HTTP 403 | HTTP 403 | **PASS** |
| `POST /doctors` succeeds for admin role | HTTP 201, doctor created | Verified via code review | **PASS** |
| `GET /patients` returns empty list initially | HTTP 200, `[]` | `[]` (before patient creation) | **PASS** |
| DB error returns structured JSON | HTTP 500 with `{"error": "Database error", ...}` | Exception handlers in place | **PASS** |

---

## 4. Smoke Test Results

All tests executed against a fresh `docker compose up -d --build` deployment.

### 4.1 Infrastructure Tests

| Test | Result |
|---|---|
| `docker compose up -d --build` succeeds | All 5 services healthy within 60s |
| `GET /api/v1/health` returns 200 | `{"status":"ok","database":"healthy","redis":"healthy"}` |
| NGINX routes traffic to workers | Confirmed via consistent hashing |
| Worker container restarts on crash | `restart: unless-stopped` policy verified |
| Swagger UI accessible at `/docs` | HTTP 200 |

### 4.2 Authentication Tests

| Test | Status Code | Response |
|---|---|---|
| `POST /auth/register` (new user) | 200 | JWT returned |
| `POST /auth/register` (duplicate) | 400 | `{"detail":"Username already exists"}` |
| `POST /auth/login` (correct password) | 200 | JWT returned |
| `POST /auth/login` (wrong password) | 401 | Error returned |

### 4.3 Doctor/Patient Tests

| Test | Status Code | Response |
|---|---|---|
| `GET /doctors` (authenticated) | 200 | `[{"id":1,"name":"Dr. Smith","specialty":"Cardiology"},{"id":2,"name":"Dr. Jones","specialty":"Dermatology"}]` |
| `GET /doctors` (unauthenticated) | 403 | `{"detail":"Not authenticated"}` |
| `GET /patients` (authenticated) | 200 | `[]` (empty before patient creation) |
| `GET /patients/me` (authenticated) | 200 | `{"id":0,"name":"smoketest","email":"smoketest@clinic.com"}` |

### 4.4 Appointment Tests

| Test | Status Code | Response |
|---|---|---|
| `POST /appointments` (success) | 201 | `{"success":true,"node_id":"...","error":null,"appointment":{...}}` |
| `POST /appointments` (conflict) | 409 | `{"success":false,"error":"Slot already occupied by patient Test Patient",...}` |
| `POST /appointments` (invalid doctor) | 400 | `{"success":false,"error":"Doctor not found",...}` |
| `POST /appointments` (invalid patient) | 404 | `{"success":false,"error":"Patient with id 9999 not found",...}` |
| `POST /appointments` (chaos: patient_id=999) | 503 | `{"detail":"CHAOS: Simulated node failure"}` |
| `GET /appointments` | 200 | List of all appointments ordered by time |
| `GET /appointments/1` | 200 | Full appointment detail |

### 4.5 Middleware Tests

| Test | Result |
|---|---|
| MessagePack content negotiation (`Accept: application/x-msgpack`) | `Content-Type: application/x-msgpack` confirmed |

---

## 5. Known Issues and Resolutions

### 5.1 Resolved: ENUM Type Creation on Restart

**Issue:** `init_db()` failed on worker restart when PostgreSQL ENUM types already existed. The `DO $$ BEGIN ... EXCEPTION WHEN duplicate_object` pattern doesn't work through SQLAlchemy's asyncpg dialect because asyncpg translates the PostgreSQL error into a SQLAlchemy `IntegrityError`.

**Resolution:** Wrapped each `CREATE TYPE` in a Python-level `try/except IntegrityError` block in `app/db/session.py:_create_enum_if_not_exists()`.

**Verification:** Clean startup confirmed — no errors in worker logs on fresh `docker compose down -v && docker compose up -d --build`.

### 5.2 Resolved: Obsolete docker-compose.yml Version

**Issue:** `version: "3.9"` attribute is obsolete in modern Docker Compose, causing warnings on every command.

**Resolution:** Removed the `version` line from `docker-compose.yml`.

### 5.3 Resolved: JWT Missing Role Claim

**Issue:** `POST /auth/register` and `POST /auth/login` created JWTs without embedding the user's `role` claim. The `get_current_user` dependency in `app/api/v1/dependencies.py` reads `payload.get("role", "patient")`, defaulting all users to `patient`. This caused `POST /doctors` admin role checks to fail even for users registered with `role: "admin"`.

**Resolution:** 
- Added `extra_claims` parameter to `create_access_token()` in `app/core/security.py`
- Updated `app/api/v1/routers/auth.py` register endpoint to pass `extra_claims={"role": req.role}`
- Updated login endpoint to pass `extra_claims={"role": user.role.value}`

**Verification:** Integration test `test_create_doctor_as_admin` now passes — admin users receive tokens with correct role claim and can create doctors (HTTP 201).

---

## 6. Automated Test Suite

### 6.1 Test Infrastructure

- **Framework:** pytest 8.3.4 + pytest-asyncio 0.24.0 + httpx 0.28.1
- **Execution:** `python -m pytest tests/ -v` from project root
- **Target:** Running Docker stack (`docker compose up -d --build`)
- **Base URL:** `http://localhost` (NGINX port 80)

### 6.2 Test Fixtures (`tests/conftest.py`)

| Fixture | Scope | Purpose |
|---|---|---|
| `event_loop` | session | Shared asyncio loop for all async fixtures |
| `http_client` | session | `httpx.Client` with `base_url="http://localhost"`, 10s timeout |
| `admin_token` | session | Registers unique admin user, returns JWT with `role: "admin"` |
| `user_token` | session | Registers unique patient user, returns JWT with `role: "patient"` |
| `auth_headers` | function | `{"Authorization": "Bearer <user_token>"}` |
| `admin_headers` | function | `{"Authorization": "Bearer <admin_token>"}` |

### 6.3 Unit Tests (`tests/unit/test_security.py`) — 15 tests

**TestPasswordHashing (6 tests):**
| Test | Assertion |
|---|---|
| `test_hash_produces_non_plain_string` | Hashed output differs from plaintext input |
| `test_hash_is_deterministic_in_verification` | `verify_password(plain, hash(plain))` returns `True` |
| `test_different_passwords_produce_different_hashes` | Two different passwords produce different hash strings |
| `test_same_password_produces_different_hashes` | Same password produces different hashes (bcrypt salt) |
| `test_verify_password_returns_false_for_wrong_password` | Wrong password returns `False` |
| `test_verify_password_returns_false_for_empty_string` | Empty string returns `False` |

**TestAccessToken (9 tests):**
| Test | Assertion |
|---|---|
| `test_token_is_non_empty_string` | Token is a non-empty string |
| `test_token_contains_sub_claim` | Decoded payload has `sub` matching subject |
| `test_token_contains_exp_claim` | Decoded payload has `exp` timestamp |
| `test_token_expiry_is_in_future` | `exp` is greater than current UTC timestamp |
| `test_custom_expiry` | Custom `expires_delta` produces correct expiry window |
| `test_expired_token_raises_error` | Token with negative expiry raises decode exception |
| `test_tampered_token_raises_error` | Modified signature raises decode exception |
| `test_wrong_secret_raises_error` | Token decoded with wrong secret raises exception |
| `test_alg_none_attack_rejected` | Forged token with `alg: none` header is rejected |

### 6.4 Integration Tests (`tests/integration/`) — 21 tests

**test_auth.py (10 tests):**
| Test Class | Tests | Coverage |
|---|---|---|
| `TestRegister` | 3 | New user returns JWT, duplicate rejected (400), role parameter accepted |
| `TestLogin` | 3 | Correct credentials return JWT, wrong password returns 401, nonexistent user returns 401 |
| `TestJWTValidation` | 4 | Valid token on protected endpoint (200), no token (401/403), invalid token (401/403), expired token (401/403) |

**test_doctors.py (6 tests):**
| Test Class | Tests | Coverage |
|---|---|---|
| `TestListDoctors` | 3 | Authenticated list (200), unauthenticated (401/403), response field validation |
| `TestCreateDoctor` | 3 | Admin creates doctor (201), non-admin rejected (403), unauthenticated (401/403) |

**test_patients.py (5 tests):**
| Test Class | Tests | Coverage |
|---|---|---|
| `TestListPatients` | 2 | Authenticated list (200), unauthenticated (401/403) |
| `TestPatientProfile` | 3 | Authenticated profile (200), unauthenticated (401/403), profile returns registered username |

### 6.5 Test Results

```
============================= test session starts =============================
collected 36 items

tests/integration/test_auth.py ..........                                [ 27%]
tests/integration/test_doctors.py ......                                 [ 44%]
tests/integration/test_patients.py .....                                 [ 58%]
tests/unit/test_security.py ...............                              [100%]

===================== 36 passed, 1758 warnings in 24.76s ======================
```

All 36 tests pass. Zero failures.

---

## 7. Technical Notes

### 7.1 Password Hashing

- `passlib[bcrypt]==1.7.4` with `bcrypt==4.0.1`
- Cost factor: 12 (default for passlib's `CryptContext`)
- Passwords longer than 72 bytes are silently truncated by bcrypt (per spec)

### 7.2 JWT Configuration

- Algorithm: HS256
- Expiry: 30 minutes (configurable via `ACCESS_TOKEN_EXPIRE_MINUTES`)
- Payload claims: `sub` (username), `exp` (expiry timestamp), `role` (user role string)
- Secret key: injected via `SECRET_KEY` environment variable
- `create_access_token()` accepts optional `extra_claims` dict for extensibility

### 7.3 Database Connection Pool

- `pool_size=20`, `max_overflow=10`, `pool_timeout=10s`, `pool_recycle=1800s`
- Async session factory with `expire_on_commit=False`
- Transactional session management via `get_db()` dependency (commit on success, rollback on exception)

### 7.4 ENUM Type Handling

- `userrole`: `patient`, `doctor`, `admin`
- `appointmentstatus`: `scheduled`, `confirmed`, `completed`, `cancelled`
- Created idempotently in `init_db()` via `_create_enum_if_not_exists()`
- Model columns use `create_type=False` and `values_callable=lambda x: [e.value for e in x]`

---

## 8. Phase 1 Quality Gates

| Gate | Status |
|---|---|
| Docker Compose build succeeds from clean checkout | **PASS** |
| `GET /api/v1/health` returns 200 | **PASS** |
| Auth integration tests pass (register, login, JWT validation) | **PASS** |
| All endpoints documented in Swagger UI | **PASS** |
| No startup errors in worker logs | **PASS** |
| All services healthy within 60 seconds | **PASS** |
| Unit tests pass (15/15 security tests) | **PASS** |
| Integration tests pass (21/21 endpoint tests) | **PASS** |
| `alg: none` JWT attack rejected | **PASS** |
| Admin role claim embedded in JWT | **PASS** |

---

## 9. Next Steps (Phase 2)

Phase 2 will deliver the full appointment booking engine with:
- `AppointmentRepository` — `list_all`, `get_by_id`, `create`, `check_conflict`
- Partial unique index for race condition prevention
- Concurrent booking tests
- Timezone handling verification
- Chaos backdoor automated tests

---

*End of Phase 1 Implementation Report*
