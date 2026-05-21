import pytest
import httpx
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone


class TestConcurrentBooking:
    def test_concurrent_same_slot_one_succeeds(self, auth_headers, patient_id, seeded_doctor_id):
        """Two simultaneous requests for the same slot: one gets 201, one gets 409."""
        uid = uuid.uuid4().hex[:8]
        hour = int(uid[:2], 16) % 24
        minute = int(uid[2:4], 16) % 60
        second = int(uid[4:6], 16) % 60
        day_offset = int(uid[6:8], 16) % 200
        future_date = datetime.now(timezone.utc) + timedelta(days=150 + day_offset)
        concurrent_slot = future_date.strftime(f"%Y-%m-%dT{hour:02d}:{minute:02d}:{second:02d}Z")
        payload = {
            "doctor_id": seeded_doctor_id,
            "patient_id": patient_id,
            "time_slot": concurrent_slot,
        }

        results = []
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = []
            for _ in range(2):
                futures.append(executor.submit(
                    self._book_appointment, payload, auth_headers
                ))
            for future in as_completed(futures):
                results.append(future.result())

        status_codes = sorted([r["status_code"] for r in results])
        assert status_codes == [201, 409], f"Expected [201, 409], got {status_codes}"

        success_resp = next(r for r in results if r["status_code"] == 201)
        conflict_resp = next(r for r in results if r["status_code"] == 409)
        assert success_resp["success"] is True
        assert conflict_resp["success"] is False
        assert "already occupied" in conflict_resp["error"]

        list_resp = httpx.Client(base_url="http://localhost", timeout=10.0).get(
            "/api/v1/appointments",
            headers=auth_headers,
        )
        assert list_resp.status_code == 200
        appointments = list_resp.json()
        matching = [
            a for a in appointments
            if a["time_slot"].startswith(concurrent_slot.rstrip("Z"))
            and a["doctor_id"] == seeded_doctor_id
        ]
        assert len(matching) == 1, f"Expected exactly 1 appointment, found {len(matching)}"

    @staticmethod
    def _book_appointment(payload, headers):
        client = httpx.Client(base_url="http://localhost", timeout=10.0)
        resp = client.post("/api/v1/appointments", headers=headers, json=payload)
        return {
            "status_code": resp.status_code,
            "success": resp.json().get("success"),
            "error": resp.json().get("error"),
        }
