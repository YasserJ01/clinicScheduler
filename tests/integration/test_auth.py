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
