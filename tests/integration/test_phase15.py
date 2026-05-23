import uuid
import os
import pathlib
import pytest
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


def _db_conn():
    import psycopg2

    return psycopg2.connect(
        user="clinic",
        password="clinicpass",
        host="localhost",
        port=_get_db_port(),
        database="clinic_db",
    )


class TestCancellationWithReason:
    def test_cancel_with_reason(self, http_client, auth_headers, seeded_doctor_id):
        me = http_client.get("/api/v1/patients/me", headers=auth_headers)
        assert me.status_code == 200
        my_patient_id = me.json()["id"]

        uid = uuid.uuid4().hex[:8]
        hour = int(uid[:2], 16) % 24
        minute = int(uid[2:4], 16) % 60
        day_offset = int(uid[6:8], 16) % 200
        future = datetime.now(timezone.utc) + timedelta(days=300 + day_offset)
        unique_slot = future.strftime(f"%Y-%m-%dT{hour:02d}:{minute:02d}:00Z")

        booking = http_client.post(
            "/api/v1/appointments",
            headers=auth_headers,
            json={
                "doctor_id": seeded_doctor_id,
                "patient_id": my_patient_id,
                "time_slot": unique_slot,
            },
        )
        if booking.status_code == 409:
            pytest.skip("Time slot already booked")
        assert booking.status_code == 201
        appt_id = booking.json()["appointment"]["id"]

        cancel = http_client.patch(
            f"/api/v1/appointments/{appt_id}/status",
            headers=auth_headers,
            json={"status": "cancelled", "cancellation_reason": "Changed my mind"},
        )
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "cancelled"

        conn = _db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT cancellation_reason, cancelled_by, cancelled_at FROM appointments WHERE id = %s",
                    (appt_id,),
                )
                row = cur.fetchone()
                assert row is not None
                reason, cancelled_by, cancelled_at = row
                assert reason == "Changed my mind"
                assert cancelled_by is not None
                assert cancelled_at is not None
        finally:
            conn.close()

    def test_cancel_without_reason(self, http_client, auth_headers, seeded_doctor_id):
        me = http_client.get("/api/v1/patients/me", headers=auth_headers)
        assert me.status_code == 200
        my_patient_id = me.json()["id"]

        uid = uuid.uuid4().hex[:8]
        hour = int(uid[:2], 16) % 24
        minute = int(uid[2:4], 16) % 60
        day_offset = int(uid[6:8], 16) % 200
        future = datetime.now(timezone.utc) + timedelta(days=350 + day_offset)
        unique_slot = future.strftime(f"%Y-%m-%dT{hour:02d}:{minute:02d}:00Z")

        booking = http_client.post(
            "/api/v1/appointments",
            headers=auth_headers,
            json={
                "doctor_id": seeded_doctor_id,
                "patient_id": my_patient_id,
                "time_slot": unique_slot,
            },
        )
        if booking.status_code == 409:
            pytest.skip("Time slot already booked")
        assert booking.status_code == 201
        appt_id = booking.json()["appointment"]["id"]

        cancel = http_client.patch(
            f"/api/v1/appointments/{appt_id}/status",
            headers=auth_headers,
            json={"status": "cancelled"},
        )
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "cancelled"

        conn = _db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT cancellation_reason FROM appointments WHERE id = %s",
                    (appt_id,),
                )
                row = cur.fetchone()
                assert row is not None
                assert row[0] is None
        finally:
            conn.close()

    def test_patient_cancel_other_fails(
        self, http_client, auth_headers, seeded_doctor_id
    ):
        other_username = f"other_pt_{uuid.uuid4().hex[:8]}"
        other_resp = http_client.post(
            "/api/v1/auth/register",
            json={
                "username": other_username,
                "password": "test1234",
                "role": "patient",
                "email": f"{other_username}@test.com",
            },
        )
        assert other_resp.status_code == 200
        other_token = other_resp.json()["access_token"]
        other_headers = {"Authorization": f"Bearer {other_token}"}

        me = http_client.get("/api/v1/patients/me", headers=other_headers)
        assert me.status_code == 200
        other_patient_id = me.json()["id"]

        uid = uuid.uuid4().hex[:8]
        hour = int(uid[:2], 16) % 24
        minute = int(uid[2:4], 16) % 60
        day_offset = int(uid[6:8], 16) % 200
        future = datetime.now(timezone.utc) + timedelta(days=400 + day_offset)
        unique_slot = future.strftime(f"%Y-%m-%dT{hour:02d}:{minute:02d}:00Z")

        booking = http_client.post(
            "/api/v1/appointments",
            headers=other_headers,
            json={
                "doctor_id": seeded_doctor_id,
                "patient_id": other_patient_id,
                "time_slot": unique_slot,
            },
        )
        if booking.status_code == 409:
            pytest.skip("Time slot already booked")
        assert booking.status_code == 201
        appt_id = booking.json()["appointment"]["id"]

        cancel = http_client.patch(
            f"/api/v1/appointments/{appt_id}/status",
            headers=auth_headers,
            json={"status": "cancelled"},
        )
        assert cancel.status_code == 403


class TestBookForMe:
    def test_book_for_me_success(self, http_client, seeded_doctor_id):
        username = f"forme_{uuid.uuid4().hex[:8]}"
        email = f"{username}@test.com"
        register = http_client.post(
            "/api/v1/auth/register",
            json={
                "username": username,
                "password": "test1234",
                "role": "patient",
                "email": email,
            },
        )
        assert register.status_code == 200
        token = register.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        me = http_client.get("/api/v1/patients/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["email"] == email

        uid = uuid.uuid4().hex[:8]
        hour = int(uid[:2], 16) % 24
        minute = int(uid[2:4], 16) % 60
        day_offset = int(uid[6:8], 16) % 200
        future = datetime.now(timezone.utc) + timedelta(days=450 + day_offset)
        unique_slot = future.strftime(f"%Y-%m-%dT{hour:02d}:{minute:02d}:00Z")

        booking = http_client.post(
            "/api/v1/appointments/for-me",
            headers=headers,
            json={
                "doctor_id": seeded_doctor_id,
                "time_slot": unique_slot,
            },
        )
        if booking.status_code == 409:
            pytest.skip("Time slot already booked")
        assert booking.status_code == 201
        data = booking.json()
        assert data["success"] is True
        assert data["appointment"]["patient_id"] == me.json()["id"]

    def test_book_for_me_no_patient_profile(self, http_client, seeded_doctor_id):
        username = f"doctor_forme_{uuid.uuid4().hex[:8]}"
        register = http_client.post(
            "/api/v1/auth/register",
            json={
                "username": username,
                "password": "test1234",
                "role": "doctor",
            },
        )
        assert register.status_code == 200
        token = register.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        uid = uuid.uuid4().hex[:8]
        hour = int(uid[:2], 16) % 24
        minute = int(uid[2:4], 16) % 60
        day_offset = int(uid[6:8], 16) % 200
        future = datetime.now(timezone.utc) + timedelta(days=500 + day_offset)
        unique_slot = future.strftime(f"%Y-%m-%dT{hour:02d}:{minute:02d}:00Z")

        booking = http_client.post(
            "/api/v1/appointments/for-me",
            headers=headers,
            json={
                "doctor_id": seeded_doctor_id,
                "time_slot": unique_slot,
            },
        )
        assert booking.status_code == 400
        assert "patient profile" in booking.json()["detail"].lower()

    def test_book_for_me_unauthenticated(self, http_client, seeded_doctor_id):
        booking = http_client.post(
            "/api/v1/appointments/for-me",
            json={
                "doctor_id": seeded_doctor_id,
                "time_slot": "2028-01-15T10:00:00Z",
            },
        )
        assert booking.status_code in (401, 403)


class TestReminderScheduler:
    def test_next_reminder_at_set_on_booking(
        self, http_client, auth_headers, patient_id, seeded_doctor_id
    ):
        uid = uuid.uuid4().hex[:8]
        hour = int(uid[:2], 16) % 24
        minute = int(uid[2:4], 16) % 60
        day_offset = int(uid[6:8], 16) % 200
        future = datetime.now(timezone.utc) + timedelta(days=550 + day_offset)
        unique_slot = future.strftime(f"%Y-%m-%dT{hour:02d}:{minute:02d}:00Z")

        booking = http_client.post(
            "/api/v1/appointments",
            headers=auth_headers,
            json={
                "doctor_id": seeded_doctor_id,
                "patient_id": patient_id,
                "time_slot": unique_slot,
            },
        )
        if booking.status_code == 409:
            pytest.skip("Time slot already booked")
        assert booking.status_code == 201
        appt_id = booking.json()["appointment"]["id"]

        conn = _db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT next_reminder_at, reminder_sent FROM appointments WHERE id = %s",
                    (appt_id,),
                )
                row = cur.fetchone()
                assert row is not None
                next_reminder_at, reminder_sent = row
                assert next_reminder_at is not None
                assert reminder_sent is False
        finally:
            conn.close()

    def test_due_reminder_query(
        self, http_client, admin_headers, patient_id, seeded_doctor_id
    ):
        uid = uuid.uuid4().hex[:8]
        hour = int(uid[:2], 16) % 24
        minute = int(uid[2:4], 16) % 60
        day_offset = int(uid[6:8], 16) % 200
        future = datetime.now(timezone.utc) + timedelta(days=600 + day_offset, hours=12)
        unique_slot = future.strftime(f"%Y-%m-%dT{hour:02d}:{minute:02d}:00Z")

        booking = http_client.post(
            "/api/v1/appointments",
            headers=admin_headers,
            json={
                "doctor_id": seeded_doctor_id,
                "patient_id": patient_id,
                "time_slot": unique_slot,
            },
        )
        if booking.status_code == 409:
            pytest.skip("Time slot already booked")
        assert booking.status_code == 201
        appt_id = booking.json()["appointment"]["id"]

        past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        conn = _db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE appointments SET next_reminder_at = %s WHERE id = %s",
                    (past, appt_id),
                )
                conn.commit()

            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id FROM appointments
                       WHERE id = %s AND next_reminder_at <= NOW()
                       AND appointment_time > NOW()
                       AND status IN ('scheduled', 'confirmed')
                       AND reminder_sent = FALSE""",
                    (appt_id,),
                )
                row = cur.fetchone()
                assert row is not None
        finally:
            conn.close()

    def test_mark_reminder_sent(
        self, http_client, admin_headers, patient_id, seeded_doctor_id
    ):
        uid = uuid.uuid4().hex[:8]
        hour = int(uid[:2], 16) % 24
        minute = int(uid[2:4], 16) % 60
        day_offset = int(uid[6:8], 16) % 200
        future = datetime.now(timezone.utc) + timedelta(days=650 + day_offset)
        unique_slot = future.strftime(f"%Y-%m-%dT{hour:02d}:{minute:02d}:00Z")

        booking = http_client.post(
            "/api/v1/appointments",
            headers=admin_headers,
            json={
                "doctor_id": seeded_doctor_id,
                "patient_id": patient_id,
                "time_slot": unique_slot,
            },
        )
        if booking.status_code == 409:
            pytest.skip("Time slot already booked")
        assert booking.status_code == 201
        appt_id = booking.json()["appointment"]["id"]

        conn = _db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE appointments SET reminder_sent = TRUE, next_reminder_at = NULL WHERE id = %s",
                    (appt_id,),
                )
                conn.commit()

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT reminder_sent, next_reminder_at FROM appointments WHERE id = %s",
                    (appt_id,),
                )
                row = cur.fetchone()
                assert row is not None
                reminder_sent, next_reminder_at = row
                assert reminder_sent is True
                assert next_reminder_at is None
        finally:
            conn.close()
