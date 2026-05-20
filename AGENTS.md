# Clinic Scheduler — Agent Instructions

## Quick Start

```bash
docker compose up -d --build          # Start all services (nginx + 3 workers + postgres + redis)
docker compose down -v                # Tear down everything including DB volume
```

**Ports**: NGINX on `:80`, Postgres on `:5433`, Redis on `:6380` (host-mapped to avoid conflicts).

## Architecture

- **NGINX** (port 80) → consistent hashing LB → **3 FastAPI workers** (port 8000 each) → **Postgres** + **Redis**
- All containers share `clinic-net` bridge network. Service discovery via Docker DNS (`db`, `redis`, `worker`).
- Health check endpoint: `GET /api/v1/health` (checks DB + Redis connectivity).
- Swagger UI: `http://localhost:80/docs`, ReDoc: `http://localhost:80/redoc`.

## Key File Map

| Path | Purpose |
|---|---|
| `app/main.py` | FastAPI entrypoint. `lifespan` runs `init_db()` then `seed_data()` on startup. |
| `app/db/session.py` | Async engine + session factory. `init_db()` creates ENUM types + tables. |
| `app/db/repository.py` | Data access layer. All DB queries go through repository classes. |
| `app/models/__init__.py` | SQLAlchemy models: `User`, `Doctor`, `Patient`, `Appointment`. |
| `app/api/v1/routers/` | Route handlers: `auth`, `doctors`, `patients`, `appointments`, `health`. |
| `app/core/middleware.py` | MessagePack serialization + `X-Response-Time` header. |
| `app/core/circuit_breaker.py` | Circuit breaker for DB/Redis partial failure isolation. |
| `nginx/nginx.conf` | NGINX config: consistent hashing, rate limiting (500r/s), retry on 502/503. |
| `loadtest/scheduler.js` | k6 load test: 30s ramp to 50 VUs, 1m at 200 VUs, 30s ramp down. |

## Gotchas

### PostgreSQL ENUM types
- DB uses `TIMESTAMP WITHOUT TIME ZONE`. Pydantic parses ISO timestamps with `Z` suffix as timezone-aware datetimes. **Always strip tzinfo before DB operations** — see `app/db/repository.py:96` and `app/db/repository.py:110`.
- ENUM columns must use `values_callable=lambda x: [e.value for e in x]` in model definitions, or SQLAlchemy inserts the enum member name (`PATIENT`) instead of the value (`patient`).
- `CREATE TYPE` has no `IF NOT EXISTS` in Postgres. Use the `DO $$ BEGIN ... EXCEPTION WHEN duplicate_object` pattern in `app/db/session.py`.

### Multi-worker state
- **Never use in-memory lists/dicts for shared state.** Three workers run independently; each request may hit a different worker. All persistent data must go through PostgreSQL.
- `seed_data()` in `app/main.py` only inserts doctors if the table is empty (idempotent).

### NGINX routing
- Only `/api/`, `/docs`, `/redoc`, and `/openapi.json` are proxied. Any new top-level route must be added to `nginx/nginx.conf`.
- Health check is at `/api/v1/health` (not `/health`). Dockerfile HEALTHCHECK uses this path.

### k6 load test
- k6 binary location: `C:\Program Files\k6\k6.exe` (not on PATH).
- Run: `& "C:\Program Files\k6\k6.exe" run loadtest/scheduler.js`
- Rate limit in NGINX is set to 500r/s for load testing. For production, reduce to ~30r/s.

### bcrypt / passlib compatibility
- `requirements.txt` pins `bcrypt==4.0.1`. Newer bcrypt versions break passlib 1.7.4 with a `ValueError: password cannot be longer than 72 bytes` error.

## Dev Commands

```bash
# Rebuild workers only (no volume reset)
docker compose up -d --build

# Full reset (destroys DB data)
docker compose down -v && docker compose up -d --build

# Check worker logs
docker logs clinic-scheduler-worker-1

# Reload NGINX config without restart
docker compose exec nginx nginx -s reload

# Run load test
& "C:\Program Files\k6\k6.exe" run loadtest/scheduler.js
```
