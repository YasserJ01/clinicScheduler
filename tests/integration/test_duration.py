import uuid
import httpx
import pytest
from datetime import datetime, timedelta, timezone
from tests.conftest import BASE_URL


class TestDurationBooking:
    """Test appointment booking with duration modelling."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = httpx.Client(base_url=BASE_URL, timeout=10.0)
        self.token = self._register_and_login()
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self._unique_offset = int(uuid.uuid4().hex[:4], 16)

    def teardown_method(self):
        self.client.close()

    def test_book_appointment_with_default_duration(self):
        patient = self._create_patient()
        future = datetime.now(timezone.utc) + timedelta(
            days=50 + self._unique_offset, hours=10
        )
        time_slot = future.strftime("%Y-%m-%dT%H:%M:%SZ")

        resp = self.client.post(
            "/api/v1/appointments",
            json={
                "doctor_id": 1,
                "patient_id": patient["id"],
                "time_slot": time_slot,
            },
            headers=self.headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["appointment"]["duration_minutes"] == 30

    def test_book_appointment_with_custom_duration(self):
        patient = self._create_patient()
        future = datetime.now(timezone.utc) + timedelta(
            days=51 + self._unique_offset, hours=10
        )
        time_slot = future.strftime("%Y-%m-%dT%H:%M:%SZ")

        resp = self.client.post(
            "/api/v1/appointments",
            json={
                "doctor_id": 1,
                "patient_id": patient["id"],
                "time_slot": time_slot,
                "duration_minutes": 60,
            },
            headers=self.headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["appointment"]["duration_minutes"] == 60

    def test_duration_validation_rejects_too_short(self):
        resp = self.client.post(
            "/api/v1/appointments",
            json={
                "doctor_id": 1,
                "patient_id": 1,
                "time_slot": "2028-01-01T10:00:00Z",
                "duration_minutes": 3,
            },
            headers=self.headers,
        )
        assert resp.status_code == 422

    def test_duration_validation_rejects_too_long(self):
        resp = self.client.post(
            "/api/v1/appointments",
            json={
                "doctor_id": 1,
                "patient_id": 1,
                "time_slot": "2028-01-01T10:00:00Z",
                "duration_minutes": 500,
            },
            headers=self.headers,
        )
        assert resp.status_code == 422

    def test_overlapping_duration_conflicts(self):
        patient = self._create_patient()
        base_time = datetime.now(timezone.utc) + timedelta(
            days=52 + self._unique_offset, hours=10
        )

        resp1 = self.client.post(
            "/api/v1/appointments",
            json={
                "doctor_id": 1,
                "patient_id": patient["id"],
                "time_slot": base_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "duration_minutes": 60,
            },
            headers=self.headers,
        )
        assert resp1.status_code == 201

        patient2 = self._create_patient()
        overlap_time = base_time + timedelta(minutes=30)
        resp2 = self.client.post(
            "/api/v1/appointments",
            json={
                "doctor_id": 1,
                "patient_id": patient2["id"],
                "time_slot": overlap_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "duration_minutes": 30,
            },
            headers=self.headers,
        )
        assert resp2.status_code == 409

    def test_non_overlapping_duration_succeeds(self):
        patient = self._create_patient()
        base_time = datetime.now(timezone.utc) + timedelta(
            days=53 + self._unique_offset, hours=10
        )

        resp1 = self.client.post(
            "/api/v1/appointments",
            json={
                "doctor_id": 1,
                "patient_id": patient["id"],
                "time_slot": base_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "duration_minutes": 30,
            },
            headers=self.headers,
        )
        assert resp1.status_code == 201

        patient2 = self._create_patient()
        next_slot = base_time + timedelta(minutes=30)
        resp2 = self.client.post(
            "/api/v1/appointments",
            json={
                "doctor_id": 1,
                "patient_id": patient2["id"],
                "time_slot": next_slot.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "duration_minutes": 30,
            },
            headers=self.headers,
        )
        assert resp2.status_code == 201

    def _create_patient(self):
        name = f"Duration Test {uuid.uuid4().hex[:8]}"
        email = f"{uuid.uuid4().hex[:8]}@duration.com"
        resp = self.client.post(
            "/api/v1/patients",
            json={"name": name, "email": email},
            headers=self.headers,
        )
        assert resp.status_code in (200, 201)
        return resp.json()

    def _register_and_login(self):
        username = f"duration_{uuid.uuid4().hex[:8]}"
        resp = self.client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": "test1234"},
        )
        assert resp.status_code == 200
        return resp.json()["access_token"]


class TestAvailableSlots:
    """Test GET /appointments/available endpoint."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = httpx.Client(base_url=BASE_URL, timeout=10.0)
        self.token = self._register_and_login()
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self._unique_offset = int(uuid.uuid4().hex[:4], 16)

    def teardown_method(self):
        self.client.close()

    def test_available_slots_returns_slots(self):
        future_date = (
            datetime.now(timezone.utc) + timedelta(days=100 + self._unique_offset)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        resp = self.client.get(
            "/api/v1/appointments/available",
            params={"doctor_id": 1, "date": future_date, "duration_minutes": 30},
            headers=self.headers,
        )
        if resp.status_code != 200:
            print(f"Response: {resp.status_code} - {resp.text}")
        assert resp.status_code == 200
        body = resp.json()
        assert "available_slots" in body
        assert isinstance(body["available_slots"], list)
        assert len(body["available_slots"]) > 0

    def test_available_slots_reduces_when_booked(self):
        patient = self._create_patient()
        future_date = datetime.now(timezone.utc) + timedelta(
            days=101 + self._unique_offset, hours=10
        )

        resp_book = self.client.post(
            "/api/v1/appointments",
            json={
                "doctor_id": 1,
                "patient_id": patient["id"],
                "time_slot": future_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "duration_minutes": 60,
            },
            headers=self.headers,
        )
        assert resp_book.status_code == 201

        date_str = future_date.strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = self.client.get(
            "/api/v1/appointments/available",
            params={"doctor_id": 1, "date": date_str, "duration_minutes": 30},
            headers=self.headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        booked_slot = future_date.strftime("%Y-%m-%dT%H:%M:%S")
        assert booked_slot not in body["available_slots"]

    def _create_patient(self):
        name = f"Avail Test {uuid.uuid4().hex[:8]}"
        email = f"{uuid.uuid4().hex[:8]}@avail.com"
        resp = self.client.post(
            "/api/v1/patients",
            json={"name": name, "email": email},
            headers=self.headers,
        )
        assert resp.status_code in (200, 201)
        return resp.json()

    def _register_and_login(self):
        username = f"avail_{uuid.uuid4().hex[:8]}"
        resp = self.client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": "test1234"},
        )
        assert resp.status_code == 200
        return resp.json()["access_token"]
