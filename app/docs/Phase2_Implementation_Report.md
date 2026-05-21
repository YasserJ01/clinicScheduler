# Phase 2 — Appointment Booking Engine: Implementation Report

| Field | Value |
|---|---|
| Document Version | 1.0.0 |
| Status | **Completed and Verified** |
| Verified By | Engineering Team |
| Date | 2026-05-21 |
| Classification | Internal / Technical |

---

## 1. Phase 2 Objectives

Deliver the full appointment booking flow, including conflict detection, all validation rules, chaos engineering backdoor, and the complete `BookingResponse` schema. This is the core business value of the system.

---

## 2. Implementation Summary

### 2.1 Files Delivered

| File | Purpose |
|---|---|
| `app/db/session.py` | Added `_create_partial_unique_index()` for race condition prevention |
| `app/api/v1/routers/appointments.py` | Added `IntegrityError` handling for concurrent booking conflicts |
| `app/api/v1/routers/patients.py` | Added `POST /patients` endpoint for patient creation (required for booking) |
| `tests/integration/test_appointments.py` | 14 integration tests: booking success/conflict/validation, list, get by ID |
| `tests/integration/test_concurrent_booking.py` | 1 integration test: concurrent same-slot booking (201 + 409) |
| `tests/integration/test_timezone.py` | 5 integration tests: Z suffix, UTC offset, naive datetime, invalid strings |
| `tests/conftest.py` | Added `patient_id`, `seeded_doctor_id`, `future_time_slot` fixtures |

### 2.2 Appointment Booking Flow

```
POST /appointments → chaos check (patient_id=999 → 503)
                   → parse time_slot (strip timezone)
                   → validate doctor exists (400 if not)
                   → check_conflict() (409 if slot occupied)
                   → validate patient exists (404 if not)
                   → create appointment (IntegrityError → 409)
                   → return 201 with BookingResponse
```

### 2.3 Concurrency Prevention

**Problem:** The two-step `check_conflict()` → `create()` operation is not atomic at the SQL level. Under concurrent load, two requests can both pass `check_conflict()` and both attempt `create()`.

**Solution (defense in depth):**
1. **Application-level:** `check_conflict()` queries for existing non-cancelled appointments at the same `(doctor_id, appointment_time)`.
2. **Database-level:** Partial unique index `uix_appointment_slot` on `(doctor_id, appointment_time) WHERE status != 'cancelled'` prevents duplicate inserts.
3. **Error handling:** `IntegrityError` from concurrent inserts is caught in the router, re-queried for the conflicting appointment, and returned as HTTP 409 with the conflict response.

### 2.4 Role-Based Access Control

| Endpoint | Auth Required | Role Required |
|---|---|---|
| `POST /appointments` | Yes | Any |
| `GET /appointments` | Yes | Any |
| `GET /appointments/{id}` | Yes | Any |
| `POST /patients` | Yes | Any |

---

## 3. Acceptance Criteria Verification

| Criterion | Expected | Actual | Status |
|---|---|---|---|
| Book appointment — success | HTTP 201, `success: true`, `appointment` populated | Verified via integration tests | **PASS** |
| Book same slot again | HTTP 409, names existing patient | Verified via integration tests | **PASS** |
| Book with invalid doctor ID | HTTP 400, `"Doctor not found"` | Verified via integration tests | **PASS** |
| Book with invalid patient ID | HTTP 404, `"Patient with id X not found"` | Verified via integration tests | **PASS** |
| Book with malformed `time_slot` | HTTP 422, Pydantic validation error | Verified via integration tests | **PASS** |
| Book with `patient_id: 999` | HTTP 503, `"CHAOS: Simulated node failure"` | Verified via integration tests | **PASS** |
| Concurrent bookings (same slot, 2 workers) | One 201, one 409; no duplicates | Verified via concurrent test | **PASS** |
| List appointments returns all bookings | HTTP 200, ordered by `appointment_time` | Verified via integration tests | **PASS** |
| Get appointment by valid ID | HTTP 200, full `AppointmentDetail` | Verified via integration tests | **PASS** |
| Get appointment by invalid ID | HTTP 404 | Verified via integration tests | **PASS** |
| `node_id` reflects actual container hostname | Matches `socket.gethostname()` | Verified via integration tests | **PASS** |

---

## 4. Smoke Test Results

### 4.1 Appointment Tests

| Test | Status Code | Response |
|---|---|---|
| `POST /appointments` (success) | 201 | `{"success":true,"node_id":"...","error":null,"appointment":{...}}` |
| `POST /appointments` (conflict) | 409 | `{"success":false,"error":"Slot already occupied by patient Test Patient",...}` |
| `POST /appointments` (invalid doctor) | 400 | `{"success":false,"error":"Doctor not found",...}` |
| `POST /appointments` (invalid patient) | 404 | `{"success":false,"error":"Patient with id 9999 not found",...}` |
| `POST /appointments` (chaos: patient_id=999) | 503 | `{"detail":"CHAOS: Simulated node failure"}` |
| `GET /appointments` | 200 | List of all appointments ordered by time |
| `GET /appointments/1` | 200 | Full appointment detail |

### 4.2 Timezone Tests

| Test | Input | Result |
|---|---|---|
| Z suffix | `"2027-05-01T08:00:00Z"` | HTTP 201 |
| UTC offset | `"2027-05-01T09:00:00+00:00"` | HTTP 201 |
| Naive datetime | `"2027-05-01T10:00:00"` | HTTP 201 |
| Invalid string | `"not-a-date"` | HTTP 422 |
| Empty string | `""` | HTTP 422 |

---

## 5. Automated Test Suite

### 5.1 New Tests Added (Phase 2)

**test_appointments.py (14 tests):**

| Test Class | Tests | Coverage |
|---|---|---|
| `TestBookAppointment` | 8 | Success (1), conflict (1), invalid doctor (1), invalid patient (1), malformed timeslot (1), chaos trigger (1), unauthenticated (1), node_id hostname (1) |
| `TestListAppointments` | 3 | Authenticated list (1), unauthenticated (1), ordered by time (1) |
| `TestGetAppointment` | 3 | By valid ID (1), invalid ID (1), unauthenticated (1) |

**test_concurrent_booking.py (1 test):**

| Test | Coverage |
|---|---|
| `test_concurrent_same_slot_one_succeeds` | Two simultaneous requests for same slot → one 201, one 409, exactly 1 appointment in DB |

**test_timezone.py (5 tests):**

| Test | Coverage |
|---|---|
| `test_book_with_z_suffix` | ISO 8601 with `Z` suffix accepted |
| `test_book_with_utc_offset` | ISO 8601 with `+00:00` offset accepted |
| `test_book_with_naive_datetime` | Naive datetime string (no tz) accepted |
| `test_book_with_invalid_timeslot` | Non-ISO string rejected (422) |
| `test_book_with_empty_timeslot` | Empty string rejected (422) |

### 5.2 New Fixtures (`tests/conftest.py`)

| Fixture | Scope | Purpose |
|---|---|---|
| `patient_id` | session | Creates a patient via `POST /patients`, returns the ID |
| `seeded_doctor_id` | session | Returns ID of a seeded doctor (1) |
| `future_time_slot` | function | Returns a future ISO 8601 timestamp string (7 days ahead) |

---

## 6. Technical Notes

### 6.1 Partial Unique Index

```sql
CREATE UNIQUE INDEX uix_appointment_slot
ON appointments (doctor_id, appointment_time)
WHERE status != 'cancelled';
```

Created idempotently in `init_db()` via `_create_partial_unique_index()`. Catches `IntegrityError` if index already exists.

### 6.2 IntegrityError Handling

In `create_appointment`, the `appt_repo.create()` call is wrapped in `try/except IntegrityError`. On catch:
1. Roll back the session
2. Re-query for the conflicting appointment via `check_conflict()`
3. Fetch the patient name for the error message
4. Return HTTP 409 with the conflict response

### 6.3 Patient Creation Endpoint

Added `POST /patients` to `app/api/v1/routers/patients.py`. Uses `get_or_create_by_name()` for idempotent patient creation. Returns existing patient if name already exists.

### 6.4 Timezone Stripping

All time parsing uses `_parse_time_slot()`:
```python
def _parse_time_slot(time_slot: str) -> datetime:
    dt = datetime.fromisoformat(time_slot.replace("Z", "+00:00"))
    return dt.replace(tzinfo=None) if dt.tzinfo else dt
```

This ensures timezone-aware datetimes (from `Z` or `+00:00`) are converted to naive datetimes before DB insertion, matching the `TIMESTAMP WITHOUT TIME ZONE` column type.

---

## 7. Phase 2 Quality Gates

| Gate | Status |
|---|---|
| Docker Compose build succeeds from clean checkout | **PASS** |
| `GET /api/v1/health` returns 200 | **PASS** |
| Booking integration tests pass (success, conflict, validation) | **PASS** |
| Chaos backdoor test passes (503 response) | **PASS** |
| Concurrent booking test passes (one 201, one 409) | **PASS** |
| Timezone handling tests pass (Z, offset, naive, invalid) | **PASS** |
| Partial unique index created in `init_db()` | **PASS** |
| `IntegrityError` caught and returned as 409 | **PASS** |
| All endpoints documented in Swagger UI | **PASS** |
| `AGENTS.md` updated with concurrency and patient creation gotchas | **PASS** |

---

## 8. Next Steps (Phase 3)

Phase 3 will deliver resilience, observability, and chaos engineering:
- `MessagePackMiddleware` — content negotiation and `X-Response-Time` header injection (already implemented, needs automated tests)
- `CircuitBreaker` — full state machine tests (CLOSED → OPEN → HALF_OPEN)
- Chaos backdoor automated tests
- NGINX retry configuration validation
- Structured logging review

---

*End of Phase 2 Implementation Report*
