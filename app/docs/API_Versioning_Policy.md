# API Versioning Policy

## Overview

The Clinic Scheduler API uses URI-based versioning (`/api/v1/`, `/api/v2/`). This document defines the versioning strategy, deprecation timeline, and migration path for clients.

## Version Strategy

| Version | Status | Path |
|---------|--------|------|
| v1 | Deprecated | `/api/v1/` |
| v2 | Current | `/api/v2/` |

## Deprecation Headers

All v1 endpoints include the following HTTP response headers:

```
Deprecation: true
Sunset: 2027-01-01T00:00:00Z
Link: </api/v2/>; rel="successor-version"
```

## v1 → v2 Migration Guide

### Appointments

| v1 Endpoint | v2 Endpoint | Change |
|-------------|-------------|--------|
| `GET /api/v1/appointments` | `GET /api/v2/appointments` | Paginated response is now the default |
| `POST /api/v1/appointments` | `POST /api/v1/appointments` | No change (v2 uses same booking flow) |
| `GET /api/v1/appointments/{id}` | `GET /api/v1/appointments/{id}` | No change |

### Doctors

| v1 Endpoint | v2 Endpoint | Change |
|-------------|-------------|--------|
| `GET /api/v1/doctors` | `GET /api/v2/doctors` | Response includes `schedule` array |
| `GET /api/v1/doctors/{id}` | `GET /api/v1/doctors/{id}` | No change |

## Timeline

| Date | Milestone |
|------|-----------|
| 2026-05-22 | v2 endpoints launched |
| 2026-08-01 | v1 deprecation warnings logged in admin dashboard |
| 2026-11-01 | v1 rate-limited to 50% of normal quota |
| 2027-01-01 | v1 endpoints removed (sunset date) |

## Adding New Versions

When creating v3 (or later):

1. Create `app/api/v3/` package with updated routers
2. Wire v3 routers in `app/main.py`
3. Update deprecation middleware to add `Deprecation` headers to v2
4. Update this document with the new timeline
5. Maintain backward compatibility for at least 6 months
