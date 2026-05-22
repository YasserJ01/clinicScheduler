import os
import pathlib
import pytest
import uuid
from datetime import datetime, timedelta, timezone


_ENV_PATH = pathlib.Path(__file__).resolve().parents[2] / ".env"


def _get_secret_key() -> str:
    key = os.getenv("SECRET_KEY", "")
    if key:
        return key
    if _ENV_PATH.exists():
        for line in _ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line.startswith("SECRET_KEY="):
                return line.split("=", 1)[1]
    return "change-me-in-production"


def _get_db_port() -> int:
    port = os.getenv("TEST_DB_PORT", "")
    if port:
        return int(port)
    db_url = os.getenv("DATABASE_URL", "")
    if db_url:
        import re

        m = re.search(r":(\d+)/", db_url)
        if m:
            return int(m.group(1))
    if _ENV_PATH.exists():
        return 5433
    return 5432


class TestUserPatientLink:
    def test_register_creates_patient(self, http_client):
        username = f"patient_link_{uuid.uuid4().hex[:8]}"
        register_resp = http_client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": "test1234", "role": "patient"},
        )
        assert register_resp.status_code == 200
        token = register_resp.json()["access_token"]

        me_resp = http_client.get(
            "/api/v1/patients/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_resp.status_code == 200
        data = me_resp.json()
        assert data["id"] != 0
        assert data["name"] == username
        assert data["email"] == f"{username}@clinic.com"

    def test_patients_me_returns_real_id(self, http_client):
        username = f"real_id_{uuid.uuid4().hex[:8]}"
        resp = http_client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": "test1234", "role": "patient"},
        )
        assert resp.status_code == 200
        token = resp.json()["access_token"]

        me = http_client.get(
            "/api/v1/patients/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me.status_code == 200
        assert me.json()["id"] != 0

    def test_cancel_uses_fk_not_email(self, http_client, seeded_doctor_id):
        username = f"cancel_fk_{uuid.uuid4().hex[:8]}"
        email = f"{username}@custom-email.com"
        register_resp = http_client.post(
            "/api/v1/auth/register",
            json={
                "username": username,
                "password": "test1234",
                "role": "patient",
                "email": email,
            },
        )
        assert register_resp.status_code == 200
        token = register_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        me = http_client.get("/api/v1/patients/me", headers=headers)
        assert me.status_code == 200
        patient_id = me.json()["id"]
        assert patient_id != 0
        assert me.json()["email"] == email

        future = (datetime.now(timezone.utc) + timedelta(days=100, hours=10)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        booking = http_client.post(
            "/api/v1/appointments",
            json={
                "doctor_id": seeded_doctor_id,
                "patient_id": patient_id,
                "time_slot": future,
            },
            headers=headers,
        )
        if booking.status_code == 409:
            pytest.skip("Time slot already booked")
        assert booking.status_code == 201
        appt_id = booking.json()["appointment"]["id"]

        cancel = http_client.patch(
            f"/api/v1/appointments/{appt_id}/status",
            json={"status": "cancelled"},
            headers=headers,
        )
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "cancelled"


class TestDoctorLink:
    def _db_user_id(self, token: str) -> int | None:
        from jose import jwt

        payload = jwt.decode(
            token,
            _get_secret_key(),
            algorithms=["HS256"],
            options={"verify_exp": False},
        )
        sub = payload.get("sub", "")
        if not sub:
            return None
        import psycopg2

        conn = psycopg2.connect(
            user="clinic",
            password="clinicpass",
            host="localhost",
            port=_get_db_port(),
            database="clinic_db",
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE username = %s", (sub,))
                row = cur.fetchone()
                return row[0] if row else None
        finally:
            conn.close()

    def test_doctor_link_user(self, http_client, admin_headers):
        username = f"doctor_link_{uuid.uuid4().hex[:8]}"
        resp = http_client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": "test1234", "role": "doctor"},
        )
        assert resp.status_code == 200
        token = resp.json()["access_token"]
        user_id = self._db_user_id(token)
        if not user_id:
            pytest.skip("Could not determine user_id")

        link_resp = http_client.patch(
            "/api/v1/admin/doctors/2/link-user",
            json={"user_id": user_id},
            headers=admin_headers,
        )
        assert link_resp.status_code == 200

    def test_link_user_duplicate_fails(self, http_client, admin_headers):
        username = f"dup_doctor_{uuid.uuid4().hex[:8]}"
        resp = http_client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": "test1234", "role": "doctor"},
        )
        assert resp.status_code == 200
        token = resp.json()["access_token"]
        user_id = self._db_user_id(token)
        if not user_id:
            pytest.skip("Could not determine user_id")

        link1 = http_client.patch(
            "/api/v1/admin/doctors/1/link-user",
            json={"user_id": user_id},
            headers=admin_headers,
        )
        assert link1.status_code == 200

        link2 = http_client.patch(
            "/api/v1/admin/doctors/2/link-user",
            json={"user_id": user_id},
            headers=admin_headers,
        )
        assert link2.status_code == 409
        assert "already linked" in link2.json()["detail"].lower()

    def test_link_user_requires_admin(self, http_client, auth_headers):
        link_resp = http_client.patch(
            "/api/v1/admin/doctors/1/link-user",
            json={"user_id": 1},
            headers=auth_headers,
        )
        assert link_resp.status_code == 403
