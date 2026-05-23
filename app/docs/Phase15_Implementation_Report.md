# Phase 15 — Appointment Lifecycle Completions

## Status: Active (Sub-Phase 15-A Complete)

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

## Upcoming Sub-Phases

| Sub-Phase | Status | Estimate |
|---|---|---|
| 15-B: `/appointments/for-me` Endpoint | Pending | 1 day |
| 15-C: Reminder Scheduler Container | Pending | 2 days |
| 15-D: Frontend Portal Completions | Pending | 2 days |
| 15-E: Tests | Pending | 1 day |
| **Total** | | **~7 days** |
