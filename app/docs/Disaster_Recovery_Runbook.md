# Disaster Recovery Runbook
## Clinic Scheduler — Backup and Restore Procedures

| Field | Value |
|---|---|
| Document Version | 1.0.0 |
| Date | 2026-05-21 |
| Classification | Internal / Operational |

---

## 1. Backup Configuration

### 1.1 Automated Backups
- **Schedule**: Daily at 02:00 UTC
- **Method**: `pg_dump` with gzip compression
- **Storage**: `/backup` volume (Kubernetes CronJob)
- **Retention**: 30 most recent backups

### 1.2 Backup Location
- **Kubernetes**: `emptyDir` volume on the CronJob pod (for production, mount a PersistentVolume or cloud storage)
- **Docker Compose**: Not configured by default; use manual backup procedures below

---

## 2. Manual Backup Procedures

### 2.1 Docker Compose
```bash
# Full database dump
docker compose exec db pg_dump -U clinic -d clinic_db | gzip > backup_$(date +%Y%m%d_%H%M%S).sql.gz

# Verify backup
gunzip -c backup_*.sql.gz | head -20
```

### 2.2 Kubernetes
```bash
# Exec into the postgres pod
kubectl exec -n clinic-scheduler -it clinic-postgres-0 -- \
  pg_dump -U clinic -d clinic_db | gzip > backup_$(date +%Y%m%d_%H%M%S).sql.gz

# Or trigger the CronJob manually
kubectl create -n clinic-scheduler job --from=cronjob/postgres-backup manual-backup-$(date +%Y%m%d)
```

### 2.3 Redis Backup
```bash
# Docker Compose
docker compose exec redis redis-cli -a redispass BGSAVE

# Kubernetes
kubectl exec -n clinic-scheduler -it deploy/clinic-redis -- redis-cli BGSAVE
```

---

## 3. Restore Procedures

### 3.1 Full Database Restore (Docker Compose)
```bash
# 1. Stop the application
docker compose down

# 2. Start only the database
docker compose up -d db

# 3. Wait for database to be ready
docker compose exec db pg_isready -U clinic

# 4. Drop and recreate the database (WARNING: destroys all data)
docker compose exec db psql -U clinic -d clinic_db -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

# 5. Restore from backup
gunzip -c backup_YYYYMMDD_HHMMSS.sql.gz | docker compose exec -T db psql -U clinic -d clinic_db

# 6. Start the full stack
docker compose up -d --build

# 7. Verify
curl http://localhost/api/v1/health
```

### 3.2 Full Database Restore (Kubernetes)
```bash
# 1. Scale down workers to prevent writes
kubectl scale -n clinic-scheduler deployment/clinic-worker --replicas=0

# 2. Copy backup to postgres pod
kubectl cp backup_YYYYMMDD_HHMMSS.sql.gz clinic-scheduler/clinic-postgres-0:/tmp/restore.sql.gz

# 3. Exec into postgres pod and restore
kubectl exec -n clinic-scheduler -it clinic-postgres-0 -- \
  sh -c "gunzip -c /tmp/restore.sql.gz | PGPASSWORD=\$POSTGRES_PASSWORD psql -U clinic -d clinic_db"

# 4. Clean up
kubectl exec -n clinic-scheduler clinic-postgres-0 -- rm /tmp/restore.sql.gz

# 5. Scale workers back up
kubectl scale -n clinic-scheduler deployment/clinic-worker --replicas=3

# 6. Verify
kubectl exec -n clinic-scheduler clinic-postgres-0 -- pg_isready -U clinic
```

### 3.3 Point-in-Time Recovery (Cloud/Managed DB)
If using AWS RDS, Google Cloud SQL, or Azure Database for PostgreSQL:
1. Navigate to the managed database console
2. Select "Restore to point in time"
3. Choose the target timestamp
4. A new database instance will be created
5. Update `DATABASE_URL` in ConfigMap/Secret to point to the restored instance
6. Restart workers

---

## 4. Redis Recovery

### 4.1 From AOF Persistence
Redis AOF (Append Only File) persistence is enabled by default. If Redis restarts:
1. Redis automatically replays the AOF file on startup
2. All data (metrics, token deny-list) is restored
3. No manual intervention required

### 4.2 If AOF is Corrupted
```bash
# Docker Compose
docker compose down redis
docker compose up -d redis
# Note: All Redis data will be lost. Token deny-list entries will need to re-expire naturally.

# Kubernetes
kubectl delete -n clinic-scheduler pod -l app=clinic-redis
# The deployment will recreate the pod with a fresh Redis instance.
```

---

## 5. Verification After Restore

### 5.1 Database Integrity
```bash
# Check table counts
docker compose exec db psql -U clinic -d clinic_db -c "
  SELECT 'doctors' AS table_name, COUNT(*) FROM doctors
  UNION ALL SELECT 'patients', COUNT(*) FROM patients
  UNION ALL SELECT 'appointments', COUNT(*) FROM appointments
  UNION ALL SELECT 'users', COUNT(*) FROM users;
"

# Verify indexes
docker compose exec db psql -U clinic -d clinic_db -c "\di"
```

### 5.2 Application Health
```bash
# Health check
curl -f http://localhost/api/v1/health

# Test booking flow
# (Use a test patient and future time slot)
```

### 5.3 Metrics Verification
```bash
# Check Prometheus metrics endpoint
curl -s http://localhost/api/v1/metrics | head -20
```

---

## 6. Escalation Procedures

| Severity | Condition | Action |
|---|---|---|
| P1 — Critical | Database completely lost, no backups | Contact DBA team; attempt disk recovery |
| P1 — Critical | Database corrupted, backups available | Restore from latest backup (Section 3) |
| P2 — High | Redis data lost | Restart Redis; deny-list entries will expire naturally |
| P2 — High | Single worker pod failing | Kubernetes auto-restarts; check logs |
| P3 — Medium | Metrics gap (Redis restart) | Acceptable data loss; metrics resume automatically |
| P3 — Medium | Backup job failed | Run manual backup (Section 2); investigate CronJob logs |

---

## 7. Backup Monitoring

### 7.1 Alert Rules
The following Prometheus alerts are configured (see `observability/alerts.yml`):
- `HighErrorRate` — HTTP 5xx rate > 5%
- `CircuitBreakerOpen` — DB circuit breaker OPEN
- `HighP95Latency` — p95 latency > 500ms
- `BookingConflictRateHigh` — Conflict rate > 30%
- `WorkerPodsUnavailable` — > 1 worker pod down
- `RedisDown` — Redis unreachable
- `PostgresDown` — PostgreSQL unreachable

### 7.2 Backup Success Verification
```bash
# Kubernetes: Check last backup job status
kubectl get -n clinic-scheduler jobs --sort-by=.status.startTime

# Check backup file exists
kubectl exec -n clinic-scheduler job/postgres-backup-xxxx -- ls -lh /backup/
```

---

## 8. Contact Information

| Role | Contact |
|---|---|
| On-call Engineer | PagerDuty rotation |
| DBA Team | dba-team@clinic.example.com |
| Infrastructure Lead | infra-lead@clinic.example.com |
