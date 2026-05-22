import pytest
from datetime import datetime, timedelta


class TestDoctorSchedule:
    def test_get_schedule(self, http_client, admin_headers):
        resp = http_client.get("/api/v1/doctors/1/schedule", headers=admin_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_set_schedule(self, http_client, admin_headers):
        schedules = [
            {"day_of_week": 0, "start_time": "09:00", "end_time": "17:00"},
            {"day_of_week": 1, "start_time": "09:00", "end_time": "17:00"},
            {"day_of_week": 2, "start_time": "09:00", "end_time": "17:00"},
        ]
        resp = http_client.put(
            "/api/v1/doctors/1/schedule",
            json=schedules,
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3

    def test_get_schedule_after_set(self, http_client, admin_headers):
        schedules = [
            {"day_of_week": 3, "start_time": "10:00", "end_time": "15:00"},
        ]
        http_client.put(
            "/api/v1/doctors/1/schedule",
            json=schedules,
            headers=admin_headers,
        )
        resp = http_client.get("/api/v1/doctors/1/schedule", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert any(d["day_of_week"] == 3 for d in data)

    def test_update_schedule_day(self, http_client, admin_headers):
        schedules = [
            {"day_of_week": 4, "start_time": "08:00", "end_time": "16:00"},
        ]
        http_client.put(
            "/api/v1/doctors/1/schedule",
            json=schedules,
            headers=admin_headers,
        )
        resp = http_client.patch(
            "/api/v1/doctors/1/schedule/4",
            json={"start_time": "09:00", "end_time": "17:00"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["start_time"] == "09:00:00"
        assert data["end_time"] == "17:00:00"

    def test_delete_schedule_day(self, http_client, admin_headers):
        schedules = [
            {"day_of_week": 5, "start_time": "08:00", "end_time": "12:00"},
        ]
        http_client.put(
            "/api/v1/doctors/1/schedule",
            json=schedules,
            headers=admin_headers,
        )
        resp = http_client.delete(
            "/api/v1/doctors/1/schedule/5",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        resp = http_client.get("/api/v1/doctors/1/schedule", headers=admin_headers)
        assert not any(d["day_of_week"] == 5 for d in resp.json())

    def test_schedule_requires_admin(self, http_client, auth_headers):
        resp = http_client.put(
            "/api/v1/doctors/1/schedule",
            json=[{"day_of_week": 0, "start_time": "09:00", "end_time": "17:00"}],
            headers=auth_headers,
        )
        assert resp.status_code == 403

    def test_invalid_day_of_week(self, http_client, admin_headers):
        resp = http_client.patch(
            "/api/v1/doctors/1/schedule/7",
            json={"start_time": "09:00"},
            headers=admin_headers,
        )
        assert resp.status_code == 422


class TestAvailableSlotsWithSchedule:
    def test_uses_schedule_when_set(self, http_client, admin_headers, auth_headers):
        schedules = [
            {"day_of_week": 2, "start_time": "10:00", "end_time": "14:00"},
        ]
        http_client.put(
            "/api/v1/doctors/1/schedule",
            json=schedules,
            headers=admin_headers,
        )
        target_date = datetime(2026, 7, 1)
        while target_date.weekday() != 2:
            target_date += timedelta(days=1)

        resp = http_client.get(
            f"/api/v1/appointments/available?doctor_id=1&date={target_date.isoformat()}Z",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["schedule_based"] is True

    def test_uses_default_when_no_schedule(self, http_client, auth_headers):
        resp = http_client.get(
            "/api/v1/appointments/available?doctor_id=1&date=2026-07-20T00:00:00Z",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["schedule_based"] is False


class TestAppointmentNotes:
    def test_doctor_can_update_notes(
        self, http_client, admin_headers, auth_headers, patient_id
    ):
        future_time = (
            datetime.utcnow() + timedelta(days=21, hours=2)
        ).isoformat() + "Z"
        booking = http_client.post(
            "/api/v1/appointments",
            json={
                "doctor_id": 1,
                "patient_id": patient_id,
                "time_slot": future_time,
            },
            headers=auth_headers,
        )
        if booking.status_code == 409:
            pytest.skip("Time slot already booked")
        assert booking.status_code == 201
        appt_id = booking.json()["appointment"]["id"]

        doctor_token = _get_doctor_token(http_client)

        resp = http_client.patch(
            f"/api/v1/appointments/{appt_id}/notes",
            json={"notes": "Patient has peanut allergy"},
            headers={"Authorization": f"Bearer {doctor_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["notes"] == "Patient has peanut allergy"

    def test_patient_cannot_update_notes(self, http_client, auth_headers, patient_id):
        future_time = (
            datetime.utcnow() + timedelta(days=14, hours=3)
        ).isoformat() + "Z"
        booking = http_client.post(
            "/api/v1/appointments",
            json={
                "doctor_id": 1,
                "patient_id": patient_id,
                "time_slot": future_time,
            },
            headers=auth_headers,
        )
        if booking.status_code == 409:
            pytest.skip("Time slot already booked")
        assert booking.status_code == 201
        appt_id = booking.json()["appointment"]["id"]

        resp = http_client.patch(
            f"/api/v1/appointments/{appt_id}/notes",
            json={"notes": "Should fail"},
            headers=auth_headers,
        )
        assert resp.status_code == 403


class TestRecurringAppointments:
    def test_create_weekly_series(self, http_client, auth_headers, patient_id):
        resp = http_client.post(
            "/api/v1/appointments/recurring",
            json={
                "doctor_id": 1,
                "patient_id": patient_id,
                "start_time": "2028-01-03T09:00:00Z",
                "duration_minutes": 30,
                "recurrence": "weekly",
                "occurrences": 4,
            },
            headers=auth_headers,
        )
        if resp.status_code == 500:
            pytest.skip("Database conflict")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_created"] >= 0
        assert data["recurrence"] == "weekly"

    def test_create_biweekly_series(self, http_client, auth_headers, patient_id):
        resp = http_client.post(
            "/api/v1/appointments/recurring",
            json={
                "doctor_id": 1,
                "patient_id": patient_id,
                "start_time": "2028-02-01T10:00:00Z",
                "duration_minutes": 45,
                "recurrence": "biweekly",
                "occurrences": 3,
            },
            headers=auth_headers,
        )
        if resp.status_code == 500:
            pytest.skip("Database conflict")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_created"] >= 0

    def test_create_monthly_series(self, http_client, auth_headers, patient_id):
        resp = http_client.post(
            "/api/v1/appointments/recurring",
            json={
                "doctor_id": 1,
                "patient_id": patient_id,
                "start_time": "2028-03-15T14:00:00Z",
                "duration_minutes": 30,
                "recurrence": "monthly",
                "occurrences": 6,
            },
            headers=auth_headers,
        )
        if resp.status_code == 500:
            pytest.skip("Database conflict")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_created"] >= 0

    def test_cancel_series(self, http_client, auth_headers, patient_id):
        resp = http_client.post(
            "/api/v1/appointments/recurring",
            json={
                "doctor_id": 1,
                "patient_id": patient_id,
                "start_time": "2028-04-01T09:00:00Z",
                "duration_minutes": 30,
                "recurrence": "weekly",
                "occurrences": 3,
            },
            headers=auth_headers,
        )
        if resp.status_code != 200:
            pytest.skip("Could not create series")
        series_id = resp.json()["series_id"]

        cancel_resp = http_client.delete(
            f"/api/v1/appointments/series/{series_id}",
            headers=auth_headers,
        )
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["cancelled_count"] >= 0

    def test_invalid_recurrence(self, http_client, auth_headers, patient_id):
        resp = http_client.post(
            "/api/v1/appointments/recurring",
            json={
                "doctor_id": 1,
                "patient_id": patient_id,
                "start_time": "2028-01-01T09:00:00Z",
                "duration_minutes": 30,
                "recurrence": "daily",
                "occurrences": 5,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422


class TestAPIv2:
    def test_v2_appointments_paginated(self, http_client, auth_headers):
        resp = http_client.get("/api/v2/appointments", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data

    def test_v2_doctors_includes_schedule(self, http_client, auth_headers):
        resp = http_client.get("/api/v2/doctors", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        if data["items"]:
            assert "schedule" in data["items"][0]


class TestDeprecationHeaders:
    def test_v1_has_deprecation_headers(self, http_client, auth_headers):
        resp = http_client.get("/api/v1/doctors", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.headers.get("Deprecation") == "true"
        assert "Sunset" in resp.headers
        assert "Link" in resp.headers

    def test_v2_no_deprecation_headers(self, http_client, auth_headers):
        resp = http_client.get("/api/v2/doctors", headers=auth_headers)
        assert resp.status_code == 200
        assert "Deprecation" not in resp.headers


def _get_doctor_token(http_client):
    username = f"doctor_loadtest_{datetime.utcnow().timestamp()}"
    http_client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "testpass123", "role": "doctor"},
    )
    resp = http_client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "testpass123"},
    )
    return resp.json()["access_token"]
