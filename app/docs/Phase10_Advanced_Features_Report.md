# Phase 10 Advanced Feature Delivery — Report

## Summary
Phase 10 delivers the high and medium priority feature extensions from the Phase 6 deferred list: doctor availability windows, email notifications, appointment notes, recurring appointments, NGINX dynamic upstream resolution, API v2 versioning strategy, and enhanced load testing. All 144 tests pass, ruff lint/format clean.

## Changes

### 1. Doctor Availability Windows

**New Model:** `DoctorSchedule` in `app/models/__init__.py`
- `doctor_schedules` table with `doctor_id`, `day_of_week` (0=Monday, 6=Sunday), `start_time`, `end_time`, `is_active`
- Unique constraint on `(doctor_id, day_of_week)`
- FK to `doctors(id)` with cascade delete

**Alembic Migration:** `006_doctor_schedules.py`

**Repository Methods** in `DoctorRepository`:
- `get_schedule(doctor_id)` — list all active schedule windows
- `set_schedule(doctor_id, schedules)` — replace entire schedule (deletes existing, inserts new)
- `update_schedule_day(doctor_id, day_of_week, **fields)` — update single day
- `delete_schedule_day(doctor_id, day_of_week)` — remove a day
- `get_schedule_for_date(doctor_id, date)` — return schedule for a specific date's weekday

**New Endpoints** in `app/api/v1/routers/doctors.py`:
| Method | Path | Access | Description |
|--------|------|--------|-------------|
| `GET` | `/doctors/{id}/schedule` | Any authenticated | List schedule windows |
| `PUT` | `/doctors/{id}/schedule` | Admin only | Replace entire schedule |
| `PATCH` | `/doctors/{id}/schedule/{day}` | Admin only | Update single day |
| `DELETE` | `/doctors/{id}/schedule/{day}` | Admin only | Remove a day |

**Impact on `GET /appointments/available`:**
- Queries doctor's schedule for the requested date's weekday
- If schedule exists → uses `start_time`/`end_time` from schedule
- If no schedule → falls back to default 08:00–17:00
- Response includes `schedule_based: true/false` to indicate which mode was used

### 2. Email Notifications

**New Module:** `app/core/email.py`
- `EmailService` abstract base class with `async def send(to, subject, body)`
- `NullEmailService` (default) — logs email attempts, no-op delivery
- `SMTPEmailService` — uses `aiosmtplib` for async SMTP delivery
- `SendGridEmailService` — uses `httpx` for async HTTP API calls

**Config Additions** in `app/config.py`:
```python
EMAIL_PROVIDER: str = "null"       # "smtp", "sendgrid", "null"
SMTP_HOST: str = "localhost"
SMTP_PORT: int = 1025
SENDGRID_API_KEY: str = ""
FROM_EMAIL: str = "clinic@example.com"
```

**Email Functions:**
- `send_booking_confirmation(patient_email, appointment)` — triggered on booking creation
- `send_cancellation_email(patient_email, appointment)` — triggered on status → cancelled
- `send_confirmation_email(patient_email, appointment)` — triggered on status → confirmed
- `send_reminder_email(patient_email, appointment)` — for 24-hour reminders (future use)

**Integration Points:**
- `POST /appointments` → `BackgroundTasks.add_task(send_booking_confirmation, ...)`
- `PATCH /appointments/{id}/status` (cancelled) → `send_cancellation_email`
- `PATCH /appointments/{id}/status` (confirmed) → `send_confirmation_email`

**Reminder System:**
- Added `next_reminder_at` and `reminder_sent` columns to `appointments` table
- `AppointmentRepository.get_due_reminders()` — queries appointments within 24 hours not yet reminded
- `AppointmentRepository.mark_reminder_sent(appointment_id)` — marks reminder as sent

### 3. Appointment Notes

**New Endpoint:** `PATCH /appointments/{id}/notes`
- Request body: `{"notes": "..."}`
- RBAC: doctor or admin only (patients receive HTTP 403)
- Audit-logged on every change
- Notes field already existed in `Appointment` model; now exposed via API

**Repository Method:** `AppointmentRepository.update_notes(appointment_id, notes)`

### 4. Recurring Appointments

**New Model:** `RecurringSeries` in `app/models/__init__.py`
- `recurring_series` table with `doctor_id`, `patient_id`, `recurrence` (weekly/biweekly/monthly)
- `Appointment` model updated with `series_id` FK, `next_reminder_at`, `reminder_sent` columns

**Alembic Migration:** `007_recurring_appointments.py`

**New Endpoints** in `app/api/v1/routers/appointments.py`:
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/appointments/recurring` | Create recurring appointment series |
| `DELETE` | `/appointments/series/{series_id}` | Cancel all remaining appointments in series |

**`POST /appointments/recurring` Request:**
```json
{
  "doctor_id": 1,
  "patient_id": 42,
  "start_time": "2026-07-01T09:00:00Z",
  "duration_minutes": 30,
  "recurrence": "weekly",
  "occurrences": 12
}
```

**Response:**
```json
{
  "series_id": 1,
  "recurrence": "weekly",
  "created": [{"id": 1, "time_slot": "..."}, ...],
  "conflicts": [{"time_slot": "...", "reason": "Slot already occupied"}],
  "total_requested": 12,
  "total_created": 10,
  "total_conflicts": 2
}
```

**Recurrence Logic:**
- `weekly`: +7 days per occurrence
- `biweekly`: +14 days per occurrence
- `monthly`: +1 month (capped at day 28 to avoid month-end overflow)

**`DELETE /appointments/series/{series_id}`:**
- Cancels all appointments with status `scheduled` or `confirmed` in the series
- Returns `{"series_id": N, "cancelled_count": M}`
- RBAC: admin or patient only

### 5. NGINX Dynamic Upstream Resolution

**Problem (BUG-09 resolution):** NGINX resolves Docker Compose service names at startup. When workers restart or scale, stale IPs may be cached.

**Fix** in `nginx/nginx.conf`:
- Added `resolver 127.0.0.11 valid=5s` (Docker's embedded DNS)
- Replaced `upstream clinic_backend { server worker:8000; }` with variable-based resolution
- All location blocks now use `set $backend http://worker:8000; proxy_pass $backend;`
- Compatible with worker restarts, scaling, and IP changes without NGINX reload

### 6. API Versioning Strategy

**New Package:** `app/api/v2/`
- `app/api/v2/__init__.py`
- `app/api/v2/routers/__init__.py`
- `app/api/v2/routers/appointments.py` — paginated response by default, includes `series_id`
- `app/api/v2/routers/doctors.py` — list response includes `schedule` array per doctor

**Deprecation Middleware:** `app/core/deprecation_middleware.py`
- Adds `Deprecation: true` header to all `/api/v1/` responses
- Adds `Sunset: 2027-01-01T00:00:00Z` header
- Adds `Link: </api/v2/>; rel="successor-version"` header
- v2 responses have no deprecation headers

**Policy Document:** `app/docs/API_Versioning_Policy.md`
- Defines versioning strategy, deprecation timeline, migration guide
- Timeline: v2 launched 2026-05-22, v1 sunset 2027-01-01

### 7. Enhanced Load Testing

**Changes** in `loadtest/scheduler.js`:
- **BUG-10 fix:** `setup()` now throws error if `token` or `patientId` is null (was silently returning)
- **Mixed scenario:** 70% reads (GET /doctors), 20% bookings (POST /appointments), 10% status updates (PATCH /status)
- **New metric:** `bookings_per_second` custom rate metric
- **Updated thresholds:** `bookings_per_second: ['rate>0.1']`
- **Unwrapped paginated responses:** `doctors.items` instead of flat array

## Test Results
- **Unit tests:** 40 passed (includes 7 new email tests)
- **Integration tests:** 104 passed (includes 20 new Phase 10 tests)
- **Total:** 144 passed, 5 skipped
- **Ruff:** All checks passed, 58 files already formatted

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

## New Files
| Path | Purpose |
|------|---------|
| `alembic/versions/006_doctor_schedules.py` | Migration for doctor_schedules table |
| `alembic/versions/007_recurring_appointments.py` | Migration for recurring_series + appointment columns |
| `app/api/v2/__init__.py` | v2 API package |
| `app/api/v2/routers/__init__.py` | v2 routers package |
| `app/api/v2/routers/appointments.py` | v2 appointments router (paginated) |
| `app/api/v2/routers/doctors.py` | v2 doctors router (includes schedule) |
| `app/core/deprecation_middleware.py` | Deprecation headers for v1 endpoints |
| `app/core/email.py` | Email service abstraction |
| `app/docs/API_Versioning_Policy.md` | API versioning strategy document |
| `tests/integration/test_phase10.py` | 20 integration tests for Phase 10 features |
| `tests/unit/test_email.py` | 7 unit tests for email services |

## Modified Files
| Path | Changes |
|------|---------|
| `app/models/__init__.py` | Added `DoctorSchedule`, `RecurringSeries` models; added columns to `Appointment` |
| `app/db/repository.py` | Added schedule CRUD, recurring series, notes, reminder methods |
| `app/db/session.py` | Imported new models for `create_all` |
| `app/config.py` | Added email configuration fields |
| `app/main.py` | Wired v2 routers, deprecation middleware |
| `app/api/v1/routers/appointments.py` | Added recurring endpoints, notes endpoint, email background tasks, schedule-aware available slots |
| `app/api/v1/routers/doctors.py` | Added schedule CRUD endpoints |
| `nginx/nginx.conf` | Dynamic upstream resolution with Docker DNS resolver |
| `loadtest/scheduler.js` | Mixed scenario, fail-fast setup, bookings_per_second metric |
| `app/docs/AGENTS.md` | Added Phase 10 documentation sections |

## Next Steps (Phase 11)
- Admin Dashboard API (`/api/v1/admin/analytics/`)
- Patient Self-Service Portal (single-file HTML/JS frontend)
- Doctor Mobile App API Extensions
- Webhook Notifications (HMAC-SHA256 signed, retry policy)
- Multi-Tenant Support (`clinic_id` on all entities)
