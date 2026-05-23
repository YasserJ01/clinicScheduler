import uuid


class TestRegister:
    def test_register_new_user_returns_jwt(self, http_client):
        username = f"reg_test_{uuid.uuid4().hex[:8]}"
        resp = http_client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": "test1234"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert len(data["access_token"]) > 0

    def test_register_duplicate_username_rejected(self, http_client):
        username = f"dup_test_{uuid.uuid4().hex[:8]}"
        resp1 = http_client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": "test1234"},
        )
        assert resp1.status_code == 200

        resp2 = http_client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": "different_pass"},
        )
        assert resp2.status_code == 400
        assert "already exists" in resp2.json()["detail"]

    def test_register_with_role_returns_jwt(self, http_client):
        username = f"role_test_{uuid.uuid4().hex[:8]}"
        resp = http_client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": "test1234", "role": "admin"},
        )
        assert resp.status_code == 200
        assert "access_token" in resp.json()


class TestLogin:
    def test_login_correct_credentials(self, http_client):
        username = f"login_test_{uuid.uuid4().hex[:8]}"
        password = "secure_pass_123"
        http_client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": password},
        )
        resp = http_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    def test_login_wrong_password(self, http_client):
        username = f"wrong_pass_{uuid.uuid4().hex[:8]}"
        http_client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": "correct_password"},
        )
        resp = http_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "wrong_password"},
        )
        assert resp.status_code == 401
        assert "Invalid credentials" in resp.json()["detail"]

    def test_login_nonexistent_user(self, http_client):
        resp = http_client.post(
            "/api/v1/auth/login",
            json={"username": "nonexistent_user_xyz", "password": "any"},
        )
        assert resp.status_code == 401


class TestJWTValidation:
    def test_protected_endpoint_with_valid_token(self, http_client, user_token):
        resp = http_client.get(
            "/api/v1/doctors",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 200

    def test_protected_endpoint_without_token(self, http_client):
        resp = http_client.get("/api/v1/doctors")
        assert resp.status_code in (401, 403)

    def test_protected_endpoint_with_invalid_token(self, http_client):
        resp = http_client.get(
            "/api/v1/doctors",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert resp.status_code in (401, 403)

    def test_protected_endpoint_with_expired_token(self, http_client):
        from app.core.security import create_access_token
        from datetime import timedelta

        expired_token = create_access_token(
            subject="expired_user", expires_delta=timedelta(seconds=-10)
        )
        resp = http_client.get(
            "/api/v1/doctors",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert resp.status_code in (401, 403)


class TestAccountLockout:
    def test_locks_after_five_failed_attempts(self, http_client):
        username = f"lockout_{uuid.uuid4().hex[:8]}"
        http_client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": "correct_password"},
        )

        for _ in range(5):
            resp = http_client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": "wrong_password"},
            )
            assert resp.status_code == 401

        resp = http_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "wrong_password"},
        )
        assert resp.status_code == 429
        assert "locked" in resp.json()["detail"].lower()

    def test_successful_login_resets_failed_attempts(self, http_client):
        username = f"reset_lock_{uuid.uuid4().hex[:8]}"
        http_client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": "correct_password"},
        )

        for _ in range(4):
            resp = http_client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": "wrong_password"},
            )
            assert resp.status_code == 401

        resp = http_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "correct_password"},
        )
        assert resp.status_code == 200
        assert "access_token" in resp.json()

        resp = http_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "wrong_password"},
        )
        assert resp.status_code == 401

    def test_lockout_does_not_affect_other_users(self, http_client):
        locked_user = f"locked_user_{uuid.uuid4().hex[:8]}"
        other_user = f"other_user_{uuid.uuid4().hex[:8]}"

        http_client.post(
            "/api/v1/auth/register",
            json={"username": locked_user, "password": "correct_password"},
        )
        http_client.post(
            "/api/v1/auth/register",
            json={"username": other_user, "password": "other_password"},
        )

        for _ in range(6):
            http_client.post(
                "/api/v1/auth/login",
                json={"username": locked_user, "password": "wrong_password"},
            )

        resp = http_client.post(
            "/api/v1/auth/login",
            json={"username": other_user, "password": "other_password"},
        )
        assert resp.status_code == 200
        assert "access_token" in resp.json()


class TestPasswordReset:
    def test_forgot_password_always_returns_200(self, http_client):
        resp = http_client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "nonexistent@test.com"},
        )
        assert resp.status_code == 200
        assert "reset" in resp.json()["message"].lower()

    def test_reset_password_full_flow(self, http_client):
        import os
        import pathlib

        from app.core.security import create_password_reset_token

        username = f"reset_flow_{uuid.uuid4().hex[:8]}"
        email = f"{username}@test.com"
        register = http_client.post(
            "/api/v1/auth/register",
            json={
                "username": username,
                "password": "old_password",
                "role": "patient",
                "email": email,
            },
        )
        assert register.status_code == 200

        patient_resp = http_client.get(
            "/api/v1/patients/me",
            headers={"Authorization": f"Bearer {register.json()['access_token']}"},
        )
        assert patient_resp.status_code == 200
        patient_id = patient_resp.json()["id"]

        _env_path = pathlib.Path(__file__).resolve().parents[2] / ".env"

        def _get_db_port():
            port = os.getenv("TEST_DB_PORT", "")
            if port:
                return int(port)
            db_url = os.getenv("DATABASE_URL", "")
            if db_url:
                import re

                m = re.search(r":(\d+)/", db_url)
                if m:
                    return int(m.group(1))
            if _env_path.exists():
                return 5433
            return 5432

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
                cur.execute(
                    "SELECT id FROM patients WHERE id = %s",
                    (patient_id,),
                )
                row = cur.fetchone()
                assert row is not None
                patient_db_id = row[0]

                cur.execute(
                    "SELECT user_id FROM patients WHERE id = %s",
                    (patient_db_id,),
                )
                user_id = cur.fetchone()[0]
                assert user_id is not None

                full_token, jti, token_hash = create_password_reset_token()
                from datetime import datetime, timedelta, timezone

                cur.execute(
                    """UPDATE users
                       SET password_reset_jti = %s,
                           password_reset_hash = %s,
                           password_reset_expires_at = %s
                       WHERE id = %s""",
                    (
                        jti,
                        token_hash,
                        datetime.now(timezone.utc).replace(tzinfo=None)
                        + timedelta(minutes=15),
                        user_id,
                    ),
                )
                conn.commit()
        finally:
            conn.close()

        reset = http_client.post(
            "/api/v1/auth/reset-password",
            json={"token": full_token, "new_password": "new_password"},
        )
        assert reset.status_code == 200
        assert "reset" in reset.json()["message"].lower()

        login = http_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "new_password"},
        )
        assert login.status_code == 200
        assert "access_token" in login.json()

        old_login = http_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "old_password"},
        )
        assert old_login.status_code == 401

    def test_reset_password_invalid_token(self, http_client):
        resp = http_client.post(
            "/api/v1/auth/reset-password",
            json={"token": "invalid.token.here", "new_password": "test1234"},
        )
        assert resp.status_code == 400

    def test_reset_password_bad_format(self, http_client):
        resp = http_client.post(
            "/api/v1/auth/reset-password",
            json={"token": "no_dot_separator", "new_password": "test1234"},
        )
        assert resp.status_code == 400
