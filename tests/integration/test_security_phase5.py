import uuid
import httpx
import pytest
from tests.conftest import BASE_URL


class TestSQLInjection:
    """Verify that SQL injection attempts are rejected safely."""

    @pytest.fixture(autouse=True)
    def setup_client(self):
        self.client = httpx.Client(base_url=BASE_URL, timeout=10.0)

    def teardown_method(self):
        self.client.close()

    def test_sql_injection_in_login_username(self):
        resp = self.client.post(
            "/api/v1/auth/login",
            json={"username": "admin' OR '1'='1", "password": "anything"},
        )
        assert resp.status_code == 401
        assert "Invalid credentials" in resp.json().get("detail", "")

    def test_sql_injection_in_login_password(self):
        resp = self.client.post(
            "/api/v1/auth/login",
            json={"username": "nonexistent", "password": "' OR '1'='1"},
        )
        assert resp.status_code == 401

    def test_sql_injection_in_register_username(self):
        resp = self.client.post(
            "/api/v1/auth/register",
            json={"username": "'; DROP TABLE users; --", "password": "test1234"},
        )
        assert resp.status_code in (200, 400, 422)

    def test_sql_injection_in_appointment_booking(self):
        token = self._register_and_login()
        resp = self.client.post(
            "/api/v1/appointments",
            json={
                "doctor_id": "1; DROP TABLE appointments; --",
                "patient_id": 1,
                "time_slot": "2027-01-01T10:00:00Z",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code in (400, 422)

    def test_sql_injection_in_patient_name(self):
        token = self._register_and_login()
        resp = self.client.post(
            "/api/v1/patients",
            json={
                "name": "'; DROP TABLE patients; --",
                "email": f"{uuid.uuid4().hex[:8]}@test.com",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code in (200, 201, 422)

    def _register_and_login(self):
        username = f"sec_test_{uuid.uuid4().hex[:8]}"
        resp = self.client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": "test1234"},
        )
        assert resp.status_code == 200
        return resp.json()["access_token"]


class TestPasswordPolicy:
    """Verify password length policy (72-byte bcrypt limit)."""

    @pytest.fixture(autouse=True)
    def setup_client(self):
        self.client = httpx.Client(base_url=BASE_URL, timeout=10.0)

    def teardown_method(self):
        self.client.close()

    def test_password_exactly_72_bytes_accepted(self):
        password = "a" * 72
        resp = self.client.post(
            "/api/v1/auth/register",
            json={"username": f"pwd72_{uuid.uuid4().hex[:8]}", "password": password},
        )
        assert resp.status_code == 200

    def test_password_73_bytes_rejected(self):
        password = "a" * 73
        resp = self.client.post(
            "/api/v1/auth/register",
            json={"username": f"pwd73_{uuid.uuid4().hex[:8]}", "password": password},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert any("72" in str(d) for d in body.get("detail", []))

    def test_password_unicode_bytes_counted(self):
        password = "\u00e9" * 25
        byte_len = len(password.encode("utf-8"))
        assert byte_len == 50
        resp = self.client.post(
            "/api/v1/auth/register",
            json={"username": f"uni50_{uuid.uuid4().hex[:8]}", "password": password},
        )
        assert resp.status_code == 200

    def test_password_unicode_over_72_bytes_rejected(self):
        password = "\u00e9" * 37
        byte_len = len(password.encode("utf-8"))
        assert byte_len == 74
        resp = self.client.post(
            "/api/v1/auth/register",
            json={"username": f"uni74_{uuid.uuid4().hex[:8]}", "password": password},
        )
        assert resp.status_code == 422


class TestAlgNoneAttack:
    """Integration test: alg: none JWT is rejected on protected endpoints."""

    @pytest.fixture(autouse=True)
    def setup_client(self):
        self.client = httpx.Client(base_url=BASE_URL, timeout=10.0)

    def teardown_method(self):
        self.client.close()

    def test_alg_none_token_rejected_on_protected_endpoint(self):
        import base64
        import json

        header = (
            base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode())
            .rstrip(b"=")
            .decode()
        )
        payload = (
            base64.urlsafe_b64encode(
                json.dumps({"sub": "hacker", "role": "admin"}).encode()
            )
            .rstrip(b"=")
            .decode()
        )
        fake_token = f"{header}.{payload}."

        resp = self.client.get(
            "/api/v1/doctors",
            headers={"Authorization": f"Bearer {fake_token}"},
        )
        assert resp.status_code == 401
