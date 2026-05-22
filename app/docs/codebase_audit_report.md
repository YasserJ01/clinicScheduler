# Codebase Audit Report

> **Status:** Audit Complete  
> **Summary:** Analysis of critical architectural flaws, logic bugs, data isolation failures, and missing features.

---

## 🚨 Critical Issues Found in Codebase

### BUG-A: `app/api/v1/dependencies.py`
* **Redis Connection Pooling:** A new Redis connection is created on **every single request** rather than utilizing a shared connection pool. 
* **Collision Risk:** `jti = user_id + ":" + credentials.credentials[:8]` — The JTI uses only an 8-character prefix of the credentials, making token collisions highly possible.

### BUG-B: `app/api/v1/routers/auth.py` — Refresh Token Lookup
* **Inefficient Query:** `SELECT * FROM users WHERE refresh_token_hash IS NOT NULL`
* **Performance Impact:** The application then iterates through **all** users in Python to run a `bcrypt` verification. This results in an O(N * bcrypt_cost) complexity, which will cause catastrophic performance degradation at scale.

### BUG-C: `app/api/v1/routers/auth.py` — Datetime Comparison
* **Type Incompatibility:** The comparison `u.refresh_token_expires_at < datetime.now(timezone.utc)` attempts to compare a naive datetime against a timezone-aware datetime.
* **Impact:** This raises a `TypeError` in Python 3, meaning refresh tokens can never be successfully validated.

### BUG-D: `app/api/v1/routers/doctors.py` — Doctor `user_id` Ownership Check
* **Type Mismatch:** The code checks `Doctor.user_id == current_user.get("user_id")`. However, the `user_id` inside the JWT is a username **string** (`sub` claim), whereas `Doctor.user_id` is an **integer** Foreign Key pointing to `users.id`.
* **Impact:** This comparison **never** matches. As a fallback failure, all doctor-role users can access any doctor's schedule.

### BUG-E: `app/core/webhooks.py` — Asyncio Task with Closed Session
* **Context Lifespan:** `asyncio.create_task(deliver_webhook(session, webhook, event_type, data))` passes an HTTP session bound to the short-lived request context.
* **Impact:** The session will be **closed** by the time the background task actually fires, raising a `"Session is closed"` error on every single webhook delivery attempt.

### BUG-F: `frontend/index.html` — Wrong Cancel Endpoint
* **API Mismatch:** `cancelAppointment()` hits `/api/v1/appointments/${id}/cancel`.
* **Actual Route:** The backend expects a `PATCH` request to `/appointments/{id}/status` with a body of `{"status":"cancelled"}`.
* **Impact:** The cancel button is completely broken for all patients on the frontend UI.

### BUG-G: `app/db/session.py` — Database Connection Starvation
* **Misconfiguration:** The connection pool size was reduced from 20 to 5 for PgBouncer during Phase 9 adjustments.
* **Direct Connection:** However, workers are connecting directly to port `5432` (bypassing PgBouncer entirely). 
* **Impact:** `3 workers × (5 + 5) = 30` max connections. Running a load test like k6 at 200 Virtual Users (VUs) will instantly exhaust the connection pool.

### BUG-H: Missing Alembic Migration for Multi-Tenant Changes
* **Schema Drift:** Schema migrations `001-009` do not include the `tenant_id` columns, even though the application models now enforce `tenant_id` across **every** table.
* **Migration Failure:** When `ALEMBIC_ENABLED=true`, the tables are created without the required column. `init_db()` subsequently fails during `seed_data()` because the database tries to write to a `NOT NULL` column that doesn't exist.
* **Environment Gap:** Local development works because it likely uses `create_all`, but production deployments using Alembic are fundamentally broken.

### BUG-I: No User-Patient FK Linkage Implemented
* **Brittle Fallbacks:** `GET /patients/me` still defaults to `id:0` if a patient matching the `{username}@clinic.com` email convention doesn't exist.
* **Ownership Controls:** Patient cancellation validation relies entirely on this fragile email string layout.
* **UX Friction:** The booking endpoint requires manual `patient_id` lookups; patients have no native path to "book an appointment for myself".

### BUG-J: `app/api/v1/routers/analytics.py` — Cross-Tenant Data Leakage
* **Conditional Filters:** The analytics query builds filters dynamically: `if tenant_id is not None`.
* **Security Risk:** If the endpoint is called with `current_user.get("tenant_id")` and it happens to evaluate to `None` (common with older or legacy JWT tokens), the query omits the filter entirely.
* **Impact:** This results in an unfiltered, cross-tenant data exposure, leaking data across accounts.

---

## 📋 Missing Features
*The following items are missing from the codebase, despite being marked as "complete" in past status updates or explicitly slated for production:*

1. **Appointment Reminder Scheduler:** The database infrastructure is ready (`reminder_sent`, `next_reminder_at` columns exist), but no actual background cron or scheduler worker is running to process them.
2. **Tenant Management CRUD API:** The underlying `Tenant` model is written, but no public `/tenants` controller routes or API endpoints exist to manage them.
3. **Password Recovery Flow:** Complete absence of password reset or "forgot password" routing.
4. **Brute Force Protection:** No account lockout system or threshold safety checks after multiple failed login attempts.
5. **PostgreSQL Row-Level Security (RLS):** Slated for Phase 12 data hardening but completely absent from the current database initialization layers.
6. **Machine-to-Machine Auth:** No specialized API key validation or authentication layer for automated background processes.
7. **Blue-Green Deployment Infrastructure:** Not implemented in deployment manifests.
8. **PostgreSQL Read Replicas:** Documented heavily in architectural notes as "Option A or B", but routing is not separated at the engine configuration layer.
9. **Secrets Management:** Environment variables are loaded in plaintext; integration with standard engines like HashiCorp Vault or External Secrets Operator is absent.
10. **Granular Rate Limiting:** Rate limiters are implemented globally on a per-IP footprint only. No specialized limits exist to prevent brute-forcing individual users or high-value tokens.
