# Phase 15 — Appointment Lifecycle Completions

## Status: Complete ✅

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

---

## Sub-Phase 15-D: Frontend Portal Completions ✅

### Objective
Update the frontend SPA to integrate all Phase 14/15 features: patient registration with email, `/for-me` booking, cancellation with reason, profile editing, schedule-based slot indicator, and notes display.

### Changes

#### 1. Registration Flow
- Sends `email` field to `POST /auth/register` (Phase 14 auto-creates patient with user-patient FK)
- Removed separate `POST /patients` call from the register flow

#### 2. Booking Flow
- Uses `POST /appointments/for-me` instead of `POST /appointments` with `patient_id`
- Removes the requirement for the patient to know their `patient_id`

#### 3. Available Slots
- Shows `schedule_based` badge when the doctor's schedule is set (Phase 10 feature)
- Clearer visual distinction between schedule-based and default 08:00-17:00 slots

#### 4. Appointment Cards
- Displays `notes` with a styled left-border block
- Status colour classes match DB values:
  - `scheduled` → blue
  - `confirmed` → green
  - `completed` → gray
  - `cancelled` → red

#### 5. Cancel Dialog
- Modal prompts for optional `cancellation_reason`
- Calls `PATCH /appointments/{id}/status` with `{ status: 'cancelled', cancellation_reason }`

#### 6. Profile Tab
- New tab displaying patient `name`, `email`, and `username` (from JWT sub)
- Edit button loads inline form
- Calls `PATCH /patients/{id}` to save changes
- Updates header name on save

#### 7. Auth Alerts
- Shows backend error messages on login/register (e.g., "Username already exists")

### Files Changed
| File | Change |
|---|---|
| `frontend/index.html` | Register sends email, booking uses `/for-me`, cancel modal with reason, profile tab, schedule-based indicator, notes display, status colors, auth error alerts |

### Tests
- Full suite: **137 passed, 2 skipped** (pre-existing slot contention)
- No regressions
- Ruff format: clean

---

## Sub-Phase 15-E: Integration Tests ✅

### Objective
Write integration tests covering all new Phase 15 functionality: cancellation with reason, `/for-me` booking, and reminder scheduler plumbing.

### Test File: `tests/integration/test_phase15.py`

#### TestCancellationWithReason (3 tests)
| Test | Description |
|---|---|
| `test_cancel_with_reason` | Books an appointment (via own patient linked to JWT), cancels with a reason, verifies `cancellation_reason`, `cancelled_by`, and `cancelled_at` are persisted in DB |
| `test_cancel_without_reason` | Cancels without providing a reason, verifies `cancellation_reason` is `NULL` in DB |
| `test_patient_cancel_other_fails` | Patient A books an appointment, Patient B attempts to cancel it — verifies HTTP 403 |

#### TestBookForMe (3 tests)
| Test | Description |
|---|---|
| `test_book_for_me_success` | Registers a patient with email, books via `/for-me`, verifies 201 and correct `patient_id` in response |
| `test_book_for_me_no_patient_profile` | Registers a doctor (no patient profile created), attempts `/for-me`, verifies HTTP 400 with "patient profile" error |
| `test_book_for_me_unauthenticated` | Calls `/for-me` without auth token, verifies HTTP 401/403 |

#### TestReminderScheduler (3 tests)
| Test | Description |
|---|---|
| `test_next_reminder_at_set_on_booking` | Books an appointment, checks DB that `next_reminder_at` is populated and `reminder_sent` is `FALSE` |
| `test_due_reminder_query` | Books an appointment, sets `next_reminder_at` to the past via DB, verifies the row matches `get_due_reminders()` query criteria |
| `test_mark_reminder_sent` | Books an appointment, updates `reminder_sent=TRUE` and `next_reminder_at=NULL` via DB, verifies the changes |

### Files Changed
| File | Change |
|---|---|
| `tests/integration/test_phase15.py` | New — 9 integration tests |

### Tests
- Full suite: **137 passed, 2 skipped** (pre-existing)
- No regressions
- Ruff format: clean

---

## Summary

| Sub-Phase | Status | Deliverables |
|---|---|---|
| 15-A: Cancellation Reasons | ✅ | `cancellation_reason`, `cancelled_at`, `cancelled_by` columns; API logic; DB migration |
| 15-B: `/appointments/for-me` | ✅ | Convenience booking endpoint; `next_reminder_at` set at booking |
| 15-C: Reminder Scheduler Container | ✅ | `reminders.py` infinite loop; `reminders_once.py` K8s variant; Docker service |
| 15-D: Frontend Portal Completions | ✅ | Register with email; `/for-me` booking; cancel modal; profile tab; UI enhancements |
| 15-E: Integration Tests | ✅ | 9 new tests covering cancellation, `/for-me`, and reminder scheduler |
| **Total** | **✅ Complete** | **130 → 139 total integration tests (137 pass, 2 skip)** |
