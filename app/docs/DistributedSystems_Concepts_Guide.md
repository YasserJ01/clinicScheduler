# Distributed Systems Concepts in Practice
## A Developer's Guide to the Clinic Scheduler Codebase

> **Audience:** Backend developers learning how distributed systems concepts are applied in production Python services.
> **Codebase:** Medical Clinic Appointment Scheduler — FastAPI + PostgreSQL + Redis + NGINX + Docker
> **Philosophy:** Every concept here solves a real problem. We explain the problem first, then show exactly how the code solves it.

---

## Table of Contents

1. [Load Balancing](#1-load-balancing)
2. [Concurrency](#2-concurrency)
3. [Latency](#3-latency)
4. [Partial Failure and Resilience](#4-partial-failure-and-resilience)
5. [Service Discovery](#5-service-discovery)
6. [MessagePack](#6-messagepack)
7. [Serialization](#7-serialization)

---

## 1. Load Balancing

### 1.1 The Problem It Solves

Imagine a clinic booking system on a busy Monday morning — hundreds of patients trying to book appointments simultaneously. If all those requests hit a single server, that server eventually runs out of memory, connection pool slots, or CPU time. Requests queue up, latency skyrockets, and the service crashes.

**Load balancing** distributes incoming requests across multiple identical server instances (workers). No single instance gets overwhelmed, and if one fails, traffic is automatically redirected to the survivors.

### 1.2 The Algorithm: Consistent Hashing

There are several load balancing algorithms to choose from:

| Algorithm | How It Works | Best For |
|---|---|---|
| Round Robin | Requests sent to workers in rotation (1→2→3→1→2→3) | Uniform, stateless workloads |
| Least Connections | Route to whichever worker has fewest active connections | Variable-duration requests |
| IP Hash | Hash the client IP, always route to the same worker | Session affinity |
| **Consistent Hashing** | Hash the request URI, route to the same worker per URI | Cache locality, URI-affine routing |

This codebase uses **Consistent Hashing on the request URI**.

**Why Consistent Hashing over Round Robin?**

Round Robin is simple but ignores the nature of the requests. Consider `GET /api/v1/doctors` — this is called by every VU in the load test. With Round Robin, three consecutive calls go to three different workers. If each worker caches doctor data locally, you need three separate cache entries updated independently.

With Consistent Hashing on `$request_uri`, all requests for the same URI go to the same upstream node. Cache hits are maximised. When a new worker is added, only the fraction of keys that "move" are affected — unlike a simple modulo hash where adding one server remaps everything.

**The "consistent" keyword** implements the [Ketama](https://www.last.fm/user/RJ/journal/2007/04/10/rz_libketama_-_a_consistent_hashing_algo_for_memcache_clients) ring algorithm. Without `consistent`, adding or removing a server remaps all keys. With `consistent`, only approximately `1/N` of keys move when `N` changes.

### 1.3 The Code

```nginx
# nginx/nginx.conf

upstream clinic_backend {
    hash $request_uri consistent;   # ← The algorithm declaration
    server worker:8000;             # ← Docker DNS resolves "worker" to all 3 replicas
}
```

This single upstream block is deceptively powerful. Docker's DNS returns all three replica IPs when `worker` is resolved, and NGINX distributes across them using the consistent hash ring.

**Rate limiting** protects the upstream workers from being overwhelmed:

```nginx
# Define a rate limit zone: 10MB memory, 500 requests/second per client IP
limit_req_zone $binary_remote_addr zone=api_limit:10m rate=500r/s;

location /api/ {
    limit_req zone=api_limit burst=50 nodelay;  # Allow burst of 50, then enforce limit immediately
    
    proxy_pass http://clinic_backend;
    
    proxy_connect_timeout 3s;    # Give up connecting after 3 seconds
    proxy_send_timeout    5s;    # Give up sending after 5 seconds
    proxy_read_timeout   10s;    # Give up waiting for response after 10 seconds
    
    # If a worker returns 502 (Bad Gateway) or 503 (Unavailable),
    # NGINX automatically retries the request on the NEXT worker
    proxy_next_upstream error timeout http_502 http_503;
    proxy_next_upstream_tries 2;  # Try at most 2 workers before giving up
}
```

**What `burst=50 nodelay` means:** If a client sends 550 requests in one second (above the 500 r/s limit), the first 500 are served immediately, the next 50 are queued (burst), and the rest are rejected with HTTP 429. `nodelay` means the burst requests are served immediately rather than being delayed — essential for maintaining low perceived latency.

**What `proxy_next_upstream` buys you:** If Worker 1 crashes mid-deployment, NGINX automatically retries the next request on Worker 2 or 3. The client never sees the error. This is a critical piece of the availability puzzle.

### 1.4 The Full Request Flow

```
Client (200 VUs)
    │
    ▼
NGINX :80
    │ hash("/api/v1/appointments") = Node #2
    │
    ├── Worker 1 :8000  ← gets GET /api/v1/doctors (always)
    ├── Worker 2 :8000  ← gets POST /api/v1/appointments (always)
    └── Worker 3 :8000  ← gets GET /api/v1/patients (always)
         │
         └── PostgreSQL :5432  (all workers share the same DB)
         └── Redis     :6379  (all workers share the same Redis)
```

### 1.5 Load Test Validation

The k6 load test in `loadtest/scheduler.js` directly validates the load balancing claims:

```javascript
// loadtest/scheduler.js

export const options = {
  scenarios: {
    read_heavy: {
      executor: 'ramping-vus',
      stages: [
        { duration: '30s', target: 50  },   // Ramp up to 50 virtual users
        { duration: '1m',  target: 200 },   // Sustain 200 virtual users for 1 minute
        { duration: '30s', target: 0   },   // Ramp down
      ],
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<500'],  // 95th percentile must be under 500ms
    http_req_failed:   ['rate<0.05'],  // Less than 5% requests fail
    errors:            ['rate<0.1'],   // Less than 10% application errors
    booking_success:   ['rate>0.8'],   // 80% of booking attempts succeed
    doctors_latency:   ['p(95)<300'],  // GET /doctors under 300ms at p95
  },
};
```

**Phase 4 results — the numbers prove the algorithm works:**

| Configuration | Throughput | p95 Latency | Improvement |
|---|---|---|---|
| 1 Worker (baseline) | 132.6 req/s | 50.74ms | — |
| 3 Workers (load balanced) | 159.4 req/s | 22.65ms | +20% throughput, -55% latency |

The latency reduction is dramatic because fewer requests compete for the same database connection pool. The throughput gain is modest (not 3×) because the single PostgreSQL instance becomes the bottleneck — the load balancer is doing its job, but the DB is now the constraint.

---

## 2. Concurrency

### 2.1 The Problem It Solves

Concurrency in this system has two completely different dimensions that are easy to confuse:

1. **I/O Concurrency:** How can a single worker process hundreds of requests simultaneously without blocking?
2. **Write Concurrency:** How do we prevent two requests that arrive at the same millisecond from both booking the same appointment slot?

Both are solved in this codebase, with different tools.

### 2.2 I/O Concurrency: Async/Await and the Event Loop

Traditional Python web servers process one request at a time per thread. While waiting for the database to respond, the thread sits idle. To handle 200 concurrent users you'd need 200 threads — expensive in memory (each thread uses ~8MB of stack) and slow to context-switch.

FastAPI uses Python's `asyncio` event loop. A single thread can suspend a coroutine that is waiting for I/O (a database query, a Redis ping) and immediately start processing another request. When the DB responds, the original coroutine resumes. One thread can handle hundreds of concurrent I/O-bound operations.

```python
# app/db/session.py

# create_async_engine → uses asyncpg (async PostgreSQL driver)
# This engine NEVER blocks — all queries return awaitables
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=20,        # Keep 20 connections open and ready
    max_overflow=10,     # Allow up to 10 more when pool is full (max: 30 total)
    pool_timeout=10,     # Wait at most 10 seconds for a connection before erroring
    pool_recycle=1800,   # Re-open connections after 30 minutes (prevents stale TCP)
    echo=False,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # Don't expire objects after commit (avoids lazy load issues)
)

# get_db is a FastAPI dependency — called for EVERY request
# yield turns it into a context manager: session opens, request runs, session closes
async def get_db() -> AsyncSession:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()   # Commit on success (non-blocking)
        except Exception:
            await session.rollback() # Rollback on any exception (non-blocking)
            raise
```

**Why `pool_size=20`?** Each of the 3 workers maintains 20 open database connections. That's 60 total connections to PostgreSQL from the application layer. PostgreSQL's default `max_connections=100` leaves headroom. If you set `pool_size=200`, you'd have 600 connections — PostgreSQL would refuse new ones and the system would grind to a halt.

**Why `expire_on_commit=False`?** By default, SQLAlchemy marks all ORM objects as "expired" after a commit, meaning any attribute access after the commit would trigger a new SQL SELECT. In an async context, this would cause `MissingGreenlet` errors if you try to access attributes outside the session scope. Setting this to `False` lets you safely read the object after the commit.

### 2.3 Write Concurrency: The Double-Booking Race Condition

This is the hard problem. Two patients try to book the same slot with the same doctor at the exact same millisecond. Both requests arrive on different workers. Both call `check_conflict()`. Both find no conflict (the appointment doesn't exist yet). Both attempt to insert. Without protection, both succeed — and now the doctor has two patients in the same slot.

The solution is **defense in depth**: two layers of protection.

**Layer 1 — Application-level conflict check:**

```python
# app/db/repository.py

async def check_conflict(
    self, doctor_id: int, appointment_time: datetime, duration_minutes: int = 30
) -> Appointment | None:
    naive_time = appointment_time.replace(tzinfo=None) if appointment_time.tzinfo else appointment_time
    end_time = naive_time + timedelta(minutes=duration_minutes)
    lower_bound = naive_time - timedelta(minutes=480)  # Max appointment duration as safety bound

    result = await self.session.execute(
        select(Appointment).where(
            Appointment.doctor_id == doctor_id,
            Appointment.appointment_time >= lower_bound,  # Efficient lower bound
            Appointment.appointment_time < end_time,      # Upper bound
            Appointment.status != AppointmentStatus.CANCELLED,
        )
    )
    appointments = result.scalars().all()

    # Range overlap detection: two intervals overlap if:
    # new_start < existing_end AND new_end > existing_start
    for appt in appointments:
        appt_end = appt.appointment_time + timedelta(minutes=appt.duration_minutes)
        if naive_time < appt_end:
            return appt   # Return the conflicting appointment (not just True/False)

    return None
```

The conflict check returns the **conflicting appointment object**, not just a boolean. This lets the API include the other patient's name in the error message — vastly more useful than a generic "slot unavailable".

**Layer 2 — Database-level partial unique index:**

```python
# app/db/session.py

async def _create_partial_unique_index(conn):
    """
    This is the last line of defence against race conditions.
    Even if two requests both pass the application-level check,
    the database will only allow ONE insert to succeed.
    """
    await conn.execute(text("""
        CREATE UNIQUE INDEX uix_appointment_slot
        ON appointments (doctor_id, appointment_time)
        WHERE status != 'cancelled';
    """))
```

Why **partial** (the `WHERE status != 'cancelled'` clause)? Because the business rule says: if an appointment is cancelled, that slot is free again. A regular unique index would prevent re-booking a cancelled slot. The partial index only enforces uniqueness among non-cancelled appointments.

**Layer 3 — IntegrityError recovery in the router:**

```python
# app/api/v1/routers/appointments.py

try:
    new_appt = await appt_repo.create(
        doctor_id=appt.doctor_id,
        patient_id=patient.id,
        appointment_time=naive_time,
        duration_minutes=appt.duration_minutes,
    )
except IntegrityError:
    # The unique index fired — another concurrent request won the race.
    # Roll back our failed transaction and find the winner.
    await db.rollback()
    conflict = await appt_repo.check_conflict(appt.doctor_id, naive_time, appt.duration_minutes)
    if conflict:
        patient_repo = PatientRepository(db)
        holder = await patient_repo.get_by_id(conflict.patient_id)
        holder_name = holder.name if holder else "Unknown"
        conflict_resp = BookingResponse(
            success=False,
            node_id=NODE_ID,
            error=f"Slot already occupied by patient {holder_name}",
            appointment=AppointmentDetail(...)
        )
        return JSONResponse(status_code=409, content=conflict_resp.model_dump())
    raise  # Something else went wrong — re-raise to the global exception handler
```

**The three-layer chain in plain English:**

```
Request A and Request B arrive simultaneously for same slot

Both call check_conflict() → both find nothing → both proceed

Both call INSERT INTO appointments ...

PostgreSQL evaluates the partial unique index:
  ├── First INSERT: succeeds ✓ → 201 Created
  └── Second INSERT: violates uix_appointment_slot → IntegrityError

IntegrityError is caught → rollback → re-query for the winner → 409 Conflict
```

### 2.4 Concurrency Tests

```python
# tests/integration/test_concurrent_booking.py

class TestConcurrentBooking:
    def test_concurrent_same_slot_one_succeeds(self, auth_headers, patient_id, seeded_doctor_id):
        """
        Two simultaneous requests for the same slot: one gets 201, one gets 409.
        Uses ThreadPoolExecutor to fire both requests at exactly the same time.
        """
        payload = {
            "doctor_id": seeded_doctor_id,
            "patient_id": patient_id,
            "time_slot": concurrent_slot,   # Same slot for both
        }

        results = []
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self._book_appointment, payload, auth_headers)
                       for _ in range(2)]
            for future in as_completed(futures):
                results.append(future.result())

        status_codes = sorted([r["status_code"] for r in results])
        
        # The contract: exactly one 201, exactly one 409 — never two 201s
        assert status_codes == [201, 409], f"Expected [201, 409], got {status_codes}"

        success_resp = next(r for r in results if r["status_code"] == 201)
        conflict_resp = next(r for r in results if r["status_code"] == 409)
        assert success_resp["success"] is True
        assert conflict_resp["success"] is False
        assert "already occupied" in conflict_resp["error"]

        # Verify at the database level: exactly 1 appointment was created
        list_resp = httpx.Client(base_url="http://localhost").get(
            "/api/v1/appointments", headers=auth_headers
        )
        matching = [a for a in list_resp.json()
                    if a["time_slot"].startswith(concurrent_slot.rstrip("Z"))
                    and a["doctor_id"] == seeded_doctor_id]
        assert len(matching) == 1, f"Expected 1 appointment, found {len(matching)}"
```

**Why `ThreadPoolExecutor` and not `asyncio.gather`?** The test client (`httpx.Client`) is synchronous — it runs in the test's thread. To send two truly simultaneous HTTP requests, we spin up two OS threads, each sending its own request. `asyncio.gather` would still send them sequentially because the test itself is not async. Real concurrency for real race condition testing requires real threads.

---

## 3. Latency

### 3.1 The Problem It Solves

Latency is the time between a client sending a request and receiving the response. High latency means users wait. In a booking system, a 3-second response feels broken. A 50ms response feels instant.

This codebase manages latency at three levels: **measurement** (know what's happening), **control** (enforce limits), and **optimisation** (make things faster).

### 3.2 Measurement: The X-Response-Time Header

You cannot optimise what you cannot measure. Every single response from this system includes a header that tells the client exactly how long the server took to process the request:

```python
# app/core/middleware.py

class MessagePackMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # time.monotonic() is used instead of time.time() because:
        # - monotonic() is guaranteed to never go backwards (no NTP adjustments)
        # - time.time() can jump backwards if the system clock is adjusted
        # - For measuring elapsed time, monotonic() is always correct
        start_time = time.monotonic()
        
        response = await call_next(request)  # Process the entire request
        
        elapsed = time.monotonic() - start_time
        
        # Format: milliseconds with 2 decimal places (e.g., "12.43ms")
        response.headers["X-Response-Time"] = f"{elapsed * 1000:.2f}ms"
        
        return response
```

**Why a response header?** Because it travels back with every response automatically. No separate logging query needed. Client-side monitoring tools, browsers (in DevTools), and API gateways can all read it. It also allows the client to compute the network round-trip overhead by comparing the `X-Response-Time` with their own measured elapsed time.

**This header is set by the middleware, which wraps the ENTIRE request pipeline.** That means it includes Pydantic validation, JWT decoding, database queries, business logic, response serialization — everything. It's the true end-to-end server processing time.

### 3.3 Control: Timeout Enforcement at Every Layer

Latency without bounds causes cascading failures. If a slow database query takes 60 seconds, that request holds a connection pool slot for 60 seconds. 200 concurrent slow queries exhaust all 30 connection pool slots within 30/200 * 60 seconds = 9 seconds, and then every new request waits indefinitely.

The system enforces timeouts at every layer:

```nginx
# nginx/nginx.conf — enforces timeouts between NGINX and the upstream workers

proxy_connect_timeout  3s;   # If no TCP connection in 3s, fail fast → 502
proxy_send_timeout     5s;   # If no data sent to upstream in 5s, fail fast
proxy_read_timeout    10s;   # If no data received from upstream in 10s, fail fast

# TCP connection reuse (avoiding the cost of repeated 3-way handshakes)
keepalive_timeout    30s;    # Keep idle connections open for 30 seconds
keepalive_requests 1000;     # Reuse a connection for up to 1000 requests
```

```python
# app/db/session.py — enforces timeouts between workers and PostgreSQL

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_timeout=10,     # If no connection available in pool within 10s → error
    pool_recycle=1800,   # Force-close connections after 30 min (prevents zombie TCP)
)
```

**The timeout cascade:**

```
Client → (browser timeout: varies)
    └── NGINX → (connect: 3s, send: 5s, read: 10s)
            └── FastAPI Worker → (pool_timeout: 10s)
                    └── PostgreSQL → (query execution: no explicit timeout, 
                                      controlled by pool_timeout above)
```

**Why different values at each layer?** NGINX's `proxy_read_timeout=10s` is the outer bound. If a worker takes more than 10 seconds, NGINX closes the connection and (if configured) retries on another worker. The `pool_timeout=10s` ensures workers don't wait forever for a DB connection — they fail fast and return an error to NGINX, which then retries.

### 3.4 Optimisation: Database Indexes for Sub-Millisecond Queries

Latency at the application layer is dominated by database query time. Every appointment booking triggers a conflict check — a query that runs on every single POST. If this query does a full table scan, latency grows linearly with the number of appointments. With 100,000 appointments, a full scan might take 50ms. At 1 million, it might take 500ms.

```sql
-- The critical index: composite, partial
CREATE UNIQUE INDEX uix_appointment_slot
ON appointments (doctor_id, appointment_time)
WHERE status != 'cancelled';

-- Supporting indexes
CREATE INDEX ix_appointments_doctor_id ON appointments (doctor_id);
CREATE INDEX ix_appointments_appointment_time ON appointments (appointment_time);
CREATE INDEX ix_appointments_patient_id ON appointments (patient_id);
```

**Phase 4 measured query execution times with 26,000+ rows:**

| Query | Plan | Execution Time |
|---|---|---|
| `check_conflict()` | Index Scan using `uix_appointment_slot` | **0.083ms** |
| `list_all()` with ORDER BY | Index Scan using `ix_appointments_appointment_time` | **0.085ms** |
| Patient lookup by ID | Index Scan using `patients_pkey` | **0.044ms** |

Sub-millisecond query execution means the dominant latency source is the network round-trip between the worker and the database container (typically 0.5–2ms on a Docker bridge network) — not the query itself.

### 3.5 The k6 Load Test as a Latency Contract

The load test enforces latency as a hard contract that breaks the build if violated:

```javascript
// loadtest/scheduler.js

const bookingLatency = new Trend('booking_latency', true);  // true = track as milliseconds
const doctorsLatency = new Trend('doctors_latency', true);

export const options = {
  thresholds: {
    // These are SLO (Service Level Objectives) encoded as test assertions:
    'http_req_duration': ['p(95)<500'],  // 95% of ALL requests under 500ms
    'booking_latency':   ['p(95)<500'],  // POST /appointments p95 under 500ms
    'doctors_latency':   ['p(95)<300'],  // GET /doctors p95 under 300ms (read path faster)
  },
};

export default function(data) {
    const start = Date.now();
    const res = http.get(`${BASE_URL}/api/v1/doctors`, { headers });
    doctorsLatency.add(res.timings.duration);  // Record actual measured duration
    
    // res.timings.duration: time from sending request to receiving full response body
    // This is the client-side view of latency, including network time
}
```

### 3.6 Latency Tests

```python
# tests/integration/test_middleware.py

class TestMessagePackMiddleware:
    def test_response_time_header_present(self, http_client, auth_headers):
        """Every response must include the X-Response-Time header."""
        resp = http_client.get("/api/v1/doctors", headers=auth_headers)
        assert resp.status_code == 200
        assert "X-Response-Time" in resp.headers
        assert resp.headers["X-Response-Time"].endswith("ms")

    def test_response_time_is_positive(self, http_client, auth_headers):
        """The measured time must be a positive number."""
        resp = http_client.get("/api/v1/doctors", headers=auth_headers)
        time_str = resp.headers["X-Response-Time"]
        value = float(time_str.replace("ms", ""))
        assert value >= 0   # Can be 0.00ms in rare cases (cache hit)

    def test_health_check_has_response_time(self, http_client, auth_headers):
        """Even lightweight endpoints are timed — no exceptions."""
        resp = http_client.get("/api/v1/health", headers=auth_headers)
        assert resp.status_code == 200
        assert "X-Response-Time" in resp.headers
```

---

## 4. Partial Failure and Resilience

### 4.1 The Problem It Solves

Distributed systems fail partially. The database might be temporarily unreachable. Redis might restart for maintenance. One of the three workers might crash. The question is not "will things fail?" but "how does the system behave when they do?"

**Without resilience patterns:**
- Database goes down → every request hangs for 30 seconds → all 30 connection pool slots fill up → new requests immediately fail → cascading failure across the entire system

**With resilience patterns:**
- Database goes down → first 5 requests fail fast → circuit breaker opens → all subsequent requests fail instantly with a clear error → database recovers → circuit breaker closes → system resumes automatically

### 4.2 The Circuit Breaker Pattern

A circuit breaker is modelled on electrical circuit breakers. When too many failures occur, the breaker "opens" and stops allowing current (requests) to flow through. After a timeout, it allows a single test request through. If that succeeds, the breaker "closes" again.

```python
# app/core/circuit_breaker.py

class CircuitState(Enum):
    CLOSED   = "closed"     # Normal operation — requests flow through
    OPEN     = "open"       # Failure mode — requests fail immediately
    HALF_OPEN = "half_open" # Recovery probe — one test request allowed

class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,     # How many failures trigger OPEN
        recovery_timeout: float = 30.0, # How long to wait before probing
        half_open_max_calls: int = 1,   # How many probes to allow
    ):
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0

    @property
    def state(self) -> CircuitState:
        # This is the key insight: state is computed on every access.
        # If enough time has passed since opening, automatically transition to HALF_OPEN.
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._last_failure_time > self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
        return self._state

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        current_state = self.state
        
        if current_state == CircuitState.OPEN:
            # Don't even try — fail immediately with a clear error
            raise CircuitBreakerError("Circuit breaker is OPEN")

        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except Exception:
            self._on_failure()
            raise

    def _on_success(self) -> None:
        self._failure_count = 0                          # Reset the failure counter
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.CLOSED            # Recovery confirmed — reopen fully

    def _on_failure(self) -> None:
        self._failure_count += 1
        if self._state == CircuitState.HALF_OPEN:
            # The probe failed — back to OPEN
            self._state = CircuitState.OPEN
            self._last_failure_time = time.monotonic()
        elif self._failure_count >= self.failure_threshold:
            # Threshold crossed — trip the breaker
            self._state = CircuitState.OPEN
            self._last_failure_time = time.monotonic()


# Two separate breakers for two separate dependencies
db_breaker    = CircuitBreaker(failure_threshold=5, recovery_timeout=15.0)
redis_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=10.0)
```

**Why separate breakers with different thresholds?**

Redis is more peripheral than the database. The system can survive a Redis outage (metrics will stop collecting, but booking still works). Therefore Redis gets a lower threshold (3 failures) and faster recovery (10s). The database is existential — you want more proof that it's actually down (5 failures) before refusing all requests, and a longer recovery probe period (15s).

**The state machine:**

```
         ┌──────────────────────────────────────────────────────────────────┐
         │                   failure_count >= threshold                     │
         ▼                                                                  │
    ┌─────────┐                                                        ┌────┴────┐
    │  OPEN   │──── recovery_timeout elapsed ────────────────────────► │HALF_OPEN│
    └─────────┘                                                        └────┬────┘
         ▲                                                                  │
         │ failure in HALF_OPEN                               success in    │
         └──────────────────────────────────────────────────── HALF_OPEN   │
                                                                            │
    ┌──────────┐ ◄────────────────────────────────────────────────────────┘
    │  CLOSED  │
    └──────────┘
         │
         └── failure_count++ on each failure
             (does NOT open until threshold is reached)
```

### 4.3 Graduated Degradation in the Health Endpoint

The health check demonstrates **graduated degradation** — the system distinguishes between different levels of failure and responds appropriately:

```python
# app/api/v1/routers/health.py

@router.get("")
async def health_check(db: AsyncSession = Depends(get_db)):
    db_status    = "healthy"
    redis_status = "healthy"

    # DB probe — wrapped in circuit breaker
    try:
        await db_breaker.call(db.execute, text("SELECT 1"))
    except CircuitBreakerError:
        logger.error("health_check: DB circuit breaker is OPEN")
        db_status = "unhealthy"
    except Exception as e:
        logger.error(f"health_check: DB probe failed: {e}")
        db_status = "unhealthy"

    # Redis probe — separate breaker, independent failure
    try:
        r = aioredis.from_url(settings.REDIS_URL)
        await redis_breaker.call(r.ping)
        await r.aclose()
    except CircuitBreakerError:
        logger.error("health_check: Redis circuit breaker is OPEN")
        redis_status = "unhealthy"
    except Exception as e:
        logger.error(f"health_check: Redis probe failed: {e}")
        redis_status = "unhealthy"

    # The critical distinction:
    # - DB down → 503 Service Unavailable (system cannot function)
    # - Redis down → 200 OK but with degraded status (system still works, just slower)
    status_code = 200 if db_status == "healthy" else 503
    return {
        "status": "ok" if status_code == 200 else "degraded",
        "database": db_status,
        "redis":    redis_status,
    }
```

### 4.4 NGINX-Level Resilience: Automatic Retry

The circuit breaker operates at the application layer. NGINX adds resilience at the infrastructure layer, transparent to the application:

```nginx
# nginx/nginx.conf

proxy_next_upstream error timeout http_502 http_503;
proxy_next_upstream_tries 2;
```

**What this means in practice:**

If Worker 2 crashes mid-deployment and returns a 502, NGINX automatically retries the request on Worker 1 or Worker 3. The client never sees the 502. This is zero-downtime failover at the proxy layer.

**The retry combination:**

```
Client Request → NGINX → Worker 2 (crashed → 502)
                      ↘
                        Worker 1 (healthy → 200) → Client sees 200
```

NGINX logs the retry internally, but the client only sees a successful response. Latency increases slightly (two hops instead of one), but availability is preserved.

### 4.5 Structured Exception Handling

The final layer of partial failure management: when something does fail, return a structured, useful error rather than an unhandled traceback:

```python
# app/core/exceptions.py

def register_exception_handlers(app: FastAPI):
    
    @app.exception_handler(CircuitBreakerError)
    async def circuit_breaker_handler(request: Request, exc: CircuitBreakerError):
        # Log with context for operations team
        logger.error("Circuit breaker error: %s", exc)
        # Return structured JSON — never a raw Python traceback
        return JSONResponse(
            status_code=503,
            content={"error": "Service temporarily unavailable", "detail": str(exc)},
        )

    @app.exception_handler(SQLAlchemyError)
    async def db_error_handler(request: Request, exc: SQLAlchemyError):
        logger.error("Database error: %s", exc, exc_info=True)  # exc_info=True includes stack trace in logs
        return JSONResponse(
            status_code=500,
            content={"error": "Database error", "detail": str(exc)},
        )

    @app.exception_handler(Exception)
    async def general_handler(request: Request, exc: Exception):
        logger.error("Unhandled error: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error", "detail": str(exc)},
        )
```

### 4.6 Partial Failure Tests

**Unit tests — pure state machine validation, no infrastructure required:**

```python
# tests/unit/test_circuit_breaker.py

class TestCircuitBreakerStateTransitions:
    def test_initial_state_is_closed(self):
        """Brand new breaker must start CLOSED — requests should flow."""
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_closed_to_open_after_threshold_failures(self):
        """After exactly `failure_threshold` failures, breaker must open."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=10.0)

        async def failing_func():
            raise ConnectionError("DB down")

        for _ in range(3):   # Exactly 3 failures
            with pytest.raises(ConnectionError):
                await cb.call(failing_func)

        assert cb.state == CircuitState.OPEN   # Must be OPEN now

    @pytest.mark.asyncio
    async def test_open_raises_circuit_breaker_error(self):
        """When OPEN, calls must fail immediately without touching the dependency."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60.0)

        async def failing_func():
            raise ConnectionError("DB down")

        with pytest.raises(ConnectionError):
            await cb.call(failing_func)          # First call: hits the DB, fails

        assert cb.state == CircuitState.OPEN

        with pytest.raises(CircuitBreakerError): # Second call: NEVER hits the DB
            await cb.call(failing_func)           # Fails immediately, no I/O

    @pytest.mark.asyncio
    async def test_open_to_half_open_after_timeout(self):
        """After recovery_timeout elapses, breaker must transition to HALF_OPEN."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)  # 100ms

        async def failing_func():
            raise ConnectionError()

        with pytest.raises(ConnectionError):
            await cb.call(failing_func)

        assert cb.state == CircuitState.OPEN

        await asyncio.sleep(0.15)               # Wait past recovery_timeout

        assert cb.state == CircuitState.HALF_OPEN  # Probe window is open

    @pytest.mark.asyncio
    async def test_half_open_to_closed_on_success(self):
        """A successful probe in HALF_OPEN must close the breaker and reset failure count."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)

        async def success_func():
            return "ok"

        # ... (set up OPEN state, wait for timeout) ...

        result = await cb.call(success_func)
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED   # Fully recovered

    @pytest.mark.asyncio
    async def test_half_open_to_open_on_failure(self):
        """A failed probe in HALF_OPEN must immediately re-open the breaker."""
        # ... (set up HALF_OPEN state) ...
        with pytest.raises(ConnectionError):
            await cb.call(failing_func)
        assert cb.state == CircuitState.OPEN     # Back to OPEN immediately

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self):
        """Successes must reset the failure counter — failures don't accumulate across recoveries."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=10.0)

        for _ in range(2):           # 2 failures (below threshold)
            with pytest.raises(ConnectionError):
                await cb.call(failing_func)

        assert cb._failure_count == 2

        await cb.call(success_func)  # One success
        assert cb._failure_count == 0    # Counter reset, not decremented

    @pytest.mark.asyncio
    async def test_call_passes_arguments(self):
        """The circuit breaker must be transparent — passes args and kwargs through."""
        cb = CircuitBreaker()

        async def add(a, b):
            return a + b

        assert await cb.call(add, 3, 4)      == 7
        assert await cb.call(add, a=10, b=20) == 30
```

**Integration tests — with the real running stack:**

```python
# tests/integration/test_circuit_breaker.py

class TestCircuitBreakerIntegration:
    def test_health_check_returns_healthy(self, http_client, auth_headers):
        """In normal operation, all dependencies must report healthy."""
        resp = http_client.get("/api/v1/health", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["database"] == "healthy"
        assert resp.json()["redis"]    == "healthy"

    def test_db_breaker_state_is_closed_initially(self):
        """Import the actual breaker instance and verify it starts CLOSED."""
        from app.core.circuit_breaker import db_breaker
        assert db_breaker.state == CircuitState.CLOSED

    def test_redis_breaker_state_is_closed_initially(self):
        from app.core.circuit_breaker import redis_breaker
        assert redis_breaker.state == CircuitState.CLOSED
```

---

## 5. Service Discovery

### 5.1 The Problem It Solves

In a distributed system, services need to find each other. In a static single-server world, you hardcode `127.0.0.1:5432` for the database. But in a containerised, orchestrated environment:

- IP addresses are assigned dynamically when containers start
- Multiple replicas of the same service exist at different IPs
- Services start and stop during deployments
- You cannot hardcode IPs that don't exist yet

**Service Discovery** is the mechanism by which services locate each other at runtime using stable **names** rather than volatile **IP addresses**.

### 5.2 How It Works: Docker DNS

Every Docker Compose deployment automatically gets a private DNS server. All services on the same network are registered by their service name. When NGINX looks up `worker`, Docker's DNS returns the IPs of all three worker replicas.

```yaml
# docker-compose.yml

services:
  nginx:
    networks: [clinic-net]     # Joined to this network

  worker:
    deploy:
      replicas: 3              # Three instances, three IPs
    networks: [clinic-net]     # Same network → mutually discoverable

  db:
    networks: [clinic-net]     # Registered as "db"

  redis:
    networks: [clinic-net]     # Registered as "redis"

networks:
  clinic-net:
    driver: bridge             # The bridge network hosts the embedded DNS server
```

The embedded DNS server lives at `127.0.0.11` inside each container. When `worker` starts, it resolves `db` to get the database's IP. When NGINX starts, it resolves `worker` to get all three worker IPs.

### 5.3 Where Discovery Happens in the Code

**NGINX upstream configuration:**

```nginx
# nginx/nginx.conf

upstream clinic_backend {
    hash $request_uri consistent;
    server worker:8000;    # "worker" resolved by Docker DNS
                           # Returns all 3 replica IPs
}
```

NGINX resolves `worker` at startup. When the upstream has multiple IPs (from 3 replicas), NGINX distributes across all of them using the consistent hashing algorithm.

**Worker → Database:**

```python
# app/config.py — the DNS name "db" is baked into the default URL
DATABASE_URL: str = "postgresql+asyncpg://clinic:clinicpass@db:5432/clinic_db"
#                                                              ^^
#                                              Docker service name, not an IP

# app/db/session.py — asyncpg resolves "db" via Docker DNS at connection time
engine = create_async_engine(settings.DATABASE_URL, ...)
```

**Worker → Redis:**

```python
# app/config.py
REDIS_URL: str = "redis://redis:6379/0"
#                          ^^^^^
#                   Docker service name

# app/api/v1/routers/health.py — resolved at every health check
r = aioredis.from_url(settings.REDIS_URL)
await r.ping()
```

### 5.4 Health-Check-Driven Readiness

Service discovery alone isn't enough — you need to know that a discovered service is actually **ready** to receive traffic. Docker Compose's `depends_on` with `condition: service_healthy` implements health-check-driven readiness:

```yaml
# docker-compose.yml

services:
  nginx:
    depends_on:
      worker:                       # Don't start NGINX until workers are ready
        condition: service_healthy  # "healthy" means the HEALTHCHECK passed

  worker:
    depends_on:
      db:
        condition: service_healthy   # Don't start workers until DB is ready
      redis:
        condition: service_healthy   # Don't start workers until Redis is ready
    # Worker's HEALTHCHECK is defined in the Dockerfile

  db:
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U clinic"]  # PostgreSQL-native readiness check
      interval: 5s   # Check every 5 seconds
      timeout: 3s    # Consider it failed if no response in 3 seconds
      retries: 5     # Mark unhealthy after 5 consecutive failures
```

```dockerfile
# Dockerfile — the worker's own health check
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1
```

**The startup sequence is enforced:**

```
PostgreSQL starts → pg_isready passes → service_healthy ✓
Redis starts     → redis-cli ping passes → service_healthy ✓
Workers start    → wait for db + redis healthy ✓
                 → init_db() runs, seed_data() runs
                 → GET /api/v1/health returns 200 → service_healthy ✓
NGINX starts     → worker is healthy ✓ → begins routing traffic
```

Without `condition: service_healthy`, Docker starts all services simultaneously. NGINX starts routing before workers are ready. Workers try to connect to the database before PostgreSQL has finished initialising. Everything races and fails randomly.

### 5.5 Environment-Specific Discovery

The same service names work in every environment because they're injected via environment variables:

```yaml
# docker-compose.yml (development)
environment:
  - DATABASE_URL=postgresql+asyncpg://clinic:clinicpass@db:5432/clinic_db
  - REDIS_URL=redis://redis:6379/0
```

```yaml
# docker-compose.prod.yml (production — different passwords, same names)
environment:
  - DATABASE_URL=postgresql+asyncpg://clinic:${DB_PASSWORD}@db:5432/clinic_db
  - REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
```

In a Kubernetes deployment, `db` would be replaced by a Kubernetes Service name (e.g., `clinic-postgres-service`), and the environment variable would be updated accordingly — the application code changes not at all.

---

## 6. MessagePack

### 6.1 The Problem It Solves

JSON is text. Before a server can send JSON to a client, it must serialise Python objects into a UTF-8 string. Before the client can use the data, it must parse the string back into objects. Text is inherently verbose — the string `"2026-06-15T09:00:00"` is 21 bytes. The integer `1234` in JSON is 4 bytes as text.

**MessagePack** is a binary serialisation format. `1234` encodes as 3 bytes. `"2026-06-15T09:00:00"` encodes as 20 bytes (with a 1-byte length prefix). Arrays and objects are encoded with binary length prefixes rather than ASCII brackets and commas.

MessagePack is particularly valuable for:
- High-frequency API calls (internal service-to-service)
- Mobile clients with limited bandwidth
- Large list responses (hundreds of appointments)

### 6.2 How Content Negotiation Works

The client declares what it wants via HTTP headers. The server inspects those headers and responds accordingly. This is standard HTTP content negotiation:

```
Client                          Server
  │                               │
  │── GET /api/v1/doctors ────────►│
  │   Accept: application/x-msgpack│
  │                               │
  │◄── 200 OK ────────────────────│
  │    Content-Type: application/x-msgpack
  │    Body: <binary msgpack data>
  │
```

### 6.3 The Code

```python
# app/core/middleware.py

import msgpack

class MessagePackMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        accept = request.headers.get("accept", "")
        content_type = request.headers.get("content-type", "")

        # ─── INBOUND (request body) ───────────────────────────────────────────
        # If the client sends a MessagePack-encoded request body,
        # decode it into a Python dict and store it on the request object
        if "application/x-msgpack" in content_type and request.method in ("POST", "PUT", "PATCH"):
            body = await request.body()
            try:
                request._msgpack_data = msgpack.unpackb(body, raw=False)
                # raw=False: decode byte strings as UTF-8 strings (not raw bytes)
            except Exception:
                return Response(content="Invalid MessagePack payload", status_code=400)

        # ─── TIMING ───────────────────────────────────────────────────────────
        start_time = time.monotonic()
        response = await call_next(request)
        elapsed = time.monotonic() - start_time
        response.headers["X-Response-Time"] = f"{elapsed * 1000:.2f}ms"

        # ─── OUTBOUND (response body) ─────────────────────────────────────────
        # If the client asked for MessagePack, re-encode the JSON response
        if "application/x-msgpack" in accept:
            body_bytes = b""
            async for chunk in response.body_iterator:
                body_bytes += chunk
            
            try:
                import json
                data = json.loads(body_bytes)     # Parse the JSON response
                packed = msgpack.packb(data, use_bin_type=True)
                # use_bin_type=True: encode Python str as msgpack str (not bin)
                
                return Response(
                    content=packed,
                    status_code=response.status_code,
                    headers={
                        **response.headers,
                        "content-type": "application/x-msgpack",
                        "content-length": str(len(packed)),   # Exact byte count
                    },
                )
            except Exception:
                pass  # If re-encoding fails, fall through and return original JSON

        return response
```

**Architectural note:** The middleware sits between NGINX and the route handlers. The route handlers always produce JSON (via Pydantic's `model_dump()` and `JSONResponse`). The middleware then optionally transcodes that JSON to MessagePack. This means route handlers don't need to know about MessagePack at all — the transport layer handles it transparently.

```
Route Handler → JSONResponse (always JSON)
    ↓
MessagePackMiddleware → inspects Accept header
    ├── If "application/x-msgpack": transcode to binary → return binary response
    └── Otherwise: return original JSON response unchanged
```

### 6.4 MessagePack Tests

```python
# tests/integration/test_middleware.py

import msgpack

class TestMessagePackMiddleware:
    def test_msgpack_accept_returns_binary(self, http_client, auth_headers):
        """Requesting msgpack must return a binary response with correct Content-Type."""
        resp = http_client.get(
            "/api/v1/doctors",
            headers={**auth_headers, "Accept": "application/x-msgpack"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/x-msgpack"
        
        # The body is raw bytes — unpack and verify the structure
        data = msgpack.unpackb(resp.content, raw=False)
        assert isinstance(data, list)
        assert len(data) >= 2    # Seeded doctors exist

    def test_msgpack_response_matches_json_response(self, http_client, auth_headers):
        """MessagePack and JSON must encode exactly the same data — only the format differs."""
        json_resp = http_client.get(
            "/api/v1/doctors",
            headers=auth_headers,          # Default: JSON
        )
        msgpack_resp = http_client.get(
            "/api/v1/doctors",
            headers={**auth_headers, "Accept": "application/x-msgpack"},
        )

        json_data    = json_resp.json()
        msgpack_data = msgpack.unpackb(msgpack_resp.content, raw=False)
        
        # Data identity: same content, different wire format
        assert json_data == msgpack_data

    def test_msgpack_response_has_content_length(self, http_client, auth_headers):
        """The Content-Length header must match the actual byte count of the body."""
        resp = http_client.get(
            "/api/v1/doctors",
            headers={**auth_headers, "Accept": "application/x-msgpack"},
        )
        assert "content-length" in resp.headers
        assert int(resp.headers["content-length"]) == len(resp.content)
```

---

## 7. Serialization

### 7.1 The Problem It Solves

Serialization is the process of converting in-memory data structures (Python objects, SQLAlchemy models, datetime instances) into a format that can be transmitted over a network or stored to disk. Deserialization is the reverse.

Without careful serialization design:
- A Python `datetime` object sent to the client might become `datetime.datetime(2026, 6, 15, 9, 0)` (unreadable)
- A user-submitted string like `"not-a-date"` might crash the server
- An SQLAlchemy ENUM member `AppointmentStatus.SCHEDULED` might serialize as `"SCHEDULED"` when PostgreSQL expects `"scheduled"`
- A `patient_id: "abc"` string might cause a type error deep in the ORM

Serialization in this codebase handles: **validation**, **transformation**, **type safety**, and **format negotiation**.

### 7.2 Layer 1: Pydantic — Request Deserialization and Validation

Every incoming request body passes through a Pydantic model before any business logic runs. This is FastAPI's core mechanism.

```python
# app/api/v1/routers/appointments.py

class AppointmentCreate(BaseModel):
    doctor_id: int              # Must be an integer — "abc" → 422 Unprocessable Entity
    patient_id: Union[int, str] # Accepts either: 42 or "42" (chaos trigger uses string "999")
    time_slot: str              # Accepted as string, then validated below
    duration_minutes: int = 30  # Optional with default — not required in request

    @field_validator("time_slot")
    @classmethod
    def validate_time_slot(cls, v: str) -> str:
        """
        Custom validator: ensure time_slot is a valid ISO 8601 string.
        Pydantic calls this automatically during model instantiation.
        If it raises ValueError, Pydantic catches it and returns HTTP 422.
        """
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            raise ValueError("time_slot must be a valid ISO 8601 datetime string")
        return v   # Return the original string — transformation happens in the router

    @field_validator("duration_minutes")
    @classmethod
    def validate_duration(cls, v: int) -> int:
        if v < 5 or v > 480:
            raise ValueError("duration_minutes must be between 5 and 480 (8 hours)")
        return v
```

**Why `Union[int, str]` for `patient_id`?** The chaos engineering backdoor is triggered by `patient_id = 999` (integer) or `patient_id = "999"` (string). A client might send either. Accepting both and converting to string for the comparison makes the check robust:

```python
patient_id_str = str(appt.patient_id)  # Always a string now
if patient_id_str == "999":
    raise HTTPException(status_code=503, detail="CHAOS: Simulated node failure")
```

### 7.3 Layer 2: SQLAlchemy ENUM — Database Type Mapping

PostgreSQL ENUM types are strict — they only accept the exact string values defined in the type. SQLAlchemy's default behaviour is to send the Python enum **member name** (`SCHEDULED`) rather than the **member value** (`scheduled`). PostgreSQL rejects `SCHEDULED` because the type defines `scheduled` (lowercase).

```python
# app/models/__init__.py

class AppointmentStatus(str, enum.Enum):
    SCHEDULED = "scheduled"    # member name = SCHEDULED, member value = "scheduled"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class Appointment(Base):
    status = Column(
        ENUM(
            AppointmentStatus,
            name="appointmentstatus",
            create_type=False,      # We already created the type in init_db()
            
            # This lambda is the critical fix:
            # Without it: SQLAlchemy sends "SCHEDULED" → PostgreSQL error
            # With it: SQLAlchemy sends "scheduled" → PostgreSQL accepts
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=AppointmentStatus.SCHEDULED,
    )
```

**Why `create_type=False`?** The ENUM type is created explicitly in `init_db()` using raw SQL (`CREATE TYPE appointmentstatus AS ENUM (...)`). If `create_type=True`, SQLAlchemy would try to `CREATE TYPE` again when the table is created, failing with "type already exists". Setting it to `False` tells SQLAlchemy "assume this type already exists".

### 7.4 Layer 3: Datetime — Timezone Stripping for PostgreSQL Compatibility

Python's `datetime` objects come in two flavours: **timezone-aware** (with a `tzinfo` attribute) and **timezone-naive** (no `tzinfo`). PostgreSQL's `TIMESTAMP WITHOUT TIME ZONE` column type only accepts naive datetimes. If you send a timezone-aware datetime, asyncpg raises a type error.

```python
# app/api/v1/routers/appointments.py

def _parse_time_slot(time_slot: str) -> datetime:
    """
    Convert an ISO 8601 string (possibly with timezone) into a naive UTC datetime.
    
    Examples:
    - "2026-06-15T09:00:00Z"       → datetime(2026, 6, 15, 9, 0, 0) [naive]
    - "2026-06-15T09:00:00+00:00"  → datetime(2026, 6, 15, 9, 0, 0) [naive]
    - "2026-06-15T09:00:00"        → datetime(2026, 6, 15, 9, 0, 0) [naive]
    """
    # fromisoformat() doesn't understand "Z" — replace with "+00:00" first
    dt = datetime.fromisoformat(time_slot.replace("Z", "+00:00"))
    
    # Strip tzinfo while keeping the time value (all input assumed to be UTC)
    return dt.replace(tzinfo=None) if dt.tzinfo else dt
```

This is also applied in the repository layer for defense-in-depth:

```python
# app/db/repository.py

async def create(self, ..., appointment_time: datetime, ...) -> Appointment:
    # Second stripping — in case the caller forgot to call _parse_time_slot
    naive_time = (
        appointment_time.replace(tzinfo=None)
        if appointment_time.tzinfo
        else appointment_time
    )
    appointment = Appointment(..., appointment_time=naive_time, ...)
```

### 7.5 Layer 4: Response Serialization

Outbound data goes through the reverse process — SQLAlchemy model objects are converted to Pydantic models, then to JSON (or MessagePack).

```python
# app/api/v1/routers/appointments.py

class AppointmentDetail(BaseModel):
    id: int
    doctor_id: int
    patient_id: int
    patient_name: str
    time_slot: str       # Stored as datetime, serialized as ISO 8601 string
    duration_minutes: int
    status: str          # Stored as ENUM, serialized as the string value

    model_config = {"from_attributes": True}  # Allow constructing from ORM objects

class BookingResponse(BaseModel):
    success: bool
    node_id: str
    error: str | None = None            # None serializes to JSON null
    appointment: AppointmentDetail | None = None


# In the router handler:
booking = BookingResponse(
    success=True,
    node_id=NODE_ID,
    appointment=AppointmentDetail(
        id=new_appt.id,
        doctor_id=new_appt.doctor_id,
        patient_id=new_appt.patient_id,
        patient_name=patient.name,
        # datetime → ISO 8601 string: "2026-06-15T09:00:00"
        time_slot=new_appt.appointment_time.isoformat(),
        duration_minutes=new_appt.duration_minutes,
        # AppointmentStatus.SCHEDULED → "scheduled"  (str enum)
        status=new_appt.status.value,
    ),
)

# model_dump() → Python dict → JSONResponse serializes to JSON
return JSONResponse(status_code=201, content=booking.model_dump())
```

**Why `booking.model_dump()` instead of returning the Pydantic model directly?**

FastAPI can serialize Pydantic models to JSON automatically if you return them from a route. But this booking endpoint uses `JSONResponse` explicitly (to set the status code to 201 — FastAPI defaults to 200 for success). `JSONResponse` requires a plain dict, so `.model_dump()` converts the Pydantic model to a dict first.

### 7.6 Layer 5: JWT — Token Serialization

JWTs are a specific serialization format for authentication claims:

```python
# app/core/security.py

def create_access_token(
    subject: str,
    expires_delta: timedelta | None = None,
    extra_claims: dict | None = None,    # Extensible — any claims can be added
) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode = {
        "sub": str(subject),   # Subject = username
        "exp": expire,         # Expiry = datetime object, jose encodes as Unix timestamp
    }
    if extra_claims:
        to_encode.update(extra_claims)    # Add role, or any other claims
    
    # jose.jwt.encode: Python dict → base64url(header).base64url(payload).signature
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


# Decoding in the dependency:
payload = jwt.decode(
    credentials.credentials,
    settings.SECRET_KEY,
    algorithms=[settings.ALGORITHM],   # Explicit allowlist — rejects "alg: none"
)
user_id: str = payload.get("sub")
role: str = payload.get("role", "patient")
```

**Why embed `role` in the JWT?** The alternative is to look up the user's role in the database on every request. With JWT, the role is in the token itself — no database query needed for authorization. The tradeoff: if a role changes (admin demoted to patient), the old token still shows "admin" until it expires. For this use case (30-minute tokens), that's acceptable.

### 7.7 Serialization Flow: End-to-End

```
── INBOUND ──────────────────────────────────────────────────────────────
JSON string (HTTP body)
    │
    ▼ Pydantic validation & coercion
Python dict / Pydantic model
    │
    ▼ field_validator (time_slot → datetime, duration check)
Validated Pydantic model
    │
    ▼ _parse_time_slot() → timezone strip
Naive Python datetime
    │
    ▼ SQLAlchemy ORM insert
PostgreSQL row

── OUTBOUND ─────────────────────────────────────────────────────────────
PostgreSQL row
    │
    ▼ SQLAlchemy ORM fetch → Appointment object
Python ORM object
    │
    ▼ Pydantic model construction (AppointmentDetail)
      .isoformat() for datetime → ISO 8601 string
      .value for ENUM → lowercase string
Pydantic model
    │
    ▼ .model_dump() → Python dict
Plain dict
    │
    ▼ JSONResponse → json.dumps()
JSON string (HTTP response body)
    │
    ▼ MessagePackMiddleware (if Accept: application/x-msgpack)
      json.loads() → Python dict → msgpack.packb()
Binary MessagePack (HTTP response body)
```

---

## Summary: How the 7 Concepts Work Together

These seven concepts are not independent. They form an integrated system where each one supports the others:

```
┌─────────────────────────────────────────────────────────────────────┐
│                        CLIENT REQUEST                               │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
                ┌─────────────▼─────────────┐
                │      5. SERVICE DISCOVERY  │
                │   "worker" → 3 worker IPs  │
                └─────────────┬─────────────┘
                              │
                ┌─────────────▼─────────────┐
                │   1. LOAD BALANCING       │
                │   Consistent Hash → Node   │
                │   Rate limiting 500r/s     │
                └─────────────┬─────────────┘
                              │
                ┌─────────────▼─────────────┐
                │   3. LATENCY TRACKING     │
                │   monotonic timer starts   │
                └─────────────┬─────────────┘
                              │
                ┌─────────────▼─────────────┐
                │  7. SERIALIZATION (IN)    │
                │   Pydantic validates body  │
                │   ENUM / datetime coerce   │
                └─────────────┬─────────────┘
                              │
            ┌─────────────────▼──────────────────┐
            │   2. CONCURRENCY                    │
            │   async/await — non-blocking I/O    │
            │   Connection pool (pool_size=20)    │
            │   Conflict check + IntegrityError   │
            │   Partial unique index (DB layer)   │
            └─────────────────┬──────────────────┘
                              │
                ┌─────────────▼─────────────┐
                │  4. PARTIAL FAILURE       │
                │   Circuit breaker wraps   │
                │   DB + Redis calls        │
                │   NGINX retries on 502    │
                └─────────────┬─────────────┘
                              │
                ┌─────────────▼─────────────┐
                │  7. SERIALIZATION (OUT)   │
                │   ORM → Pydantic → dict   │
                │   datetime.isoformat()    │
                │   ENUM.value              │
                └─────────────┬─────────────┘
                              │
                ┌─────────────▼─────────────┐
                │   6. MESSAGEPACK          │
                │   If Accept: x-msgpack    │
                │   JSON → binary           │
                └─────────────┬─────────────┘
                              │
                ┌─────────────▼─────────────┐
                │   3. LATENCY (header)     │
                │   X-Response-Time set     │
                └─────────────┬─────────────┘
                              │
                ┌─────────────▼─────────────┐
                │      CLIENT RESPONSE      │
                └───────────────────────────┘
```

Every request travels through all seven systems. Remove any one of them and the system degrades:
- Remove **Load Balancing**: one worker gets overwhelmed, others are idle
- Remove **Concurrency** protections: double bookings occur under load
- Remove **Latency** controls: slow queries cascade into timeouts across the fleet
- Remove **Partial Failure** handling: one database hiccup takes down the whole service
- Remove **Service Discovery**: hardcoded IPs break on every deployment
- Remove **MessagePack**: bandwidth-sensitive clients have no efficient option
- Remove **Serialization** validation: malformed input reaches the database

Together, they make the system correct, fast, observable, and resilient — which is exactly what a production medical scheduling service must be.
