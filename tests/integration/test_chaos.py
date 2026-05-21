import pytest
import httpx


class TestChaosBackdoor:
    def test_chaos_trigger_returns_503(self, http_client, auth_headers, seeded_doctor_id):
        resp = http_client.post(
            "/api/v1/appointments",
            headers=auth_headers,
            json={"doctor_id": seeded_doctor_id, "patient_id": 999, "time_slot": "2027-06-01T10:00:00Z"},
        )
        assert resp.status_code == 503
        data = resp.json()
        assert "CHAOS" in data["detail"]

    def test_chaos_trigger_with_string_patient_id(self, http_client, auth_headers, seeded_doctor_id):
        resp = http_client.post(
            "/api/v1/appointments",
            headers=auth_headers,
            json={"doctor_id": seeded_doctor_id, "patient_id": "999", "time_slot": "2027-06-01T11:00:00Z"},
        )
        assert resp.status_code == 503
        data = resp.json()
        assert "CHAOS" in data["detail"]
