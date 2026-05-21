import pytest
import httpx
import uuid


def _unique_time():
    uid = uuid.uuid4().hex[:8]
    hour = int(uid[:2], 16) % 24
    minute = int(uid[2:4], 16) % 60
    second = int(uid[4:6], 16) % 60
    return f"2027-05-01T{hour:02d}:{minute:02d}:{second:02d}Z"


class TestTimezoneHandling:
    def test_book_with_z_suffix(self, http_client, auth_headers, patient_id, seeded_doctor_id):
        time_slot = _unique_time()
        resp = http_client.post(
            "/api/v1/appointments",
            headers=auth_headers,
            json={"doctor_id": seeded_doctor_id, "patient_id": patient_id, "time_slot": time_slot},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["success"] is True

    def test_book_with_utc_offset(self, http_client, auth_headers, patient_id, seeded_doctor_id):
        base = _unique_time().rstrip("Z")
        time_slot = f"{base}+00:00"
        resp = http_client.post(
            "/api/v1/appointments",
            headers=auth_headers,
            json={"doctor_id": seeded_doctor_id, "patient_id": patient_id, "time_slot": time_slot},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["success"] is True

    def test_book_with_naive_datetime(self, http_client, auth_headers, patient_id, seeded_doctor_id):
        time_slot = _unique_time().rstrip("Z")
        resp = http_client.post(
            "/api/v1/appointments",
            headers=auth_headers,
            json={"doctor_id": seeded_doctor_id, "patient_id": patient_id, "time_slot": time_slot},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["success"] is True

    def test_book_with_invalid_timeslot(self, http_client, auth_headers, patient_id, seeded_doctor_id):
        resp = http_client.post(
            "/api/v1/appointments",
            headers=auth_headers,
            json={"doctor_id": seeded_doctor_id, "patient_id": patient_id, "time_slot": "not-a-date"},
        )
        assert resp.status_code == 422

    def test_book_with_empty_timeslot(self, http_client, auth_headers, patient_id, seeded_doctor_id):
        resp = http_client.post(
            "/api/v1/appointments",
            headers=auth_headers,
            json={"doctor_id": seeded_doctor_id, "patient_id": patient_id, "time_slot": ""},
        )
        assert resp.status_code == 422
