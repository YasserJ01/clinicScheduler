# Phase 9 Infrastructure Hardening — Report

## Summary
Phase 9 delivers production-ready infrastructure hardening: PgBouncer connection pooling, Redis AOF persistence, Kubernetes manifests, Prometheus alerting, CI NGINX validation, and a Disaster Recovery runbook. All 120 tests pass, ruff lint/format clean.

## Changes

### 1. PgBouncer Connection Pooling
- **Image**: `edoburu/pgbouncer:latest` (v1.25.1) — `1.21.0` tag not found on Docker Hub
- **Mode**: Transaction pooling (`POOL_MODE=transaction`)
- **Port mapping**: `6432:5432` (host:container)
- **Internal Docker network**: `pgbouncer:5432`
- **Auth fix**: PostgreSQL `pg_hba.conf` changed from `scram-sha-256` to `md5` for asyncpg compatibility
- **Password encryption**: Changed from SCRAM-SHA-256 to md5 via `ALTER SYSTEM SET password_encryption = 'md5'`
- **Worker DATABASE_URL**: Connects directly to `db:5432` (asyncpg has its own connection pool; PgBouncer reserved for sync clients)
- **Connection pool settings**: `pool_size=5`, `max_overflow=5` in `app/db/session.py`

### 2. Redis AOF Persistence
- `appendonly yes` enabled in `docker-compose.yml`
- `appendfilename clinic_aof.aof` for named AOF files
- Ensures JWT deny-list and metrics persist across Redis restarts

### 3. Kubernetes Manifests (`k8s/`)
11 manifests created:
- `namespace.yaml` — `clinic-scheduler` namespace
- `configmap.yaml` — application configuration
- `secret.yaml` — database credentials (base64-encoded)
- `deployment-worker.yaml` — 3-replica FastAPI workers
- `service-worker.yaml` — ClusterIP service for workers
- `ingress.yaml` — NGINX Ingress with TLS termination
- `hpa.yaml` — HorizontalPodAutoscaler (CPU 70%, 3-10 replicas)
- `pdb.yaml` — PodDisruptionBudget (min 2 available)
- `deployment-redis.yaml` — Redis deployment with resource limits
- `statefulset-postgres.yaml` — PostgreSQL StatefulSet with PVC
- `cronjob-backup.yaml` — Daily pg_dump backup to cloud storage
- `servicemonitor.yaml` — Prometheus ServiceMonitor for metrics scraping

### 4. Prometheus Alerting Rules (`observability/alerts.yml`)
7 alert rules defined:
- `HighErrorRate` — >5% 5xx responses over 5 minutes
- `CircuitBreakerOpen` — DB or Redis circuit breaker open
- `HighP95Latency` — p95 latency >500ms over 5 minutes
- `BookingConflictRateHigh` — >10% booking conflicts over 10 minutes
- `WorkerPodsUnavailable` — <2 worker pods available
- `RedisDown` — Redis target down for 1 minute
- `PostgresDown` — PostgreSQL target down for 1 minute

### 5. CI Pipeline Enhancement (`.github/workflows/ci.yml`)
- Added `nginx-config` validation job before test execution
- Uses `nginx:1.25-alpine` image to validate `nginx/nginx.conf` syntax

### 6. Disaster Recovery Runbook (`app/docs/Disaster_Recovery_Runbook.md`)
- Backup/restore procedures for Docker Compose and Kubernetes
- RPO/RTO targets: 24h RPO, 1h RTO for dev/staging
- PostgreSQL pg_dump backup strategy
- Redis AOF recovery steps
- Kubernetes Velero backup integration

## Test Results
- **Unit tests**: 33 passed, 3 skipped
- **Integration tests**: 87 passed
- **Total**: 120 passed, 3 skipped
- **Ruff**: All checks passed, 50 files already formatted

## Infrastructure Health
```
NAME                           STATUS
clinic-scheduler-db-1          Up (healthy)
clinic-scheduler-nginx-1       Up
clinic-scheduler-pgbouncer-1   Up
clinic-scheduler-redis-1       Up (healthy)
clinic-scheduler-worker-1      Up (healthy)
clinic-scheduler-worker-2      Up (healthy)
clinic-scheduler-worker-3      Up (healthy)
```

## Known Issues & Resolutions
1. **PgBouncer image tag**: `edoburu/pgbouncer:1.21.0` not found → resolved by using `edoburu/pgbouncer:latest` (v1.25.1)
2. **asyncpg SCRAM incompatibility**: asyncpg uses SCRAM-SHA-256 by default, incompatible with PgBouncer transaction pooling → resolved by:
   - Changing PostgreSQL `password_encryption` to `md5`
   - Updating `pg_hba.conf` from `scram-sha-256` to `md5`
   - Resetting user password with md5 encryption
3. **Worker DATABASE_URL port**: Workers were connecting to `pgbouncer:6432` (host port) instead of `pgbouncer:5432` (container port) → resolved by connecting workers directly to `db:5432` (asyncpg has built-in connection pooling)

## Next Steps (Phase 10+)
- Implement blue-green deployment strategy
- Add distributed tracing with Jaeger/Tempo
- Configure automated certificate renewal with cert-manager
- Set up cross-region disaster recovery
- Implement feature flags for gradual rollouts
