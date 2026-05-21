import pytest
import httpx
import uuid


def _unique_sec():
    return str(int(uuid.uuid4().hex[:2], 16) % 60).zfill(2)


class TestTimezoneHandling:
    def test_book_with_z_suffix(self, http_client, auth_headers, patient_id, seeded_doctor_id):
        sec = _unique_sec()
        resp = http_client.post(
            "/api/v1/appointments",
            headers=auth_headers,
            json={"doctor_id": seeded_doctor_id, "patient_id": patient_id, "time_slot": f"2027-05-01T08:30:{sec}Z"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["success"] is True

    def test_book_with_utc_offset(self, http_client, auth_headers, patient_id, seeded_doctor_id):
        sec = _unique_sec()
        resp = http_client.post(
            "/api/v1/appointments",
            headers=auth_headers,
            json={"doctor_id": seeded_doctor_id, "patient_id": patient_id, "time_slot": f"2027-05-01T09:30:{sec}+00:00"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["success"] is True

    def test_book_with_naive_datetime(self, http_client, auth_headers, patient_id, seeded_doctor_id):
        sec = _unique_sec()
        resp = http_client.post(
            "/api/v1/appointments",
            headers=auth_headers,
            json={"doctor_id": seeded_doctor_id, "patient_id": patient_id, "time_slot": f"2027-05-01T10:30:{sec}"},
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
