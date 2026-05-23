# Phase 17 — Operational Excellence

## Status: Active (Sub-Phase 17-A Complete)

---

## Sub-Phase 17-A: PostgreSQL Read Replica ✅

### Objective
Add a PostgreSQL read replica (`db-replica`) to offload read-only queries from the primary database, improving read scalability and providing a hot standby for failover scenarios.

### Changes

#### 1. Configuration (`app/config.py`)
- Added `READ_DATABASE_URL: str = ""` — defaults to `DATABASE_URL` when empty (single-DB dev mode)

#### 2. Database Session (`app/db/session.py`)
- **`read_engine`**: New async engine using `READ_DATABASE_URL` with same pool settings (`pool_size=15`, `max_overflow=5`)
- **`read_session_factory`**: `async_sessionmaker` backed by `read_engine`
- **`get_read_db()`**: New async generator similar to `get_db()` but:
  - Does NOT commit on success (read-only)
  - Rollbacks on exception
  - Sets RLS context via `SET LOCAL` (same as `get_db()`)

```python
async def get_read_db() -> AsyncSession:
    async with read_session_factory() as session:
        tid = _tenant_ctx.get()
        role = _role_ctx.get()
        if tid or role:
            parts = []
            if tid:
                parts.append(f"app.current_tenant_id = '{tid}'")
            if role:
                parts.append(f"app.current_user_role = '{role}'")
            await session.execute(text(f"SET LOCAL {', '.join(parts)}"))
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
```

#### 3. Docker Compose (`docker-compose.yml`)
- **`db` service**: Added `wal_level=replica`, `max_wal_senders=3`, `wal_keep_size=256`, and mounts `scripts/init-replication.sh` to create the `replicator` role on first start
- **`db-replica` service**: New service using `pg_basebackup` to stream-replicate from `db`, with `-R` flag to auto-configure `primary_conninfo`
- **`worker` environment**: Added `READ_DATABASE_URL=postgresql+asyncpg://clinic:clinicpass@db-replica:5432/clinic_db`
- **`reminder-scheduler` environment**: Same `READ_DATABASE_URL` added

#### 4. Replication Setup Script (`scripts/init-replication.sh`)
```sql
CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'clinicpass';
ALTER SYSTEM SET wal_level = replica;
ALTER SYSTEM SET max_wal_senders = 3;
ALTER SYSTEM SET wal_keep_size = 256;
```

#### 5. Endpoints Migrated to Read Replica

**v1 routers** (25 endpoints):

| File | Endpoints |
|------|-----------|
| `appointments.py` | `GET /appointments`, `GET /appointments/available`, `GET /appointments/{id}` |
| `doctors.py` | `GET /doctors`, `GET /doctors/{id}`, `GET /doctors/{id}/schedule`, `GET /doctors/{id}/appointments/today`, `GET /doctors/{id}/appointments/upcoming`, `GET /doctors/{id}/patients` |
| `patients.py` | `GET /patients`, `GET /patients/{id}` (NOT `GET /patients/me` — it creates a Patient record on the fly) |
| `analytics.py` | `GET /admin/analytics/summary`, `GET /admin/analytics/doctors/{id}/utilisation`, `GET /admin/analytics/peak-hours`, `GET /admin/analytics/patients/{id}/history`, `GET /admin/analytics/audit-log` |
| `admin.py` | `GET /admin/patients/{id}/export`, `GET /admin/webhooks`, `GET /admin/webhooks/{id}`, `GET /admin/webhooks/{id}/deliveries`, `GET /admin/tenants`, `GET /admin/tenants/{id}`, `GET /admin/api-keys` |

**v2 routers** (2 endpoints):

| File | Endpoints |
|------|-----------|
| `appointments.py` | `GET /appointments` |
| `doctors.py` | `GET /doctors` |

**NOT migrated** (remain on primary `get_db`):
- `GET /patients/me` — writes Patient record if not found
- `GET /health` — health check should test primary
- `GET /metrics` — no DB dependency
- All POST/PATCH/DELETE endpoints (write operations)

#### 6. Agent Documentation (`app/docs/AGENTS.md`)
- Added "Read Replica (Phase 17-A)" gotcha section
- Updated architecture diagram to mention primary + replica
- Updated port description for Postgres

### Key Design Decisions
- **`get_read_db()` does NOT commit**: Read-only sessions should not issue commits. The replica is never written to.
- **Defaults to `DATABASE_URL`**: When `READ_DATABASE_URL` is empty (dev/test), reads go to the same primary DB — no infra change needed for development.
- **RLS context preserved**: `get_read_db()` sets `SET LOCAL` for tenant isolation, same as `get_db()`.
- **`GET /patients/me` kept on primary**: It writes a new `Patient` row if one doesn't exist via the FK lookup. Using a replica here would cause `could not serialize access due to read-only transaction` errors.
- **Replication lag acceptable**: List and analytics queries tolerate seconds of lag. No write-then-immediately-read patterns cross the primary→replica boundary.

### Files Changed
| File | Change |
|---|---|
| `app/config.py` | +`READ_DATABASE_URL` setting |
| `app/db/session.py` | +`read_engine`, +`read_session_factory`, +`get_read_db()` |
| `app/api/v1/routers/appointments.py` | 3 GET endpoints → `get_read_db` |
| `app/api/v1/routers/doctors.py` | 6 GET endpoints → `get_read_db` |
| `app/api/v1/routers/patients.py` | 2 GET endpoints → `get_read_db` |
| `app/api/v1/routers/analytics.py` | 5 GET endpoints → `get_read_db` |
| `app/api/v1/routers/admin.py` | 7 GET endpoints → `get_read_db` |
| `app/api/v2/routers/appointments.py` | 1 GET endpoint → `get_read_db` |
| `app/api/v2/routers/doctors.py` | 1 GET endpoint → `get_read_db` |
| `docker-compose.yml` | +`db-replica` service, `wal_level` on `db`, `READ_DATABASE_URL` on workers |
| `scripts/init-replication.sh` | New file — creates `replicator` role |
| `app/docs/AGENTS.md` | Added read replica docs |

### Tests
- Full suite: **213 passed, 3 skipped, 0 failed** (up from 211 passed in Phase 16)
- No regressions
- Ruff format: all 9 changed files left unchanged (already formatted)

---

## Upcoming Sub-Phases

| Sub-Phase | Status | Description |
|---|---|---|
| 17-B: Secrets Management | Pending | External Secrets Operator + Vault |
| 17-C: Blue-Green Deployment | Pending | CI/CD zero-downtime deploy |
| 17-D: SLA Monitoring + Error Budget | Pending | Grafana SLO dashboards |
| 17-E: Full DR Test | Pending | Documented and tested DR |
| 17-F: NGINX Config Hardening | Pending | Round-robin, rate limit tuning |
| 17-G: Load Test + Regression | Pending | k6 at 200 VUs |
