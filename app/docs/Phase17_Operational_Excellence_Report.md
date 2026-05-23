# Phase 17 — Operational Excellence

## Status: Active (Sub-Phase 17-B Complete)

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

---

## Sub-Phase 17-B: Secrets Management ✅

### Objective
Eliminate hardcoded secrets from Kubernetes manifests and Docker Compose configuration by adopting External Secrets Operator with HashiCorp Vault for K8s, and a `.env.example` pattern for Docker Compose development.

### Changes

#### 1. Kubernetes — External Secrets Operator

**`k8s/cluster-secret-store.yaml`** — new ClusterSecretStore pointing to Vault:
```yaml
apiVersion: external-secrets.io/v1beta1
kind: ClusterSecretStore
metadata:
  name: vault-backend
spec:
  provider:
    vault:
      server: "https://vault.cluster.internal:8200"
      path: "clinic-scheduler"
      version: "v2"
      auth:
        kubernetes:
          mountPath: "kubernetes"
          role: "clinic-scheduler"
```

**`k8s/external-secret.yaml`** — new ExternalSecret syncing 7 secrets from Vault:
| Secret Key | Vault Property |
|---|---|
| `SECRET_KEY` | `clinic-scheduler/production` → `SECRET_KEY` |
| `DB_PASSWORD` | `clinic-scheduler/production` → `DB_PASSWORD` |
| `REDIS_PASSWORD` | `clinic-scheduler/production` → `REDIS_PASSWORD` |
| `SENDGRID_API_KEY` | `clinic-scheduler/production` → `SENDGRID_API_KEY` |
| `SMTP_HOST` | `clinic-scheduler/production` → `SMTP_HOST` |
| `SMTP_PORT` | `clinic-scheduler/production` → `SMTP_PORT` |
| `FROM_EMAIL` | `clinic-scheduler/production` → `FROM_EMAIL` |

- Refresh interval: 1 hour
- `creationPolicy: Owner` — External Secrets manages the entire lifecycle
- Old `k8s/secret.yaml` kept as a development fallback with deprecation annotation

#### 2. Docker Compose — `.env.example`

**`.env.example`** — new file documenting all 18 environment variables with descriptions:
- **Required secrets**: `SECRET_KEY`, `DB_PASSWORD`, `REDIS_PASSWORD`
- **Database**: `DATABASE_URL`, `READ_DATABASE_URL`, `POOL_SIZE`, `MAX_OVERFLOW`
- **Redis**: `REDIS_URL`
- **Authentication**: `ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS`
- **Application**: `LOG_LEVEL`, `FRONTEND_URL`, `ALEMBIC_ENABLED`, `CHAOS_ENABLED`
- **Email**: `EMAIL_PROVIDER`, `SMTP_HOST`, `SMTP_PORT`, `SENDGRID_API_KEY`, `FROM_EMAIL`

Existing `.env` file unchanged — `.env.example` serves as documentation.

#### 3. Agent Documentation (`app/docs/AGENTS.md`)
- Added comprehensive **Secrets Management (Phase 17-B)** section covering:
  - External Secrets Operator setup and apply commands
  - Docker Compose `.env` pattern explanation
  - `SECRET_KEY` rotation with generation command
  - Full secret rotation procedure for both K8s (Vault → ExternalSecret → rollout) and Docker Compose (`.env` → rebuild)

### Key Design Decisions
- **ClusterSecretStore** (not namespaced): Vault backend is cluster-wide; `ExternalSecret` is namespaced to `clinic-scheduler`.
- **Kubernetes auth**: Vault uses Kubernetes service account authentication — no static Vault token needed.
- **`creationPolicy: Owner`**: If the ExternalSecret is deleted, the resulting Secret is also deleted — prevents orphaned secrets.
- **Old `secret.yaml` retained**: Development clusters may not have Vault/External Secrets installed. The static fallback allows `kubectl apply -f k8s/` to work without External Secrets.
- **`.env.example` not `.env`**: The actual `.env` is gitignored; `.env.example` is the documented template.

### Files Changed
| File | Change |
|---|---|
| `k8s/cluster-secret-store.yaml` | New — Vault ClusterSecretStore |
| `k8s/external-secret.yaml` | New — ExternalSecret for 7 secrets |
| `k8s/secret.yaml` | Updated — deprecation annotation, kept as fallback |
| `.env.example` | New — comprehensive env var documentation |
| `app/docs/AGENTS.md` | Added Secrets Management section |

### Tests
- Full suite: **213 passed, 3 skipped, 0 failed** (unchanged — no code changes)
- No regressions
- Ruff format: no Python files changed (YAML/markdown only)

---

## Upcoming Sub-Phases

| Sub-Phase | Status | Description |
|---|---|---|
| 17-C: Blue-Green Deployment | Pending | CI/CD zero-downtime deploy |
| 17-D: SLA Monitoring + Error Budget | Pending | Grafana SLO dashboards |
| 17-E: Full DR Test | Pending | Documented and tested DR |
| 17-F: NGINX Config Hardening | Pending | Round-robin, rate limit tuning |
| 17-G: Load Test + Regression | Pending | k6 at 200 VUs |
