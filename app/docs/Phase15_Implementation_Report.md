# Phase 15 — Appointment Lifecycle Completions

## Status: Active (Sub-Phase 15-C Complete)

---

## Sub-Phase 15-A: Cancellation Reasons ✅

### Objective
Add `cancellation_reason`, `cancelled_at`, and `cancelled_by` fields to appointments so the reason for cancellation is captured for analytics (FR-APT-10).

### Changes

#### 1. Model (`app/models/__init__.py`)
Added three nullable columns to `Appointment`:
```python
cancellation_reason = Column(String(500), nullable=True)
cancelled_at = Column(DateTime, nullable=True)
cancelled_by = Column(String(100), nullable=True)
```

#### 2. Router (`app/api/v1/routers/appointments.py`)
- **`StatusUpdate` schema** — Added optional `cancellation_reason: str | None = None`
- **`PATCH /appointments/{id}/status` handler** — When `new_status == CANCELLED`:
  - Sets `updated.cancellation_reason = req.cancellation_reason`
  - Sets `updated.cancelled_at = datetime.utcnow()`
  - Sets `updated.cancelled_by = username` (from JWT)
  - Includes `cancellation_reason` in audit log details

#### 3. Database
```sql
ALTER TABLE appointments ADD COLUMN cancellation_reason VARCHAR(500);
ALTER TABLE appointments ADD COLUMN cancelled_at TIMESTAMP;
ALTER TABLE appointments ADD COLUMN cancelled_by VARCHAR(100);
```

### Files Changed
| File | Change |
|---|---|
| `app/models/__init__.py` | +3 columns on `Appointment` |
| `app/api/v1/routers/appointments.py` | Extended `StatusUpdate` + handler logic |

### Tests
- Full suite: **130/130 passed**
- No regressions
- Ruff format: clean

---

## Sub-Phase 15-B: `/appointments/for-me` Endpoint ✅

### Objective
Provide a convenience booking endpoint for authenticated patient users — infers `patient_id` from the user-patient FK linkage instead of requiring the patient to supply it manually.

### Changes

#### 1. Request Model (`app/api/v1/routers/appointments.py`)
Added `AppointmentForMeCreate(BaseModel)`:
- `doctor_id: int`
- `time_slot: str` (ISO 8601, same validator as `AppointmentCreate`)
- `duration_minutes: int = 30` (same validator, 5–480 range)
- **No `patient_id` field** — inferred from JWT

#### 2. New Endpoint — `POST /api/v1/appointments/for-me`
```python
@router.post("/for-me")
async def book_for_me(...):
```
- Looks up `User` by `username` (from JWT `sub`) + `tenant_id`
- Looks up `Patient` by `Patient.user_id == user.id`
- Returns **400** if no linked patient profile found
- Delegates to the existing `create_appointment` function with the resolved `patient_id`

#### 3. `next_reminder_at` Population
Both `POST /appointments` and `POST /appointments/for-me` now set:
```python
new_appt.next_reminder_at = appointment_time - timedelta(hours=24)
```
This primes the `next_reminder_at` column for the reminder scheduler (15-C).

### Files Changed
| File | Change |
|---|---|
| `app/api/v1/routers/appointments.py` | +`AppointmentForMeCreate` model, +`POST /for-me` endpoint, `next_reminder_at` set on creation |

### Tests
- Full suite: **129 passed, 1 skipped** (pre-existing slot contention)
- No regressions
- Ruff format: clean

---

## Sub-Phase 15-C: Reminder Scheduler Container ✅

### Objective
Wire up the dead reminder code (`send_reminder_email`, `get_due_reminders`, `mark_reminder_sent`) into a running standalone Docker container that polls every 5 minutes.

### Changes

#### 1. New Files

**`app/scheduler/__init__.py`** — Empty package init.

**`app/scheduler/reminders.py`** — Infinite-loop scheduler:
- `send_due_reminders()` — Opens DB session, calls `get_due_reminders()`, sends email via `send_reminder_email()`, marks sent
- `run_reminder_loop()` — Loops with 5-minute `asyncio.sleep`, handles exceptions gracefully
- Module entry point: `python -m app.scheduler.reminders`

**`app/scheduler/reminders_once.py`** — Single-run variant for K8s CronJob:
- Calls `send_due_reminders()` once and exits

#### 2. Repository (`app/db/repository.py`)
- **`get_due_reminders()`** — Added `Appointment.next_reminder_at <= now` filter to respect the scheduling window
- **`mark_reminder_sent()`** — Now also clears `next_reminder_at = None` after sending

#### 3. Docker Compose (`docker-compose.yml`)
Added new service:
```yaml
reminder-scheduler:
  build: .
  command: python -m app.scheduler.reminders
  environment:
    - DATABASE_URL=postgresql+asyncpg://clinic:clinicpass@db:5432/clinic_db
    - REDIS_URL=redis://:redispass@redis:6379/0
  depends_on:
    db:
      condition: service_healthy
  networks:
    - clinic-net
  restart: unless-stopped
```

### Files Changed
| File | Change |
|---|---|
| `app/scheduler/__init__.py` | New — package init |
| `app/scheduler/reminders.py` | New — infinite-loop scheduler |
| `app/scheduler/reminders_once.py` | New — single-run K8s variant |
| `app/db/repository.py` | `get_due_reminders()` adds `next_reminder_at` filter; `mark_reminder_sent()` clears `next_reminder_at` |
| `docker-compose.yml` | New `reminder-scheduler` service |

### Tests
- Full suite: **127 passed, 3 skipped** (pre-existing)
- No regressions
- Ruff format: clean

---

## Upcoming Sub-Phases

| Sub-Phase | Status | Estimate |
|---|---|---|
| 15-D: Frontend Portal Completions | Pending | 2 days |
| 15-E: Tests | Pending | 1 day |
| **Total** | | **~7 days** |
